-- =====================================================================
-- Manngo · A10 · Hardening backend-only de privilegios y RLS
-- Archivo: migrations/manual/20261022_backend_only_rls.sql
-- Estado: PREPARADO. No aplicado a producción ni a Supabase remoto.
--
-- Contexto:
--   La aplicación es un backend Flask con conexión PostgreSQL directa.
--   El JWT de Flask NO es una identidad de Supabase Auth: no existe un
--   mapeo verificado entre users.id y auth.uid(). Por eso este script no
--   crea políticas auth.uid() ni intenta adivinar propietarios de filas.
--
-- Contrato de este script:
--   * Deja la base en modalidad backend-only: anon y authenticated no
--     pueden acceder directamente al esquema public, a users ni a las
--     tablas/vistas/secuencias de negocio.
--   * Habilita RLS en todas las tablas base de public y elimina todas las
--     políticas existentes. Solo crea una política explícita para el rol
--     de grupo manngo_backend.
--   * Crea manngo_backend como rol NOLOGIN (sin contraseña, sin LOGIN).
--     No crea cuentas de acceso ni incluye credenciales.
--   * No concede service_role a anon/authenticated/PUBLIC. Si existe,
--     revoca membresías peligrosas de/hacia service_role y manngo_backend.
--   * No modifica datos de negocio ni el esquema de las tablas.
--
-- Preflight obligatorio (revisar docs/RLS_ACTIVACION.md):
--   * Aplicar con ON_ERROR_STOP=1 (por ejemplo psql -v ON_ERROR_STOP=1 -f)
--     para que un error no se interprete como migración exitosa.
--   * Ejecutar con un rol propietario de las tablas o superuser.
--   * Los roles anon y authenticated deben existir (Supabase los define).
--   * Revisar antes los grants/políticas remotos en modo solo lectura.
--   * Probar primero en una base local desechable manngo_test*.
--
-- Rollback: no se incluye un rollback automático porque restaurar el
-- acceso amplio de anon/authenticated reabriría A10. Ver el procedimiento
-- de emergencia revisado en docs/RLS_ACTIVACION.md.
-- =====================================================================

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';

-- ---------------------------------------------------------------------
-- 1. Rol backend explícito: grupo NOLOGIN, sin password ni atributos altos
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'manngo_backend'
    ) THEN
        CREATE ROLE manngo_backend
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOBYPASSRLS;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'manngo_backend'
          AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb
               OR rolcreaterole OR rolreplication)
    ) THEN
        RAISE EXCEPTION
            'manngo_backend must be a NOLOGIN group role without elevated attributes';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname IN ('anon', 'authenticated')
          AND (rolsuper OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION
            'anon/authenticated must not be superuser or BYPASSRLS; review cluster roles';
    END IF;
END
$$;

-- ---------------------------------------------------------------------
-- 2. Denegar el esquema public a los clientes del Data API
--    Se revoca también PUBLIC porque anon/authenticated lo heredan.
-- ---------------------------------------------------------------------
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM anon;
REVOKE ALL ON SCHEMA public FROM authenticated;

GRANT USAGE ON SCHEMA public TO manngo_backend;

-- ---------------------------------------------------------------------
-- 3. Revocar privilegios directos sobre objetos existentes
--    ALL TABLES incluye tablas, vistas, vistas materializadas y foreign
--    tables. Así una vista no puede saltarse RLS con privilegios del owner.
-- ---------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;

REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM authenticated;

-- Ámbito decidido para el backend: CRUD de negocio y secuencias, sin DDL.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    TO manngo_backend;
GRANT USAGE, SELECT, UPDATE
    ON ALL SEQUENCES IN SCHEMA public
    TO manngo_backend;
GRANT EXECUTE
    ON ALL ROUTINES IN SCHEMA public
    TO manngo_backend;

-- ---------------------------------------------------------------------
-- 4. RLS en todas las tablas base de public.
--    Se eliminan todas las políticas previas (incluidas las de db.sql que
--    concedían acceso total a authenticated) y se deja una única política
--    explícita para manngo_backend.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    relation_row record;
    policy_row record;
BEGIN
    FOR relation_row IN
        SELECT c.oid, n.nspname, c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
            relation_row.nspname,
            relation_row.relname
        );

        FOR policy_row IN
            SELECT policyname
            FROM pg_catalog.pg_policies
            WHERE schemaname = relation_row.nspname
              AND tablename = relation_row.relname
        LOOP
            EXECUTE format(
                'DROP POLICY IF EXISTS %I ON %I.%I',
                policy_row.policyname,
                relation_row.nspname,
                relation_row.relname
            );
        END LOOP;

        EXECUTE format(
            'CREATE POLICY %I ON %I.%I FOR ALL TO %I USING (true) WITH CHECK (true)',
            'manngo_backend_all',
            relation_row.nspname,
            relation_row.relname,
            'manngo_backend'
        );
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------
-- 5. Default privileges para objetos creados en el futuro por el rol que
--    ejecuta esta migración. Rerun este script tras cambios de esquema.
-- ---------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO manngo_backend;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO manngo_backend;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO manngo_backend;

-- ---------------------------------------------------------------------
-- 6. Membresías peligrosas:
--    * anon/authenticated/service_role no deben poder SET ROLE al backend.
--    * service_role nunca se otorga a clientes.
--    PUBLIC no es un rol de grupo para membresías: solo se revocan
--    privilegios FROM PUBLIC, nunca "REVOKE <rol> FROM PUBLIC".
-- ---------------------------------------------------------------------
DO $$
BEGIN
    EXECUTE 'REVOKE manngo_backend FROM anon';
    EXECUTE 'REVOKE manngo_backend FROM authenticated';

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role'
    ) THEN
        EXECUTE 'REVOKE manngo_backend FROM service_role';
        EXECUTE 'REVOKE service_role FROM manngo_backend';
        EXECUTE 'REVOKE service_role FROM anon';
        EXECUTE 'REVOKE service_role FROM authenticated';
    END IF;
END
$$;

-- ---------------------------------------------------------------------
-- 7. Postcondiciones: si el estado no es backend-only, abortar y hacer
--    rollback de toda la migración en lugar de dejar una falsa sensación
--    de seguridad.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    client_role text;
    relation_row record;
    policy_row record;
BEGIN
    FOREACH client_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF has_schema_privilege(client_role::name, 'public', 'USAGE') THEN
            RAISE EXCEPTION
                'postcondition failed: % still has USAGE on schema public', client_role;
        END IF;

        IF has_schema_privilege(client_role::name, 'public', 'CREATE') THEN
            RAISE EXCEPTION
                'postcondition failed: % still has CREATE on schema public', client_role;
        END IF;

        IF pg_has_role(client_role::name, 'manngo_backend', 'MEMBER') THEN
            RAISE EXCEPTION
                'postcondition failed: % can SET ROLE manngo_backend (direct or indirect membership); manual review required',
                client_role;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role'
    ) THEN
        IF pg_has_role('service_role'::name, 'manngo_backend', 'MEMBER') THEN
            RAISE EXCEPTION
                'postcondition failed: service_role can SET ROLE manngo_backend; manual review required';
        END IF;

        IF pg_has_role('manngo_backend'::name, 'service_role'::name, 'MEMBER') THEN
            RAISE EXCEPTION
                'postcondition failed: manngo_backend can SET ROLE service_role; manual review required';
        END IF;

        FOREACH client_role IN ARRAY ARRAY['anon', 'authenticated']
        LOOP
            IF pg_has_role(client_role::name, 'service_role'::name, 'MEMBER') THEN
                RAISE EXCEPTION
                    'postcondition failed: % can SET ROLE service_role; manual review required',
                    client_role;
            END IF;
        END LOOP;
    END IF;

    FOR relation_row IN
        SELECT c.oid, c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class
            WHERE oid = relation_row.oid
              AND relrowsecurity
        ) THEN
            RAISE EXCEPTION
                'postcondition failed: RLS disabled on public.%',
                relation_row.relname;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_policies
            WHERE schemaname = 'public'
              AND tablename = relation_row.relname
              AND policyname = 'manngo_backend_all'
              AND 'manngo_backend' = ANY (roles)
        ) THEN
            RAISE EXCEPTION
                'postcondition failed: missing backend policy on public.%',
                relation_row.relname;
        END IF;
    END LOOP;

    FOR relation_row IN
        SELECT c.oid, c.relname, c.relkind
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
    LOOP
        FOREACH client_role IN ARRAY ARRAY['anon', 'authenticated']
        LOOP
            IF relation_row.relkind = 'S' THEN
                IF has_sequence_privilege(
                    client_role::name, relation_row.oid, 'USAGE,SELECT,UPDATE'
                ) THEN
                    RAISE EXCEPTION
                        'postcondition failed: % keeps privileges on sequence public.%',
                        client_role, relation_row.relname;
                END IF;
            ELSE
                IF has_table_privilege(
                    client_role::name,
                    relation_row.oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                ) THEN
                    RAISE EXCEPTION
                        'postcondition failed: % keeps privileges on public.%',
                        client_role, relation_row.relname;
                END IF;
            END IF;
        END LOOP;
    END LOOP;

    FOR policy_row IN
        SELECT policyname, tablename, roles
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
    LOOP
        IF 'anon' = ANY (policy_row.roles)
           OR 'authenticated' = ANY (policy_row.roles)
           OR 'public' = ANY (policy_row.roles) THEN
            RAISE EXCEPTION
                'postcondition failed: policy % on public.% targets a client role',
                policy_row.policyname, policy_row.tablename;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'manngo_backend'
          AND rolcanlogin
    ) THEN
        RAISE EXCEPTION
            'postcondition failed: manngo_backend must remain NOLOGIN';
    END IF;
END
$$;

COMMIT;
