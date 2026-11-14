"""Offline test environment helpers.

This module is intentionally import-safe: it must never import ``app``,
``extensions``, ``models`` or any business resource. The runner and the pytest
conftest use it to force a disposable local test configuration before the Flask
application is imported.
"""

from __future__ import annotations

import builtins
import ipaddress
import os
import socket
import sys
import threading
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

OFFLINE_ENV_FLAG = "MANNGO_OFFLINE_TESTS"
ALLOW_LOCAL_POSTGRES_ENV = "MANNGO_ALLOW_LOCAL_POSTGRES_TESTS"
LOCAL_POSTGRES_URL_ENV = "MANNGO_LOCAL_POSTGRES_URL"
ALLOW_LOCAL_REDIS_ENV = "MANNGO_ALLOW_LOCAL_REDIS_TESTS"
LOCAL_REDIS_URL_ENV = "MANNGO_LOCAL_REDIS_URL"
DEFAULT_POSTGRES_PORT = 5432
DEFAULT_REDIS_PORT = 6379
DEFAULT_LOCAL_REDIS_URL = f"redis://127.0.0.1:{DEFAULT_REDIS_PORT}/0"
REDIS_LOOPBACK_KIND = "redis-loopback"
TESTING_ENV = "testing"
DEFAULT_TEST_DATABASE_URL = "sqlite:///:memory:"
SYNTHETIC_JWT_SECRET = "manngo-offline-testing-jwt-secret-key-0000000000"
EXCLUDED_STORAGE_TEST = "test_supabase_storage_integration.py"

INTEGRATION_ENV_KEYS = frozenset(
    {
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME",
        "TELEGRAM_BOT_SECRET_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEGRAM_CHAT_ID",
        "SUNAT_RUC",
        "SUNAT_USUARIO_SOL",
        "SUNAT_CLAVE_SOL",
        "SUNAT_AMBIENTE",
        "SUNAT_DEFAULT_LLEGADA_DIRECCION",
        "SUNAT_DEFAULT_LLEGADA_UBIGEO",
        "SUNAT_DEFAULT_PARTIDA_DIRECCION",
        "SUNAT_DEFAULT_PARTIDA_UBIGEO",
        "SUNAT_DEFAULT_PLACA",
        "SUNAT_DEFAULT_CHOFER_DNI",
        "SUNAT_DEFAULT_CHOFER_LICENCIA",
        "SUNAT_DEFAULT_CHOFER_NOMBRE",
        "MANNGO_ID_SUNAT",
        "MANNGO_CLAVE_SUNAT",
        "S3_BUCKET",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_ENDPOINT_URL",
        "LIMITER_STORAGE_URI",
        "RATELIMIT_STORAGE_URI",
        "RATELIMIT_STORAGE_URL",
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "OPENAI_API_KEY",
    }
)

INTEGRATION_ENV_PREFIXES = (
    "PG",
    "SUPABASE_",
    "GOOGLE_",
    "GEMINI_",
    "TELEGRAM_",
    "SUNAT_",
    "AWS_",
    "S3_",
    "REDIS_",
    "LIMITER_",
    "OPENAI_",
)

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
POSTGRES_BACKENDS = frozenset({"postgres", "postgresql"})
UNSAFE_POSTGRES_QUERY_KEYS = frozenset(
    {"host", "hostaddr", "service", "servicefile", "dbname", "database"}
)
UNSAFE_REDIS_QUERY_KEYS = frozenset(
    {"host", "hostaddr", "path", "unix_socket_path", "socket", "url", "db", "database"}
)

_SAFE_DATABASE_ERROR = (
    "Refusing unsafe test database configuration. Use SQLite in-memory, or an "
    "explicitly authorized disposable PostgreSQL database whose name starts "
    "with 'manngo_test' on a literal loopback host."
)
_SAFE_REDIS_ERROR = (
    "Refusing unsafe test Redis configuration. Use the reserved loopback "
    "MANNGO_LOCAL_REDIS_URL with a numeric database, alongside an explicitly "
    "authorized disposable PostgreSQL database."
)


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when a test database URL is not explicitly disposable and local."""


class UnsafeTestRedisError(UnsafeTestDatabaseError):
    """Raised when a test Redis URL is not an explicitly authorized loopback."""


class OfflineNetworkBlocked(RuntimeError):
    """Raised when offline tests attempt an outbound network operation."""


@dataclass(frozen=True)
class EnvironmentStatus:
    database_kind: str
    removed_integration_keys: tuple[str, ...]
    dotenv_disabled: bool
    allow_loopback: bool
    allow_local_redis: bool = False

    def summary(self) -> str:
        return (
            f"database={self.database_kind}; "
            f"integrations_cleared={len(self.removed_integration_keys)}; "
            f"dotenv={'disabled' if self.dotenv_disabled else 'enabled'}; "
            f"loopback={'allowed' if self.allow_loopback else 'blocked'}"
        )


def _resolve_env(env: MutableMapping[str, str] | None) -> MutableMapping[str, str]:
    return os.environ if env is None else env


def _env_flag(env: Mapping[str, str], key: str) -> bool:
    value = str(env.get(key, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_offline_testing(env: Mapping[str, str] | None = None) -> bool:
    current = _resolve_env(None if env is None else env)
    return (
        current.get("FLASK_ENV", "").strip().lower() == TESTING_ENV
        or _env_flag(current, OFFLINE_ENV_FLAG)
    )


def should_skip_dotenv(env: Mapping[str, str] | None = None) -> bool:
    return is_offline_testing(env)


def clear_integration_environment(
    env: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    current = _resolve_env(env)
    removed: list[str] = []
    for key in list(current):
        if key in INTEGRATION_ENV_KEYS or key.startswith(INTEGRATION_ENV_PREFIXES):
            current.pop(key, None)
            removed.append(key)
    return tuple(sorted(removed))


def _raise_unsafe_database() -> None:
    raise UnsafeTestDatabaseError(_SAFE_DATABASE_ERROR)


def _raise_unsafe_redis() -> None:
    raise UnsafeTestRedisError(_SAFE_REDIS_ERROR)


def _is_sqlite_memory(parsed) -> bool:
    return parsed.database in (None, "", ":memory:")


def _is_loopback_literal(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().strip("[]")
    if normalized.lower() in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_testing_database_url(
    url: str,
    *,
    env: Mapping[str, str] | None = None,
    allow_local_postgres: bool | None = None,
) -> str:
    """Validate a URL without opening a connection or exposing credentials."""
    if not isinstance(url, str) or not url.strip():
        _raise_unsafe_database()

    try:
        parsed = make_url(url.strip())
    except (ArgumentError, ValueError):
        _raise_unsafe_database()

    backend = parsed.get_backend_name()

    if backend == "sqlite":
        if _is_sqlite_memory(parsed):
            return "sqlite-memory"
        _raise_unsafe_database()

    if backend in POSTGRES_BACKENDS:
        current = _resolve_env(None if env is None else env)
        authorized = (
            _env_flag(current, ALLOW_LOCAL_POSTGRES_ENV)
            if allow_local_postgres is None
            else bool(allow_local_postgres)
        )
        if not authorized:
            _raise_unsafe_database()
        if not _is_loopback_literal(parsed.host):
            _raise_unsafe_database()
        database_name = parsed.database or ""
        if not database_name.startswith("manngo_test"):
            _raise_unsafe_database()
        for key in parsed.query:
            if str(key).lower() in UNSAFE_POSTGRES_QUERY_KEYS:
                _raise_unsafe_database()
        return "postgresql-loopback-disposable"

    _raise_unsafe_database()


def validate_testing_redis_url(
    url: str,
    *,
    env: Mapping[str, str] | None = None,
    allow_local_redis: bool | None = None,
) -> str:
    """Validate the reserved local Redis URI without opening a connection.

    Only the reserved ``MANNGO_LOCAL_REDIS_URL`` value is meaningful here:
    the conventional ``REDIS_URL``/``REDIS_*`` variables never authorize a
    port. The URI must use the plain ``redis`` scheme, a literal loopback
    host, a TCP port in 1..65535 and a numeric database path. Query keys that
    can redirect the endpoint (or the database) and UNIX sockets are rejected.
    """
    if not isinstance(url, str) or not url.strip():
        _raise_unsafe_redis()

    try:
        parsed = make_url(url.strip())
    except (ArgumentError, ValueError):
        _raise_unsafe_redis()

    if parsed.get_backend_name() != "redis":
        _raise_unsafe_redis()

    current = _resolve_env(None if env is None else env)
    authorized = (
        _env_flag(current, ALLOW_LOCAL_REDIS_ENV)
        if allow_local_redis is None
        else bool(allow_local_redis)
    )
    if not authorized:
        _raise_unsafe_redis()

    if not _is_loopback_literal(parsed.host):
        _raise_unsafe_redis()

    for key in parsed.query:
        if str(key).lower() in UNSAFE_REDIS_QUERY_KEYS:
            _raise_unsafe_redis()

    port = parsed.port
    if port is None:
        port = DEFAULT_REDIS_PORT
    if type(port) is not int or not 1 <= port <= 65535:
        _raise_unsafe_redis()

    database = parsed.database
    if not database or not database.isascii() or not database.isdigit():
        _raise_unsafe_redis()

    return REDIS_LOOPBACK_KIND


def _validated_postgres_ports(env: Mapping[str, str] | None = None) -> frozenset[int]:
    """Deriva el puerto TCP permitido de los dos DSN PostgreSQL validados.

    Exige que ``DATABASE_URL`` y ``MANNGO_LOCAL_POSTGRES_URL`` sean PostgreSQL
    loopback desechables, coincidan entre sí y estén autorizados por el flag
    local. No abre conexiones y no amplía el permiso a otros puertos.
    """
    current = _resolve_env(None if env is None else env)
    database_url = str(current.get("DATABASE_URL", "")).strip()
    local_url = str(current.get(LOCAL_POSTGRES_URL_ENV, "")).strip()
    if not database_url or not local_url:
        _raise_unsafe_database()

    database_kind = validate_testing_database_url(database_url, env=current)
    local_kind = validate_testing_database_url(local_url, env=current)
    if (
        database_kind != "postgresql-loopback-disposable"
        or local_kind != "postgresql-loopback-disposable"
    ):
        _raise_unsafe_database()

    try:
        database_parsed = make_url(database_url)
        local_parsed = make_url(local_url)
        port = database_parsed.port
    except (ArgumentError, ValueError):
        _raise_unsafe_database()

    if database_parsed != local_parsed:
        _raise_unsafe_database()

    host = (database_parsed.host or "").strip("[]")
    if not _is_loopback_literal(host):
        _raise_unsafe_database()

    if port is None:
        port = DEFAULT_POSTGRES_PORT
    if type(port) is not int or not 1 <= port <= 65535:
        _raise_unsafe_database()

    return frozenset({port})


def _validated_redis_ports(
    env: Mapping[str, str] | None = None,
    *,
    allow_local_redis: bool | None = None,
) -> frozenset[int]:
    """Deriva el puerto TCP del Redis loopback reservado y autorizado.

    Redis solo se admite junto a un PostgreSQL loopback desechable ya
    validado. La autorización procede exclusivamente de
    ``MANNGO_ALLOW_LOCAL_REDIS_TESTS`` (o de un ``True`` explícito de la API)
    y del URI reservado ``MANNGO_LOCAL_REDIS_URL``; ``REDIS_URL`` y el permiso
    genérico de loopback no habilitan nada por sí mismos.
    """
    current = _resolve_env(None if env is None else env)
    authorized = (
        _env_flag(current, ALLOW_LOCAL_REDIS_ENV)
        if allow_local_redis is None
        else bool(allow_local_redis)
    )
    if not authorized:
        _raise_unsafe_redis()

    _validated_postgres_ports(current)

    raw_url = str(current.get(LOCAL_REDIS_URL_ENV, "")).strip()
    if not raw_url:
        raw_url = DEFAULT_LOCAL_REDIS_URL

    validate_testing_redis_url(
        raw_url, env=current, allow_local_redis=True
    )

    try:
        parsed = make_url(raw_url)
        port = parsed.port
    except (ArgumentError, ValueError):
        _raise_unsafe_redis()

    if port is None:
        port = DEFAULT_REDIS_PORT
    if type(port) is not int or not 1 <= port <= 65535:
        _raise_unsafe_redis()

    return frozenset({port})


def _validated_loopback_ports(
    env: Mapping[str, str] | None = None,
    *,
    allow_local_redis: bool | None = None,
) -> frozenset[int]:
    """Puertos congelables en la primera instalación: PostgreSQL y, opcionalmente, Redis."""
    ports = set(_validated_postgres_ports(env))
    current = _resolve_env(None if env is None else env)
    redis_authorized = (
        _env_flag(current, ALLOW_LOCAL_REDIS_ENV)
        if allow_local_redis is None
        else bool(allow_local_redis)
    )
    if redis_authorized:
        ports.update(_validated_redis_ports(current, allow_local_redis=True))
    return frozenset(ports)


def prepare_test_environment(
    env: MutableMapping[str, str] | None = None,
    *,
    database_url: str | None = None,
    allow_local_postgres: bool | None = None,
    allow_local_redis: bool | None = None,
) -> EnvironmentStatus:
    current = _resolve_env(env)

    current["FLASK_ENV"] = TESTING_ENV
    current[OFFLINE_ENV_FLAG] = "1"
    current["FLASK_DEBUG"] = "0"

    if allow_local_postgres is not None:
        if allow_local_postgres:
            current[ALLOW_LOCAL_POSTGRES_ENV] = "1"
        else:
            current.pop(ALLOW_LOCAL_POSTGRES_ENV, None)

    if allow_local_redis is False:
        current.pop(ALLOW_LOCAL_REDIS_ENV, None)

    candidate = database_url
    if candidate is None:
        candidate = current.get("DATABASE_URL") or DEFAULT_TEST_DATABASE_URL

    database_kind = validate_testing_database_url(candidate, env=current)
    current["DATABASE_URL"] = candidate
    current["JWT_SECRET_KEY"] = SYNTHETIC_JWT_SECRET
    current["JWT_EXPIRES_SECONDS"] = "3600"
    current["DEFAULT_RATE_LIMIT"] = "100000 per day;100000 per hour"

    redis_enabled = False
    if allow_local_redis is True:
        _validated_redis_ports(current, allow_local_redis=True)
        if not str(current.get(LOCAL_REDIS_URL_ENV, "")).strip():
            current[LOCAL_REDIS_URL_ENV] = DEFAULT_LOCAL_REDIS_URL
        current[ALLOW_LOCAL_REDIS_ENV] = "1"
        redis_enabled = True
    elif (
        allow_local_redis is None
        and _env_flag(current, ALLOW_LOCAL_REDIS_ENV)
        and database_kind == "postgresql-loopback-disposable"
        and _env_flag(current, ALLOW_LOCAL_POSTGRES_ENV)
        and str(current.get("DATABASE_URL", "")).strip()
        and str(current.get(LOCAL_POSTGRES_URL_ENV, "")).strip()
    ):
        _validated_redis_ports(current, allow_local_redis=True)
        if not str(current.get(LOCAL_REDIS_URL_ENV, "")).strip():
            current[LOCAL_REDIS_URL_ENV] = DEFAULT_LOCAL_REDIS_URL
        redis_enabled = True

    removed = clear_integration_environment(current)
    allow_loopback = (
        database_kind == "postgresql-loopback-disposable"
        and _env_flag(current, ALLOW_LOCAL_POSTGRES_ENV)
    )
    allow_local_redis_enabled = bool(redis_enabled and allow_loopback)

    return EnvironmentStatus(
        database_kind=database_kind,
        removed_integration_keys=removed,
        dotenv_disabled=should_skip_dotenv(current),
        allow_loopback=allow_loopback,
        allow_local_redis=allow_local_redis_enabled,
    )


def _is_target_test_argument(argument: str) -> bool:
    candidate = argument
    if candidate.startswith("--ignore="):
        candidate = candidate.split("=", 1)[1]
    path_part = candidate.split("::", 1)[0].replace("\\", "/")
    return Path(path_part).name == EXCLUDED_STORAGE_TEST


def build_pytest_args(
    user_args: Sequence[str] = (),
    *,
    repository_root: str | Path | None = None,
    no_cache: bool = False,
) -> list[str]:
    root = Path(repository_root) if repository_root is not None else Path(__file__).resolve().parents[1]
    excluded = (root / "tests" / EXCLUDED_STORAGE_TEST).resolve()

    args: list[str] = []
    index = 0
    while index < len(user_args):
        argument = user_args[index]
        if argument == "--ignore" and index + 1 < len(user_args):
            if _is_target_test_argument(user_args[index + 1]):
                index += 2
                continue
        if _is_target_test_argument(argument):
            index += 1
            continue
        args.append(argument)
        index += 1

    ignore_argument = f"--ignore={excluded}"
    if ignore_argument not in args:
        args.append(ignore_argument)

    if no_cache and "no:cacheprovider" not in args:
        args.extend(["-p", "no:cacheprovider"])

    return args


_guard_lock = threading.Lock()
_guard_state = {
    "installed": False,
    "allow_loopback": False,
    "allow_local_redis": False,
    "allowed_loopback_ports": frozenset(),
    "allowed_postgres_ports": frozenset(),
    "allowed_redis_ports": frozenset(),
}
_original_import = builtins.__import__
_original_socket_connect = None
_original_socket_connect_ex = None
_original_socket_sendto = None
_original_create_connection = None
_original_ssl_connect = None


def _blocked_network_call(*args, **kwargs):
    raise OfflineNetworkBlocked(
        "Outbound network access is blocked by the offline test environment"
    )


def _is_loopback_address(address) -> bool:
    try:
        host = address[0]
    except (TypeError, IndexError, KeyError):
        return False
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="ignore")
    if not isinstance(host, str):
        return False
    normalized = host.strip().strip("[]")
    if normalized.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_allowed_loopback_tcp(address) -> bool:
    """Solo permite TCP al loopback y al puerto del DSN PostgreSQL validado."""
    if not _guard_state["allow_loopback"]:
        return False
    if not isinstance(address, (tuple, list)):
        return False
    if not _is_loopback_address(address):
        return False
    try:
        port = address[1]
    except (TypeError, IndexError, KeyError):
        return False
    return type(port) is int and port in _guard_state["allowed_loopback_ports"]


def _guarded_socket_connect(self, address, *args, **kwargs):
    if _is_allowed_loopback_tcp(address):
        return _original_socket_connect(self, address, *args, **kwargs)
    raise OfflineNetworkBlocked(
        "Outbound socket connections are blocked by the offline test environment"
    )


def _guarded_socket_connect_ex(self, address, *args, **kwargs):
    if _is_allowed_loopback_tcp(address):
        return _original_socket_connect_ex(self, address, *args, **kwargs)
    raise OfflineNetworkBlocked(
        "Outbound socket connections are blocked by the offline test environment"
    )


def _guarded_socket_sendto(self, data, *args, **kwargs):
    raise OfflineNetworkBlocked(
        "Outbound socket traffic is blocked by the offline test environment"
    )


def _guarded_create_connection(address, *args, **kwargs):
    if _is_allowed_loopback_tcp(address):
        return _original_create_connection(address, *args, **kwargs)
    raise OfflineNetworkBlocked(
        "Outbound socket connections are blocked by the offline test environment"
    )


def _guarded_ssl_connect(self, address, *args, **kwargs):
    if _is_allowed_loopback_tcp(address):
        return _original_ssl_connect(self, address, *args, **kwargs)
    raise OfflineNetworkBlocked(
        "Outbound TLS connections are blocked by the offline test environment"
    )


def _replace_attribute(target, name: str, value) -> None:
    setattr(target, name, value)


def _patch_requests(module) -> None:
    session_class = getattr(module, "Session", None)
    if session_class is not None:
        _replace_attribute(session_class, "request", _blocked_network_call)
        _replace_attribute(session_class, "send", _blocked_network_call)
    api_module = getattr(module, "api", None)
    if api_module is not None:
        _replace_attribute(api_module, "request", _blocked_network_call)
    for name in ("request", "get", "post", "put", "patch", "delete", "head", "options"):
        if hasattr(module, name):
            _replace_attribute(module, name, _blocked_network_call)


def _patch_httpx(module) -> None:
    for class_name in ("Client", "AsyncClient"):
        client_class = getattr(module, class_name, None)
        if client_class is not None:
            _replace_attribute(client_class, "request", _blocked_network_call)
            _replace_attribute(client_class, "send", _blocked_network_call)
    for name in ("request", "get", "post", "put", "patch", "delete", "head", "options"):
        if hasattr(module, name):
            _replace_attribute(module, name, _blocked_network_call)


def _patch_urllib_request(module) -> None:
    if hasattr(module, "urlopen"):
        _replace_attribute(module, "urlopen", _blocked_network_call)
    opener_class = getattr(module, "OpenerDirector", None)
    if opener_class is not None:
        _replace_attribute(opener_class, "open", _blocked_network_call)


def _patch_http_client(module) -> None:
    for class_name in ("HTTPConnection", "HTTPSConnection"):
        connection_class = getattr(module, class_name, None)
        if connection_class is not None:
            _replace_attribute(connection_class, "connect", _blocked_network_call)
            _replace_attribute(connection_class, "request", _blocked_network_call)


def _patch_urllib3_connectionpool(module) -> None:
    for class_name in ("HTTPConnectionPool", "HTTPSConnectionPool"):
        pool_class = getattr(module, class_name, None)
        if pool_class is not None:
            _replace_attribute(pool_class, "urlopen", _blocked_network_call)


def _patch_gemini(module) -> None:
    for name in ("configure", "embed_content"):
        if hasattr(module, name):
            _replace_attribute(module, name, _blocked_network_call)
    model_class = getattr(module, "GenerativeModel", None)
    if model_class is not None:
        for name in (
            "generate_content",
            "generate_content_async",
            "count_tokens",
            "start_chat",
        ):
            if hasattr(model_class, name):
                _replace_attribute(model_class, name, _blocked_network_call)


def _patch_supabase(module) -> None:
    for name in ("create_client", "create_async_client"):
        if hasattr(module, name):
            _replace_attribute(module, name, _blocked_network_call)


def _patch_boto3(module) -> None:
    for name in ("client", "resource", "Session"):
        if hasattr(module, name):
            _replace_attribute(module, name, _blocked_network_call)


_TARGET_MODULES = {
    "requests": _patch_requests,
    "httpx": _patch_httpx,
    "urllib.request": _patch_urllib_request,
    "http.client": _patch_http_client,
    "urllib3.connectionpool": _patch_urllib3_connectionpool,
    "google.generativeai": _patch_gemini,
    "supabase": _patch_supabase,
    "boto3": _patch_boto3,
}


def _patch_loaded_module(name: str) -> None:
    patch_function = _TARGET_MODULES.get(name)
    module = sys.modules.get(name)
    if patch_function is None or module is None:
        return
    try:
        patch_function(module)
    except Exception:
        pass


def _offline_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _original_import(name, globals, locals, fromlist, level)
    try:
        if name in _TARGET_MODULES:
            _patch_loaded_module(name)
        for target in _TARGET_MODULES:
            if name.startswith(target + "."):
                _patch_loaded_module(target)
        for item in fromlist or ():
            combined = f"{name}.{item}" if name else str(item)
            if combined in _TARGET_MODULES:
                _patch_loaded_module(combined)
    except Exception:
        pass
    return module


def _close_redis_permission_locked() -> None:
    """Retira solo los puertos Redis conocidos, preservando PostgreSQL."""
    redis_ports = frozenset(_guard_state.get("allowed_redis_ports", frozenset()))
    postgres_ports = frozenset(
        _guard_state.get("allowed_postgres_ports", frozenset())
    )
    if not redis_ports:
        _guard_state["allow_local_redis"] = False
        return
    if not postgres_ports:
        postgres_ports = (
            frozenset(_guard_state["allowed_loopback_ports"]) - redis_ports
        )
    _guard_state["allowed_redis_ports"] = frozenset()
    _guard_state["allowed_loopback_ports"] = postgres_ports
    _guard_state["allow_local_redis"] = False


def install_offline_guards(
    allow_loopback: bool | None = None,
    *,
    allow_local_redis: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Instala las guardas de red del entorno offline.

    ``None`` conserva la política vigente sin ampliarla. ``False`` cierra
    cualquier permiso loopback. ``True`` exige un DSN PostgreSQL loopback
    desechable validado y solo autoriza su puerto TCP. Redis se agrega
    únicamente cuando la autorización local explícita también es válida y
    junto al PostgreSQL autorizado. Una vez instaladas, las guardas no
    amplían sus endpoints: el permiso queda congelado en la primera
    instalación.
    """
    global _original_socket_connect
    global _original_socket_connect_ex
    global _original_socket_sendto
    global _original_create_connection
    global _original_ssl_connect

    current = _resolve_env(None if env is None else env)
    redis_authorized = (
        _env_flag(current, ALLOW_LOCAL_REDIS_ENV)
        if allow_local_redis is None
        else bool(allow_local_redis)
    )

    with _guard_lock:
        if allow_loopback is True:
            postgres_ports = _validated_postgres_ports(current)
            redis_ports = (
                _validated_redis_ports(current, allow_local_redis=True)
                if redis_authorized
                else frozenset()
            )
            allowed_ports = postgres_ports | redis_ports
            if not _guard_state["installed"]:
                _guard_state["allow_loopback"] = True
                _guard_state["allow_local_redis"] = bool(redis_ports)
                _guard_state["allowed_postgres_ports"] = postgres_ports
                _guard_state["allowed_redis_ports"] = redis_ports
                _guard_state["allowed_loopback_ports"] = allowed_ports
            elif allow_local_redis is False:
                _close_redis_permission_locked()
        elif allow_loopback is False:
            _guard_state["allow_loopback"] = False
            _guard_state["allow_local_redis"] = False
            _guard_state["allowed_loopback_ports"] = frozenset()
            _guard_state["allowed_postgres_ports"] = frozenset()
            _guard_state["allowed_redis_ports"] = frozenset()
        elif allow_local_redis is False:
            _close_redis_permission_locked()

        if _guard_state["installed"]:
            return

        _original_socket_connect = socket.socket.connect
        _original_socket_connect_ex = socket.socket.connect_ex
        _original_socket_sendto = socket.socket.sendto
        _original_create_connection = socket.create_connection

        socket.socket.connect = _guarded_socket_connect
        socket.socket.connect_ex = _guarded_socket_connect_ex
        socket.socket.sendto = _guarded_socket_sendto
        socket.create_connection = _guarded_create_connection

        try:
            import ssl

            _original_ssl_connect = ssl.SSLSocket.connect
            ssl.SSLSocket.connect = _guarded_ssl_connect
        except Exception:
            _original_ssl_connect = None

        builtins.__import__ = _offline_import
        for target in _TARGET_MODULES:
            _patch_loaded_module(target)

        _guard_state["installed"] = True


def network_guards_installed() -> bool:
    return bool(_guard_state["installed"])
