# A10 · Activación de RLS backend-only (preparada, NO ejecutada)

**Estado:** mitigación **preparada y probada localmente**. No se aplicó a Supabase, a la base de producción ni a ningún clúster remoto. A10 sigue **por verificar en vivo** hasta una auditoría remota autorizada.

**Decisión de arquitectura:** la aplicación es un backend Flask con conexión PostgreSQL directa. El JWT de Flask **no** es una identidad de Supabase Auth. No existe un mapeo verificado `users.id ↔ auth.uid()`. Por tanto:

- No se usan políticas `auth.uid()` ni se inventa un propietario de fila.
- No se cambia la aplicación para depender de Supabase Auth.
- No se expone `users` ni `users.password` a `anon`/`authenticated`.
- No se concede `service_role` a clientes; la service key sigue siendo server-only.

La activación solo es válida si se confirma que **ningún cliente** (frontend, móvil, BI) usa el Data API de Supabase para leer o escribir estas tablas. Si algún cliente lo hace, esta migración lo bloqueará y se requiere una migración coordinada, no una relajación de RLS.

## Archivos del lote

| Archivo | Propósito |
|---|---|
| `migrations/manual/20261022_backend_only_rls.sql` | Hardening SQL revisable e idempotente. |
| `tests/integration/test_rls_backend_only_postgres.py` | Regresiones estáticas y PostgreSQL local opt-in. |
| `docs/RLS_ACTIVACION.md` | Este plan de preflight, rollout, drift y rollback. |
| `docs/oc-notas-RLS.md` | Registro real de ejecución y límites del lote. |

No se modificaron `app.py`, `models.py`, `extensions.py`, recursos, servicios ni `.env` reales.

## Qué hace la migración

1. Crea el rol de grupo `manngo_backend` con `NOLOGIN`, sin contraseña y sin atributos elevados. No crea cuentas de login.
2. Revoca `USAGE`/`CREATE` sobre el esquema `public` a `PUBLIC`, `anon` y `authenticated`.
3. Revoca privilegios sobre tablas, vistas, secuencias y rutinas de `public` a `PUBLIC`, `anon` y `authenticated`.
4. Habilita RLS en **todas** las tablas base de `public` y elimina **todas** las políticas existentes. Deja una única política explícita `manngo_backend_all` para `manngo_backend`.
5. Otorga al backend únicamente `SELECT/INSERT/UPDATE/DELETE` sobre tablas, uso de secuencias y ejecución de rutinas. No otorga DDL.
6. Revoca membresías peligrosas: `manngo_backend` de `anon`, `authenticated` y `service_role`; y `service_role` de `anon`, `authenticated` y `manngo_backend` (para que el backend no pueda escalar a `service_role`/BYPASSRLS). `PUBLIC` no es un rol de grupo para membresías, por lo que **no** se usa `REVOKE <rol> FROM PUBLIC`; solo se revocan privilegios `FROM PUBLIC`.
7. Fija default privileges para objetos futuros creados por el rol que ejecuta la migración.
8. Ejecuta postcondiciones dentro de la misma transacción: si `anon`/`authenticated`/`service_role` pueden hacer `SET ROLE manngo_backend` (directa o indirectamente), si `anon`/`authenticated`/`manngo_backend` pueden hacer `SET ROLE service_role`, si los clientes conservan acceso, si falta RLS o si una política apunta a un rol cliente, la migración falla y hace rollback. La detección usa `pg_has_role(..., 'MEMBER')`, que incluye membresías indirectas; en ese caso exige revisión humana y no se declara seguro.

`users` no se mueve a otro esquema porque eso requeriría cambiar `models.py`/`app.py`, fuera de este lote. La contención se logra denegando acceso directo a los roles cliente y dejando el acceso solo al backend explícito.

## Lo que la migración NO hace

- No se aplicó en producción, staging ni Supabase remoto.
- No crea usuarios, logins, contraseñas ni grants de `service_role`.
- No intenta `REVOKE service_role FROM PUBLIC` (inválido en PostgreSQL: `PUBLIC` no es un rol de grupo para membresías). Solo revoca privilegios `FROM PUBLIC`.
- No modifica datos de negocio, no borra filas y no cambia constraints.
- No reemplaza la autenticación Flask/JWT ni introduce Supabase Auth.
- No afirma que el estado remoto actual ya sea seguro. A10 sigue pendiente de verificación.
- No autoriza por sí sola la activación: requiere revisión humana, ventana y snapshot.

## Preflight obligatorio (solo lectura, humano autorizado)

Antes de aplicar, revisar en el proyecto correcto y sin abrir conexiones desde la aplicación:

1. Confirmar que el backend es el único consumidor directo de PostgreSQL para estas tablas.
2. Revisar si el Data API expone `public` y qué tablas/views están publicadas.
3. Revisar `pg_roles` para `anon`, `authenticated`, `service_role` y el login real de Flask.
4. Revisar políticas actuales y grants amplios, incluida `users`.
5. Revisar dueños de tablas/vistas y si el login de Flask es miembro de `manngo_backend` o propietario.
6. Tomar snapshot/backup verificado y definir ventana de reversión.
7. Ejecutar primero en una base local desechable `manngo_test*`, nunca en producción.

Consultas de preflight sugeridas:

```sql
-- Roles y atributos (revisar, no modificar)
SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
FROM pg_roles
WHERE rolname IN ('anon', 'authenticated', 'service_role', 'manngo_backend');

-- Membresías actuales que permitirían SET ROLE
SELECT granted_role.rolname AS granted_role, member_role.rolname AS member_role
FROM pg_auth_members m
JOIN pg_roles granted_role ON granted_role.oid = m.roleid
JOIN pg_roles member_role ON member_role.oid = m.member
WHERE granted_role.rolname IN ('manngo_backend', 'service_role')
   OR member_role.rolname IN ('anon', 'authenticated')
ORDER BY 1, 2;

-- Políticas actuales en public
SELECT schemaname, tablename, policyname, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

-- Grants directos a roles cliente (tablas y vistas)
SELECT c.oid::regclass AS object, r.role_name, p.privilege_type
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN (VALUES ('anon'), ('authenticated')) AS r(role_name)
CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS p
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND (p.grantee = 0 OR pg_get_userbyid(p.grantee) = r.role_name)
ORDER BY 1, 2, 3;

-- Configuración del Data API / exposición (consultar donde corresponda)
-- No ejecutar desde esta aplicación ni desde un cliente no autorizado.
```

## Rollout propuesto (NO ejecutado)

1. Snapshot/backup y ventana aprobados por el responsable.
2. Ejecutar el preflight y guardar evidencia (sin datos personales ni credenciales).
3. Resolver el login real de Flask: debe ser miembro del grupo `manngo_backend`:
   ```sql
   -- Ejemplo; el operador sustituye <flask_login>. No crea contraseñas.
   GRANT manngo_backend TO <flask_login>;
   ```
   No se incluye este grant en la migración porque el nombre del login real no se conoce ni se debe inventar.
4. Aplicar `migrations/manual/20261022_backend_only_rls.sql` como owner/superuser con `ON_ERROR_STOP=1` (por ejemplo `psql -v ON_ERROR_STOP=1 -f ...`) para que un error aborte visiblemente.
5. Verificar las postcondiciones (incluido que `anon`/`authenticated`/`service_role` no puedan hacer `SET ROLE manngo_backend`) y ejecutar las consultas de drift.
6. Smoke test del backend Flask (login, ventas, stock, reportes) y confirmar que el Data API queda fuera de uso.
7. Si el backend falla por permisos, corregir la membresía/grants del login, no reabrir `anon`/`authenticated`.
8. Si la postcondición detecta una membresía indirecta hacia `manngo_backend` o hacia `service_role`, **no forzar el commit**: revisar la cadena de roles y decidir con el responsable.
9. Registrar el resultado real y mantener A10 como “por verificar” hasta la auditoría remota.

## Rollback

El rollback recomendado es **restaurar el snapshot** tomado antes del rollout. No se entrega un rollback automático que vuelva a otorgar acceso amplio a `anon`/`authenticated`: eso reabriría A10.

Si se necesita retirar únicamente los objetos de esta mitigación (por ejemplo, por un problema de política), el siguiente SQL es un rollback parcial revisable. **No restaura grants a roles cliente ni membresías revocadas**; restaurarlas reabriría `SET ROLE`:

```sql
BEGIN;

DO $$
DECLARE
    relation_row record;
    policy_row record;
BEGIN
    FOR relation_row IN
        SELECT n.nspname, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
    LOOP
        FOR policy_row IN
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = relation_row.nspname
              AND tablename = relation_row.relname
              AND policyname = 'manngo_backend_all'
        LOOP
            EXECUTE format(
                'DROP POLICY IF EXISTS %I ON %I.%I',
                policy_row.policyname,
                relation_row.nspname,
                relation_row.relname
            );
        END LOOP;

        EXECUTE format(
            'ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
            relation_row.nspname,
            relation_row.relname
        );
    END LOOP;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM manngo_backend;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM manngo_backend;
REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM manngo_backend;
REVOKE USAGE ON SCHEMA public FROM manngo_backend;

COMMIT;
```

Después de este rollback parcial, `anon`/`authenticated` siguen sin grants directos. Si el objetivo es volver exactamente al estado anterior, usar el snapshot y asumir explícitamente que A10 vuelve a estar expuesto.

## Detección de drift (ejecutar después del rollout y tras cambios de esquema)

```sql
-- 1. Tablas base sin RLS
SELECT c.oid::regclass AS table_without_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
  AND NOT c.relrowsecurity;

-- 2. Políticas que apuntan a roles cliente
SELECT schemaname, tablename, policyname, roles
FROM pg_policies
WHERE schemaname = 'public'
  AND (
      'anon' = ANY (roles)
      OR 'authenticated' = ANY (roles)
      OR 'public' = ANY (roles)
  );

-- 3. Privilegios de tablas/vistas para roles cliente
SELECT c.oid::regclass AS object, r.role_name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN (VALUES ('anon'), ('authenticated')) AS r(role_name)
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND has_table_privilege(
      r.role_name::name,
      c.oid,
      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
  );

-- 4. Privilegios de secuencias para roles cliente
SELECT c.oid::regclass AS sequence_object, r.role_name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN (VALUES ('anon'), ('authenticated')) AS r(role_name)
WHERE n.nspname = 'public'
  AND c.relkind = 'S'
  AND has_sequence_privilege(r.role_name::name, c.oid, 'USAGE,SELECT,UPDATE');

-- 5. USAGE/CREATE del esquema público para roles cliente
SELECT r.role_name,
       has_schema_privilege(r.role_name::name, 'public', 'USAGE') AS has_usage,
       has_schema_privilege(r.role_name::name, 'public', 'CREATE') AS has_create
FROM (VALUES ('anon'), ('authenticated')) AS r(role_name);

-- 6. Atributos del backend y de los roles de Supabase
SELECT rolname, rolcanlogin, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('manngo_backend', 'anon', 'authenticated', 'service_role');

-- 7. Ningún rol cliente debe poder SET ROLE a backend/service_role
--    (pg_has_role con MEMBER incluye membresías indirectas)
SELECT r.role_name,
       CASE WHEN r.role_name IN ('anon', 'authenticated', 'service_role')
            THEN pg_has_role(r.role_name::name, 'manngo_backend', 'MEMBER')
            ELSE NULL
       END AS can_set_backend,
       CASE WHEN r.role_name IN ('anon', 'authenticated', 'manngo_backend')
            THEN pg_has_role(r.role_name::name, 'service_role', 'MEMBER')
            ELSE NULL
       END AS can_set_service_role
FROM (VALUES ('anon'), ('authenticated'), ('service_role'), ('manngo_backend'))
     AS r(role_name);

-- 8. Default privileges sospechosos en public
SELECT defaclrole::regrole AS owner_role,
       defaclnamespace::regnamespace AS schema_name,
       defaclobjtype,
       defaclacl
FROM pg_default_acl
WHERE defaclnamespace = 'public'::regnamespace;
```

Es drift cualquier fila en las consultas 1–5, atributos elevados en la 6, un `true` en `can_set_backend`/`can_set_service_role` de la 7 o ACLs inesperadas en la 8. Requiere revisión. Volver a ejecutar la migración después de crear tablas nuevas es obligatorio para que esas tablas también tengan RLS y política backend; los default privileges solo cubren grants, no habilitan RLS automáticamente.

## Pruebas locales

Las comprobaciones estáticas del SQL se ejecutan siempre con el runner offline. El bloque PostgreSQL es opt-in y crea bases desechables `manngo_test_rls_*` en un PostgreSQL **local**; nunca abre una URL remota.

```powershell
# Solo en una máquina con PostgreSQL local desechable.
$env:MANNGO_RUN_PG_TESTS = "1"
$env:MANNGO_LOCAL_POSTGRES_URL = "postgresql://usuario_local@127.0.0.1:5432/manngo_test_rls_base"
$env:DATABASE_URL = $env:MANNGO_LOCAL_POSTGRES_URL
.\venv\Scripts\python.exe scripts/run_tests_offline.py `
  --allow-local-postgres --no-bytecode --no-cache --disable-warnings -q `
  tests/integration/test_rls_backend_only_postgres.py
```

Requisitos del rol local de prueba: ser superuser del clúster local (recomendado) o tener membresía con `SET` en los roles de prueba, además de poder crear bases de datos y roles NOLOGIN. `CREATEROLE` por sí solo no permite `SET ROLE`. El test rechaza cualquier URL que no sea loopback literal con base `manngo_test*`. No usa `.env`; las variables son de la sesión de shell.

La URL puede escribirse como `postgresql://` o `postgresql+psycopg2://`: se valida antes de conectar y el driver se normaliza a `postgresql://` solo al serializar el DSN para `psycopg2`. No se elimina ninguna guarda ni se acepta una base que no empiece con `manngo_test`. La base existente indicada (por ejemplo `manngo_test_audit`) se usa como mantenimiento para crear bases temporales `manngo_test_rls_*`; no hay fallback a la base `postgres`.

Las pruebas pre-sembran membresías directas e indirectas (`anon`/`authenticated`/`service_role` → `manngo_backend`, y `anon`/`authenticated`/`manngo_backend` → `service_role`). Verifican que la migración revoca las directas, que `SET ROLE` queda denegado en ambas direcciones y que una cadena indirecta hace abortar la migración con rollback, no que se declare segura. Las membresías peligrosas se revocan al terminar cada bloque.

Ejecutar **solo** ese archivo cuando `DATABASE_URL` apunte a PostgreSQL. No lanzar la suite completa con `DATABASE_URL` PostgreSQL: otros tests están escritos para SQLite y podrían operar sobre la base indicada.

La suite sin autorización PostgreSQL ejecuta las pruebas estáticas y omite únicamente el bloque que necesita un servidor real. No se usa `skip`/`xfail` para ocultar un fallo: si el gate está activo pero la URL es insegura o falta configuración, la prueba **falla**, no se omite.

## Límites y estado de A10

- Lo probado localmente demuestra el comportamiento de PostgreSQL con roles simulados, no el estado real de Supabase.
- No se ejecutó SQL remoto, MCP, Storage, Gemini, Telegram ni SUNAT.
- No se leyó ni editó ningún `.env` real.
- La afirmación “producción está protegida” **no** está hecha. A10 permanece “por verificar en vivo” hasta que un humano autorizado ejecute el preflight y el rollout.
- Recordatorio: el usuario debe rotar/cambiar sus secretos reales fuera de este lote.
