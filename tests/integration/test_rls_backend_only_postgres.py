"""A10 · regresiones locales de RLS backend-only en PostgreSQL.

Estas pruebas NO tocan producción ni Supabase remoto. El bloque PostgreSQL
solo se ejecuta con autorización explícita y una URL local desechable:

    MANNGO_RUN_PG_TESTS=1
    MANNGO_LOCAL_POSTGRES_URL=postgresql://.../manngo_test_...
    DATABASE_URL=<la misma URL>
    scripts/run_tests_offline.py --allow-local-postgres ...

Si falta la autorización, se omite únicamente el bloque que necesita un
PostgreSQL local real. Las comprobaciones estáticas del SQL revisable se
ejecutan siempre y no usan skip/xfail.

Los datos sembrados son sintéticos (identificadores y hashes ficticios). No
se leen .env, credenciales reales ni bases remotas.
"""

from __future__ import annotations

import ipaddress
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy.engine import make_url

from scripts.offline_test_env import (
    UnsafeTestDatabaseError,
    install_offline_guards,
    validate_testing_database_url,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPOSITORY_ROOT / "migrations" / "manual" / "20261022_backend_only_rls.sql"
)
PG_GATE = "MANNGO_RUN_PG_TESTS"
PG_URL_ENV = "MANNGO_LOCAL_POSTGRES_URL"
PG_AUTHORIZATION_ENV = "MANNGO_ALLOW_LOCAL_POSTGRES_TESTS"
SYNTHETIC_HASH = "SYNTHETIC_NOT_A_REAL_PASSWORD_HASH_A10"
SYNTHETIC_USERNAMES = ("synthetic_admin_a10", "synthetic_user_a10")


@dataclass(frozen=True)
class DisposableDatabase:
    url: str
    name: str


# ---------------------------------------------------------------------------
# Guardas de entorno: nunca abrir una URL no validada como loopback / test
# ---------------------------------------------------------------------------


def _require_pg_url() -> str:
    if os.environ.get(PG_GATE, "").strip() != "1":
        pytest.skip(
            "PostgreSQL integration is opt-in: set MANNGO_RUN_PG_TESTS=1 and "
            "MANNGO_LOCAL_POSTGRES_URL, then run with --allow-local-postgres"
        )

    raw_url = os.environ.get(PG_URL_ENV, "").strip()
    if not raw_url:
        pytest.fail(
            "MANNGO_RUN_PG_TESTS=1 requires MANNGO_LOCAL_POSTGRES_URL pointing to "
            "a disposable loopback database named manngo_test*"
        )

    try:
        validate_testing_database_url(raw_url, allow_local_postgres=True)
    except UnsafeTestDatabaseError as error:
        pytest.fail(f"Refusing unsafe PostgreSQL test URL: {error}")

    parsed = make_url(raw_url)
    host = (parsed.host or "").strip("[]")
    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False

    assert parsed.get_backend_name() in {"postgres", "postgresql"}
    assert is_loopback, "PostgreSQL test host must be literal loopback"
    assert parsed.database and parsed.database.startswith("manngo_test"), (
        "PostgreSQL test database must start with manngo_test"
    )

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url != raw_url:
        pytest.fail(
            "DATABASE_URL must equal MANNGO_LOCAL_POSTGRES_URL so the offline "
            "runner validates the same disposable database"
        )
    if os.environ.get(PG_AUTHORIZATION_ENV, "").strip() != "1":
        pytest.fail(
            "Run with --allow-local-postgres; loopback PostgreSQL must be "
            "explicitly authorized by the offline runner"
        )

    install_offline_guards(allow_loopback=True)
    return raw_url


def _connect(url: str, *, database: str | None = None, autocommit: bool = True):
    parsed = make_url(url)
    if parsed.get_backend_name() not in {"postgres", "postgresql"}:
        raise RuntimeError("La conexión de prueba debe ser PostgreSQL")

    if database is not None:
        parsed = parsed.set(database=database)

    # SQLAlchemy usa postgresql+psycopg2://; libpq/psycopg2 solo acepta
    # postgresql://. La normalización se aplica únicamente al DSN, después
    # de la validación de loopback/manngo_test* y sin tocar las guardas.
    parsed = parsed.set(drivername="postgresql")
    dsn = parsed.render_as_string(hide_password=False)
    try:
        connection = psycopg2.connect(dsn, connect_timeout=5)
    except psycopg2.Error as error:
        raise RuntimeError(
            "No se pudo abrir la base PostgreSQL local desechable "
            f"({error.__class__.__name__})"
        ) from None
    connection.autocommit = autocommit
    return connection


def _maintenance_connect(url: str):
    # La base existente manngo_test_audit (o la indicada por el operador con
    # prefijo manngo_test*) es la base de mantenimiento. No se cae a
    # "postgres": sería otro nombre que las guardas no autorizaron.
    return _connect(url)


@contextmanager
def _fresh_database(base_url: str):
    database_name = f"manngo_test_rls_{uuid.uuid4().hex[:12]}"

    maintenance = _maintenance_connect(base_url)
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(database_name)
                )
            )
    finally:
        maintenance.close()

    database_url = (
        make_url(base_url)
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )

    try:
        yield DisposableDatabase(url=database_url, name=database_name)
    finally:
        cleanup = _maintenance_connect(base_url)
        try:
            with cleanup.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                cursor.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        sql.Identifier(database_name)
                    )
                )
        finally:
            cleanup.close()


# ---------------------------------------------------------------------------
# Semilla local: reproduce el estado amplio de database/db.sql
# ---------------------------------------------------------------------------

_LOCAL_ROLES_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'manngo_backend') THEN
        CREATE ROLE manngo_backend
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;
"""

_SEED_DANGEROUS_MEMBERSHIPS_SQL = """
-- Estado inseguro de partida: los roles cliente pueden SET ROLE al backend
-- y a service_role. La migración debe revocar estas membresías.
GRANT manngo_backend TO anon;
GRANT manngo_backend TO authenticated;
GRANT manngo_backend TO service_role;
GRANT service_role TO anon;
GRANT service_role TO authenticated;
"""

_REVOKE_DANGEROUS_MEMBERSHIPS_SQL = """
REVOKE manngo_backend FROM anon;
REVOKE manngo_backend FROM authenticated;
REVOKE manngo_backend FROM service_role;
REVOKE service_role FROM manngo_backend;
REVOKE service_role FROM anon;
REVOKE service_role FROM authenticated;
"""

_UNHARDENED_SCHEMA_SQL = """
CREATE TABLE public.users (
    id serial PRIMARY KEY,
    username varchar(80) NOT NULL UNIQUE,
    password varchar(256) NOT NULL,
    rol varchar(20) NOT NULL DEFAULT 'usuario'
);

CREATE TABLE public.ventas (
    id serial PRIMARY KEY,
    total numeric(12,2) NOT NULL CHECK (total > 0),
    estado_pago varchar(15) NOT NULL DEFAULT 'pendiente'
);

CREATE TABLE public.pagos (
    id serial PRIMARY KEY,
    venta_id integer NOT NULL REFERENCES public.ventas(id) ON DELETE CASCADE,
    monto numeric(12,2) NOT NULL CHECK (monto > 0)
);

CREATE VIEW public.vista_ventas AS
    SELECT id, total, estado_pago FROM public.ventas;

INSERT INTO public.users (username, password, rol)
VALUES
    ('synthetic_admin_a10', 'SYNTHETIC_NOT_A_REAL_PASSWORD_HASH_A10', 'admin'),
    ('synthetic_user_a10', 'SYNTHETIC_NOT_A_REAL_PASSWORD_HASH_A10', 'usuario');

INSERT INTO public.ventas (total, estado_pago) VALUES (100.00, 'pendiente');
INSERT INTO public.pagos (venta_id, monto) VALUES (1, 40.00);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ventas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pagos ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Permitir acceso total a usuarios autenticados"
    ON public.users FOR ALL TO authenticated
    USING (true) WITH CHECK (true);
CREATE POLICY "Permitir acceso total a usuarios autenticados"
    ON public.ventas FOR ALL TO authenticated
    USING (true) WITH CHECK (true);
CREATE POLICY "Permitir acceso total a usuarios autenticados"
    ON public.pagos FOR ALL TO authenticated
    USING (true) WITH CHECK (true);

GRANT USAGE ON SCHEMA public TO anon;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon;
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT SELECT ON public.vista_ventas TO PUBLIC;
"""


def _ensure_local_roles(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(_LOCAL_ROLES_SQL)


def _seed_dangerous_memberships(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(_SEED_DANGEROUS_MEMBERSHIPS_SQL)


def _revoke_dangerous_memberships(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(_REVOKE_DANGEROUS_MEMBERSHIPS_SQL)


def _seed_unhardened_schema(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(_UNHARDENED_SCHEMA_SQL)


def _apply_migration(connection) -> None:
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
    with connection.cursor() as cursor:
        cursor.execute(migration_sql)


@pytest.fixture(scope="module")
def unhardened_database():
    base_url = _require_pg_url()
    with _fresh_database(base_url) as database:
        connection = _connect(database.url)
        try:
            _ensure_local_roles(connection)
            _seed_unhardened_schema(connection)
        finally:
            connection.close()
        yield database


@pytest.fixture(scope="module")
def hardened_database():
    base_url = _require_pg_url()
    with _fresh_database(base_url) as database:
        connection = _connect(database.url)
        try:
            _ensure_local_roles(connection)
            _seed_unhardened_schema(connection)
            _revoke_dangerous_memberships(connection)
            _seed_dangerous_memberships(connection)
            try:
                _apply_migration(connection)
            finally:
                connection.rollback()
                _revoke_dangerous_memberships(connection)
        finally:
            connection.close()
        yield database


INDIRECT_ROLE = "manngo_test_indirect_a10"

_INDIRECT_MEMBERSHIP_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'manngo_test_indirect_a10'
    ) THEN
        CREATE ROLE manngo_test_indirect_a10
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

-- Cadena indirecta: anon -> bridge -> manngo_backend. La revocación directa
-- de anon no basta; la postcondición debe detectarla y abortar.
GRANT manngo_backend TO manngo_test_indirect_a10;
GRANT manngo_test_indirect_a10 TO anon;
"""


def _cleanup_indirect_role(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (INDIRECT_ROLE,)
        )
        if cursor.fetchone() is None:
            return
        cursor.execute(
            sql.SQL("REVOKE manngo_backend FROM {}").format(
                sql.Identifier(INDIRECT_ROLE)
            )
        )
        cursor.execute(
            sql.SQL("REVOKE {} FROM anon").format(sql.Identifier(INDIRECT_ROLE))
        )
        cursor.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(INDIRECT_ROLE))
        )


@pytest.fixture(scope="module")
def indirect_membership_database():
    base_url = _require_pg_url()
    with _fresh_database(base_url) as database:
        connection = _connect(database.url)
        try:
            _ensure_local_roles(connection)
            _revoke_dangerous_memberships(connection)
            with connection.cursor() as cursor:
                cursor.execute(_INDIRECT_MEMBERSHIP_SQL)
            _seed_unhardened_schema(connection)
        finally:
            connection.close()

        try:
            yield database
        finally:
            cleanup = _connect(database.url)
            try:
                _cleanup_indirect_role(cleanup)
            finally:
                cleanup.close()


@pytest.fixture(scope="module")
def backend_service_membership_database():
    """Caso separado: backend -> service_role sin el ciclo inverso."""
    base_url = _require_pg_url()
    with _fresh_database(base_url) as database:
        connection = _connect(database.url)
        try:
            _ensure_local_roles(connection)
            _seed_unhardened_schema(connection)
            _revoke_dangerous_memberships(connection)
            with connection.cursor() as cursor:
                cursor.execute("GRANT service_role TO manngo_backend")
        finally:
            connection.close()

        try:
            yield database
        finally:
            cleanup = _connect(database.url)
            try:
                with cleanup.cursor() as cursor:
                    cursor.execute("REVOKE service_role FROM manngo_backend")
            finally:
                cleanup.close()


# ---------------------------------------------------------------------------
# Helpers de consulta/rol
# ---------------------------------------------------------------------------


def _fetch_admin(database: DisposableDatabase, statement: str, params=None):
    connection = _connect(database.url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall()
    finally:
        connection.close()


def _fetch_as(database: DisposableDatabase, role: str, statement: str, params=None):
    connection = _connect(database.url, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
            )
            cursor.execute(statement, params)
            return cursor.fetchall()
    finally:
        connection.close()


def _set_role_chain(database: DisposableDatabase, roles):
    connection = _connect(database.url, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(roles[0])
                )
            )
            for role in roles[1:]:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
                )
            cursor.execute("SELECT current_user, session_user")
            return cursor.fetchall()
    finally:
        connection.close()


@contextmanager
def _dangerous_memberships(database: DisposableDatabase):
    connection = _connect(database.url)
    try:
        _revoke_dangerous_memberships(connection)
        _seed_dangerous_memberships(connection)
        yield
    finally:
        _revoke_dangerous_memberships(connection)
        connection.close()


def _assert_role_switch_denied(
    database: DisposableDatabase, first_role: str, target_role: str
):
    connection = _connect(database.url, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(first_role)
                )
            )
            cursor.execute("SELECT current_user, session_user")
            assert cursor.fetchall() == [(first_role, first_role)]
            with pytest.raises(psycopg2.Error) as error:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(target_role))
                )
            assert error.value.pgcode == "42501"
    finally:
        connection.close()


def _execute_as(database: DisposableDatabase, role: str, statements):
    connection = _connect(database.url, autocommit=False)
    try:
        results = []
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
            )
            for statement, params in statements:
                cursor.execute(statement, params)
                if cursor.description is not None:
                    results.append(cursor.fetchall())
        connection.commit()
        return results
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _assert_denied_read(database: DisposableDatabase, role: str, statement: str):
    with pytest.raises(psycopg2.Error) as error:
        _fetch_as(database, role, statement)
    assert error.value.pgcode == "42501"


def _assert_denied_write(database: DisposableDatabase, role: str, statement: str, params):
    with pytest.raises(psycopg2.Error) as error:
        _execute_as(database, role, [(statement, params)])
    assert error.value.pgcode == "42501"


# ---------------------------------------------------------------------------
# Reproducción local del estado previo (solo datos sintéticos)
# ---------------------------------------------------------------------------


def test_a10_local_baseline_reproduces_permissive_access(unhardened_database):
    """El estado tipo db.sql permite leer users a authenticated y la vista a anon.

    Es una simulación local con datos sintéticos; no afirma nada sobre el
    estado remoto de producción, que sigue pendiente de auditoría autorizada.
    """

    password_rows = _fetch_as(
        unhardened_database,
        "authenticated",
        "SELECT username, password FROM public.users ORDER BY id",
    )
    assert [row[0] for row in password_rows] == list(SYNTHETIC_USERNAMES)
    assert all(row[1] == SYNTHETIC_HASH for row in password_rows)

    view_rows = _fetch_as(
        unhardened_database,
        "anon",
        "SELECT total FROM public.vista_ventas",
    )
    assert len(view_rows) == 1
    assert view_rows[0][0] == Decimal("100.00")

    # Reproducción local de la membresía peligrosa: NOINHERIT no impide
    # SET ROLE si el rol cliente es miembro del grupo backend. Se revoca al
    # terminar para no contaminar el resto de la suite.
    with _dangerous_memberships(unhardened_database):
        assert _set_role_chain(
            unhardened_database, ["anon", "manngo_backend"]
        ) == [("manngo_backend", "anon")]
        assert _set_role_chain(
            unhardened_database, ["authenticated", "manngo_backend"]
        ) == [("manngo_backend", "authenticated")]
        assert _set_role_chain(
            unhardened_database, ["anon", "service_role"]
        ) == [("service_role", "anon")]


# ---------------------------------------------------------------------------
# Estado endurecido: clientes denegados, backend explícito operativo
# ---------------------------------------------------------------------------


def test_hardening_enables_rls_and_leaves_only_backend_policies(hardened_database):
    rls_rows = _fetch_admin(
        hardened_database,
        """
        SELECT c.relname, c.relrowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """,
    )
    assert rls_rows, "the disposable database must contain public base tables"
    assert all(row[1] is True for row in rls_rows)

    policies = _fetch_admin(
        hardened_database,
        """
        SELECT tablename, policyname, roles::text[]
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname
        """,
    )
    assert len(policies) == len(rls_rows)
    for _table, policy_name, roles in policies:
        assert policy_name == "manngo_backend_all"
        assert roles == ["manngo_backend"]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_role_has_no_schema_usage(hardened_database, role):
    rows = _fetch_admin(
        hardened_database,
        "SELECT has_schema_privilege(%s, 'public', 'USAGE'), "
        "has_schema_privilege(%s, 'public', 'CREATE')",
        (role, role),
    )
    assert rows == [(False, False)]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_set_role_backend_after_hardening(hardened_database, role):
    _assert_role_switch_denied(hardened_database, role, "manngo_backend")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_set_role_service_role_after_hardening(
    hardened_database, role
):
    _assert_role_switch_denied(hardened_database, role, "service_role")


def test_backend_role_cannot_set_role_service_role_after_hardening(
    hardened_database,
):
    _assert_role_switch_denied(hardened_database, "manngo_backend", "service_role")


def test_backend_membership_is_absent_for_direct_and_indirect_checks(
    hardened_database,
):
    rows = _fetch_admin(
        hardened_database,
        """
        SELECT pg_has_role('anon', 'manngo_backend', 'MEMBER'),
               pg_has_role('authenticated', 'manngo_backend', 'MEMBER'),
               pg_has_role('service_role', 'manngo_backend', 'MEMBER'),
               pg_has_role('manngo_backend', 'service_role', 'MEMBER')
        """,
    )
    assert rows == [(False, False, False, False)]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT password FROM public.users",
        "SELECT total FROM public.ventas",
        "SELECT total FROM public.vista_ventas",
        "SELECT monto FROM public.pagos",
    ],
)
def test_client_roles_cannot_read_users_or_business_objects(
    hardened_database, role, statement
):
    _assert_denied_read(hardened_database, role, statement)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_write_or_read_credentials(hardened_database, role):
    _assert_denied_write(
        hardened_database,
        role,
        "INSERT INTO public.users (username, password, rol) VALUES (%s, %s, 'usuario')",
        ("synthetic_forbidden_a10", SYNTHETIC_HASH),
    )
    _assert_denied_write(
        hardened_database,
        role,
        "UPDATE public.users SET password = %s",
        ("SYNTHETIC_OVERWRITE_A10",),
    )
    _assert_denied_write(
        hardened_database,
        role,
        "INSERT INTO public.ventas (total) VALUES (%s)",
        (Decimal("1.00"),),
    )
    _assert_denied_write(
        hardened_database,
        role,
        "DELETE FROM public.ventas",
        None,
    )

    assert _fetch_admin(
        hardened_database, "SELECT count(*) FROM public.users"
    ) == [(2,)]
    assert _fetch_admin(
        hardened_database, "SELECT count(*) FROM public.ventas"
    ) == [(1,)]


def test_backend_role_operates_on_decided_scope_and_persists(hardened_database):
    inserted = _execute_as(
        hardened_database,
        "manngo_backend",
        [
            (
                "INSERT INTO public.ventas (total, estado_pago) VALUES (%s, %s) "
                "RETURNING id",
                (Decimal("25.50"), "pendiente"),
            )
        ],
    )
    venta_id = inserted[0][0][0]

    users = _fetch_as(
        hardened_database,
        "manngo_backend",
        "SELECT password FROM public.users WHERE username = %s",
        ("synthetic_admin_a10",),
    )
    assert users == [(SYNTHETIC_HASH,)]

    persisted = _fetch_as(
        hardened_database,
        "manngo_backend",
        "SELECT total FROM public.ventas WHERE id = %s",
        (venta_id,),
    )
    assert persisted == [(Decimal("25.50"),)]

    with pytest.raises(psycopg2.Error) as error:
        _execute_as(
            hardened_database,
            "manngo_backend",
            [("CREATE TABLE public.should_not_exist_a10 (id integer)", None)],
        )
    assert error.value.pgcode == "42501"


def test_service_role_is_not_granted_to_client_roles(hardened_database):
    rows = _fetch_admin(
        hardened_database,
        """
        SELECT member_role.rolname
        FROM pg_auth_members m
        JOIN pg_roles granted_role ON granted_role.oid = m.roleid
        JOIN pg_roles member_role ON member_role.oid = m.member
        WHERE granted_role.rolname = 'service_role'
          AND member_role.rolname IN ('anon', 'authenticated')
        """,
    )
    assert rows == []


def test_backend_service_membership_is_revoked_by_migration(
    backend_service_membership_database,
):
    try:
        assert _set_role_chain(
            backend_service_membership_database, ["manngo_backend", "service_role"]
        ) == [("service_role", "manngo_backend")]

        connection = _connect(backend_service_membership_database.url)
        try:
            _apply_migration(connection)
        finally:
            connection.close()

        rows = _fetch_admin(
            backend_service_membership_database,
            "SELECT pg_has_role('manngo_backend', 'service_role', 'MEMBER')",
        )
        assert rows == [(False,)]
        _assert_role_switch_denied(
            backend_service_membership_database, "manngo_backend", "service_role"
        )
    finally:
        cleanup = _connect(backend_service_membership_database.url)
        try:
            with cleanup.cursor() as cursor:
                cursor.execute("REVOKE service_role FROM manngo_backend")
        finally:
            cleanup.close()


def test_indirect_backend_membership_aborts_migration_and_rolls_back(
    indirect_membership_database,
):
    try:
        connection = _connect(indirect_membership_database.url)
        try:
            with pytest.raises(psycopg2.Error) as error:
                _apply_migration(connection)
            assert error.value.pgcode == "P0001"
            assert "postcondition failed" in str(error.value)
            assert "SET ROLE manngo_backend" in str(error.value)
            connection.rollback()
        finally:
            connection.close()

        policies = _fetch_admin(
            indirect_membership_database,
            """
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = 'public' AND tablename = 'users'
            """,
        )
        policy_names = {row[0] for row in policies}
        assert "Permitir acceso total a usuarios autenticados" in policy_names
        assert "manngo_backend_all" not in policy_names

        usage = _fetch_admin(
            indirect_membership_database,
            "SELECT has_schema_privilege('anon', 'public', 'USAGE')",
        )
        assert usage == [(True,)]
    finally:
        cleanup = _connect(indirect_membership_database.url)
        try:
            _cleanup_indirect_role(cleanup)
        finally:
            cleanup.close()


# ---------------------------------------------------------------------------
# Contrato estático del SQL revisable (siempre se ejecuta)
# ---------------------------------------------------------------------------


def _sql_without_comments() -> str:
    content = MIGRATION_PATH.read_text(encoding="utf-8")
    return "\n".join(line.split("--", 1)[0] for line in content.splitlines())


def test_migration_sql_exists_and_is_reviewable():
    assert MIGRATION_PATH.is_file()
    sql_text = _sql_without_comments()
    assert "BEGIN;" in sql_text
    assert "COMMIT;" in sql_text
    assert "ENABLE ROW LEVEL SECURITY" in sql_text
    assert "DROP POLICY IF EXISTS" in sql_text
    assert "manngo_backend_all" in sql_text
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in sql_text
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM" in sql_text
    assert "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM" in sql_text
    assert "REVOKE manngo_backend FROM anon" in sql_text
    assert "REVOKE manngo_backend FROM authenticated" in sql_text
    assert "REVOKE manngo_backend FROM service_role" in sql_text
    assert "REVOKE service_role FROM manngo_backend" in sql_text
    assert "pg_has_role" in sql_text
    # PUBLIC no es un rol de grupo para membresías en PostgreSQL.
    assert "REVOKE service_role FROM PUBLIC" not in sql_text.upper()


def test_migration_sql_does_not_create_logins_or_invent_supabase_identity():
    sql_text = _sql_without_comments()

    assert "NOLOGIN" in sql_text.upper()
    assert re.search(r"\bPASSWORD\b", sql_text, re.IGNORECASE) is None
    assert re.search(r"\bLOGIN\b", sql_text, re.IGNORECASE) is None
    assert re.search(r"CREATE\s+USER", sql_text, re.IGNORECASE) is None
    assert re.search(r"auth\s*\.\s*uid\s*\(", sql_text, re.IGNORECASE) is None
    assert re.search(r"GRANT\s+service_role", sql_text, re.IGNORECASE) is None
    assert (
        re.search(
            r"GRANT\b[^;]*\bTO\s+(?:anon|authenticated)\b",
            sql_text,
            re.IGNORECASE | re.DOTALL,
        )
        is None
    )
