# Notas de implementación — Lote RLS/A10 (backend-only)

**Estado:** mitigación **preparada y versionada**; la última ejecución estática completa fue previa a la corrección de membresías y **no se reejecutó después** por falta de herramienta de ejecución en esta sesión. El bloque PostgreSQL opt-in **no se ejecutó localmente** porque no se suministró un PostgreSQL desechable ni la autorización de loopback; el padre lo corrió en Docker y reportó los errores que motivaron la corrección. **No se aplicó a producción, Supabase remoto ni ningún clúster real. A10 sigue por verificar en vivo.**

**Alcance respetado:** solo el lote A10/RLS backend-only. No se editaron `models.py`, `app.py`, `extensions.py`, recursos, servicios, migraciones Alembic ni `.env` reales. No se ejecutó MCP, SQL remoto, Supabase, Storage, Gemini, Telegram ni SUNAT. No se leyeron credenciales ni datos personales; la semilla de tests es sintética y el diseño de `database/db.sql` se revisó solo para estructura. No se tocaron notas de otros lotes ni tests previos.

## Archivos creados

- `migrations/manual/20261022_backend_only_rls.sql`: migración manual idempotente con transacción, revokes, RLS, política backend, default privileges y postcondiciones que abortan ante drift.
- `tests/integration/test_rls_backend_only_postgres.py`: pruebas estáticas siempre activas + integración PostgreSQL local opt-in con roles `anon`, `authenticated`, `service_role` y `manngo_backend` simulados, incluidas membresías directas/indirectas.
- `docs/RLS_ACTIVACION.md`: preflight, rollout no ejecutado, rollback parcial revisable, detección de drift y límites.
- `docs/oc-notas-RLS.md`: este registro.

## Decisiones

- **Backend-only, sin mapeo Supabase Auth:** el JWT Flask no es una identidad Supabase Auth; no se usó `auth.uid()` ni se inventó una relación `users.id ↔ auth.uid()`. No se modificó la aplicación para depender de Supabase Auth.
- **Rol backend explícito:** `manngo_backend` es un grupo `NOLOGIN`, sin contraseña ni atributos elevados. La migración **no crea logins** y no incluye credenciales. El rollout futuro debe hacer `GRANT manngo_backend TO <login_real_de_flask>`; el nombre real no se conoce y no se inventó.
- **Denegación de clientes:** se revoca `USAGE/CREATE` del esquema `public` y privilegios sobre tablas, vistas, secuencias y rutinas a `PUBLIC`, `anon` y `authenticated`. Se incluye `PUBLIC` porque `anon`/`authenticated` lo heredan.
- **RLS sin políticas amplias:** se habilitó RLS en todas las tablas base de `public`, se eliminan todas las políticas previas y solo se crea `manngo_backend_all` para el rol backend. No se habilita `FORCE RLS` para no romper al owner/trigger legítimos.
- **`users` no se expone:** no se mueve a otro esquema porque cambiaría el modelo/aplicación, fuera de alcance. El acceso directo de `anon`/`authenticated` queda denegado; el backend conserva lectura porque la autenticación Flask la necesita.
- **`service_role`:** no se concede a clientes. La migración revoca `manngo_backend` de `anon`, `authenticated` y `service_role`, y `service_role` de `anon`, `authenticated` y `manngo_backend` si el rol existe. `PUBLIC` no se usa para membresías (no es un rol de grupo); solo se revocan privilegios `FROM PUBLIC`. La postcondición usa `pg_has_role(..., 'MEMBER')` y falla ante cualquier membresía directa o indirecta.
- **Cambios futuros:** los default privileges cubren grants, pero no habilitan RLS en tablas nuevas. Tras un cambio de esquema se debe reejecutar la migración y las consultas de drift de `docs/RLS_ACTIVACION.md`.
- **Rollback:** no se incluye un rollback automático que reabra el acceso amplio de A10. Se documenta un rollback parcial que retira políticas/RLS y grants del backend sin regrantear a `anon`/`authenticated`; la vuelta exacta al estado anterior exige snapshot y aceptar explícitamente la exposición.

## Pruebas ejecutadas antes de la corrección (resultado real, historial)

### Test nuevo, sin PostgreSQL opt-in

```powershell
.\venv\Scripts\python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q tests/integration/test_rls_backend_only_postgres.py
```

Resultado real: `2 passed, 16 skipped in 0.25s`.

- Pasan las dos pruebas estáticas del SQL: existencia/estructura, `NOLOGIN`, ausencia de `PASSWORD`, `CREATE USER`, `LOGIN`, `auth.uid()`, `GRANT service_role` y grants a `anon`/`authenticated`.
- Los 16 casos PostgreSQL se omiten porque `MANNGO_RUN_PG_TESTS=1` y `MANNGO_LOCAL_POSTGRES_URL` no estaban suministrados. No es un fallo oculto: si el gate está activo y la URL es insegura o la configuración está incompleta, el test **falla**, no se omite.

### Suite completa

Primer intento, sin excluir nada: **error de colección** en `tests/test_sunat_xml.py` por `ModuleNotFoundError: No module named 'lxml'`. No se instaló ninguna dependencia, según la restricción. Es una limitación del entorno local, no un fallo de este lote.

Segundo intento, excluyendo solo ese módulo por la dependencia faltante:

```powershell
.\venv\Scripts\python.exe scripts/run_tests_offline.py --no-bytecode --no-cache --disable-warnings -q --ignore=tests/test_sunat_xml.py
```

Resultado real: `2 failed, 232 passed, 16 skipped, 240 warnings in 134.73s`.

Los 2 fallos son de `tests/test_reporte_produccion_entradas.py` y ya estaban documentados como ajenos en `docs/oc-notas-L1.md`:

- `total_unidades` 150 vs 230.
- `traslados.total_operaciones` 0 vs 1.

No se modificaron ni relajaron esas aserciones, ni se añadieron `skip`/`xfail` para ocultarlos. Las 2 pruebas nuevas estáticas pasan dentro de la suite.

### PostgreSQL local opt-in

**No ejecutado.** No se recibió `MANNGO_RUN_PG_TESTS=1`, `MANNGO_LOCAL_POSTGRES_URL` ni autorización `--allow-local-postgres` en esta sesión; no se inventó un resultado. El comando previsto para el padre/Docker está en `docs/RLS_ACTIVACION.md`.

## Corrección tras la ejecución PostgreSQL real del padre (Docker `--network none`)

El padre reportó `2 passed, 16 errors setup` en PostgreSQL real. Los errores y las correcciones aplicadas en estos mismos 4 archivos fueron:

1. **DSN con driver SQLAlchemy:** `_connect` recibía `postgresql+psycopg2://` y lo pasaba a `psycopg2.connect`, que solo acepta `postgresql://`. Ahora la validación loopback/`manngo_test*` y las guardas se ejecutan primero; la normalización a `postgresql://` se aplica **solo al serializar el DSN**. No se relajó el rechazo de bases no desechables.
2. **Base de mantenimiento:** se eliminó el fallback a la base `postgres` (nombre no autorizado por las guardas). Se usa la base existente suministrada, por ejemplo `manngo_test_audit`, para crear las bases temporales `manngo_test_rls_*`.
3. **SQL inválido de membresía:** se eliminó `REVOKE service_role FROM PUBLIC`, que PostgreSQL rechaza porque `PUBLIC` no es un rol de grupo para membresías. Se conservan los `REVOKE ... FROM PUBLIC` de privilegios, que sí son válidos.
4. **Blindaje de `manngo_backend`:** se revoca `manngo_backend` de `anon`, `authenticated` y `service_role`; se revoca `service_role` de `anon`, `authenticated` y `manngo_backend` (evita que el backend escale a `service_role`/BYPASSRLS). La postcondición usa `pg_has_role(..., 'MEMBER')` para detectar membresías directas e indirectas en ambas direcciones; si detecta una, aborta la transacción y exige revisión humana. No se declara seguro por el simple hecho de que `USAGE` sea falso.
5. **Regresiones nuevas:** el fixture pre-concede membresías directas e indirectas; se comprueba que `anon`/`authenticated` pueden hacer `SET ROLE manngo_backend` antes de la migración, que queda denegado después, que `service_role` tampoco es asumible por clientes ni por el backend y que una cadena indirecta hace abortar la migración con rollback (se conservan las políticas previas, no queda hardening parcial). Las membresías peligrosas se revocan al terminar cada bloque para no contaminar el clúster local ni producir falsos positivos.
6. **Pruebas estáticas:** se ajustaron para exigir las revocaciones nuevas y `pg_has_role`, y para rechazar explícitamente `REVOKE service_role FROM PUBLIC`. No se añadieron `skip`/`xfail` ni se suavizaron aserciones para ocultar fallos.

**Resultado real del padre tras la corrección anterior:** `20 passed, 6 failed`. Los 6 fallos fueron exclusivamente las pruebas de `SET ROLE denegado`.

**Causa:** `_assert_role_switch_denied` ejecutaba `SET LOCAL ROLE <first_role>` sobre una conexión cuyo `session_user` seguía siendo `manngo_test_owner` (SUPERUSER). `SET LOCAL ROLE` cambiaba `current_user`, pero el `session_user` superuser conservaba la capacidad de asumir cualquier rol, por lo que el `SET ROLE` posterior al rol no autorizado siempre era permitido. No era una prueba real de membresía de `anon`.

**Corrección mínima aplicada solo al test y a estas notas:** el helper ahora ejecuta `SET SESSION AUTHORIZATION <first_role>` desde la conexión admin, verifica `SELECT current_user, session_user = (first_role, first_role)` y solo entonces intenta `SET LOCAL ROLE <target_role>`, exigiendo el `42501` real. El control positivo `_set_role_chain` también establece `SESSION AUTHORIZATION roles[0]` y solo intercala `SET ROLE` para los roles posteriores; las aserciones usan `(current_user, session_user)`, p. ej. `("manngo_backend", "anon")`. No se cambió ningún `assert DENIED` a aceptado, no se añadieron `skip`/`xfail` y se mantienen los 26 casos. No se modificaron SQL, modelos, app ni `.env`.

**Reejecución local:** no se pudo ejecutar pytest en esta sesión por falta de herramienta de ejecución/Bash; no se inventa un resultado posterior. El padre debe verificar en Docker/PostgreSQL real. Los resultados `2 passed, 16 skipped` y `232 passed, 2 failed, 16 skipped` siguen siendo historial previo.

## Reproducción y límites

- No se declara un “fallo antes de reparar” no ejecutado. La evidencia estática de partida está en `database/db.sql` (políticas de acceso total a `authenticated`, grants amplios a `anon`/`authenticated` y vistas con owner). El test `test_a10_local_baseline_reproduces_permissive_access` reproduce el estado local amplio con datos sintéticos cuando se habilita el bloque PostgreSQL; los tests de denegación posteriores fallarían sin la migración.
- La migración SQL **no fue ejecutada por PostgreSQL en esta sesión**; su sintaxis y postcondiciones quedan para la verificación independiente en Docker. SQLite no puede validar RLS PostgreSQL.
- El test opt-in requiere un clúster PostgreSQL local con permisos para crear bases y roles NOLOGIN; para los `SET ROLE` de las regresiones se necesita superuser (recomendado) o membresía con `SET` en los roles de prueba, porque `CREATEROLE` por sí solo no permite `SET ROLE`. No abre URLs remotas: valida loopback literal y prefijo `manngo_test` antes de crear cualquier engine/conexión.
- El script no asume ni crea logins con contraseña; el único rol creado es un grupo `NOLOGIN`.
- No se ejecutó detección de drift contra un clúster real. Las consultas quedan preparadas en `docs/RLS_ACTIVACION.md` para una auditoría autorizada.
- **A10 permanece por verificar en vivo.** Esta entrega es una mitigación preparada y probada localmente, **no** una afirmación de que producción ya esté protegida.

## Bloqueos/pendientes reales

- Confirmar que ningún cliente usa el Data API de Supabase para estas tablas antes de activar.
- Identificar el login real de Flask y su membresía en `manngo_backend` sin exponer credenciales.
- Snapshot/backup, ventana y autorización humana para el rollout.
- Auditoría remota de solo lectura de grants, políticas, exposición y dueños.

Recordatorio: el usuario debe rotar/cambiar sus secretos reales en sus `.env` fuera de este lote; este lote no leyó ni editó esos archivos.
