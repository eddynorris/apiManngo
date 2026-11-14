import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.offline_test_env import (
    ALLOW_LOCAL_POSTGRES_ENV,
    DEFAULT_TEST_DATABASE_URL,
    EXCLUDED_STORAGE_TEST,
    SYNTHETIC_JWT_SECRET,
    OfflineNetworkBlocked,
    UnsafeTestDatabaseError,
    build_pytest_args,
    clear_integration_environment,
    install_offline_guards,
    network_guards_installed,
    prepare_test_environment,
    should_skip_dotenv,
    validate_testing_database_url,
)


def test_prepare_environment_sets_disposable_defaults_and_clears_integrations():
    env = {
        "DATABASE_URL": "",
        "FLASK_ENV": "production",
        "JWT_SECRET_KEY": "real-looking-secret-that-must-not-survive",
        "SUPABASE_URL": "https://remote.invalid",
        "SUPABASE_KEY": "sentinel-not-a-secret",
        "GOOGLE_API_KEY": "sentinel-not-a-secret",
        "TELEGRAM_BOT_TOKEN": "sentinel-not-a-secret",
        "SUNAT_CLAVE_SOL": "sentinel-not-a-secret",
        "AWS_SECRET_ACCESS_KEY": "sentinel-not-a-secret",
        "LIMITER_STORAGE_URI": "redis://remote.invalid:6379/0",
    }

    status = prepare_test_environment(env)

    assert env["FLASK_ENV"] == "testing"
    assert env["MANNGO_OFFLINE_TESTS"] == "1"
    assert env["DATABASE_URL"] == DEFAULT_TEST_DATABASE_URL
    assert env["JWT_SECRET_KEY"] == SYNTHETIC_JWT_SECRET
    assert len(env["JWT_SECRET_KEY"]) >= 32
    assert status.database_kind == "sqlite-memory"
    assert status.dotenv_disabled is True
    assert status.allow_loopback is False
    for removed_key in (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "GOOGLE_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "SUNAT_CLAVE_SOL",
        "AWS_SECRET_ACCESS_KEY",
        "LIMITER_STORAGE_URI",
    ):
        assert removed_key not in env
        assert removed_key in status.removed_integration_keys


def test_clear_integration_environment_reports_names_only():
    env = {
        "SUPABASE_URL": "sentinel-not-a-secret",
        "GOOGLE_API_KEY": "sentinel-not-a-secret",
        "JWT_SECRET_KEY": "keep-me",
        ALLOW_LOCAL_POSTGRES_ENV: "1",
    }

    removed = clear_integration_environment(env)

    assert "SUPABASE_URL" in removed
    assert "GOOGLE_API_KEY" in removed
    assert env["JWT_SECRET_KEY"] == "keep-me"
    assert env[ALLOW_LOCAL_POSTGRES_ENV] == "1"


def test_clear_integration_environment_removes_libpq_overrides():
    env = {
        "PGHOST": "evil.example",
        "PGHOSTADDR": "203.0.113.10",
        "PGPORT": "5432",
        "PGDATABASE": "production",
        "PGUSER": "admin",
        "PGPASSWORD": "sentinel-not-a-secret",
        "PGPASSFILE": "/tmp/pgpass",
        "PGSERVICE": "production",
        "PGSERVICEFILE": "/tmp/pg_service.conf",
        "PGOPTIONS": "-c search_path=public",
        "PGSSLMODE": "require",
        "KEEP_ME": "visible",
    }

    removed = clear_integration_environment(env)

    for key in (
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGPASSFILE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
        "PGSSLMODE",
    ):
        assert key not in env
        assert key in removed
    assert env["KEEP_ME"] == "visible"


def test_dotenv_is_skipped_in_testing_and_offline_modes():
    assert should_skip_dotenv({"FLASK_ENV": "testing"}) is True
    assert should_skip_dotenv({"MANNGO_OFFLINE_TESTS": "1"}) is True
    assert should_skip_dotenv({"FLASK_ENV": "production"}) is False


def test_validate_accepts_sqlite_memory_variants():
    assert validate_testing_database_url("sqlite:///:memory:") == "sqlite-memory"
    assert validate_testing_database_url("sqlite://") == "sqlite-memory"
    assert validate_testing_database_url("sqlite+pysqlite:///:memory:") == "sqlite-memory"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///manngo_test_local.db",
        "sqlite:////tmp/localhost.db",
        "sqlite:///remote?host=localhost",
        "postgresql://user:supersecret@evil.example/remote_db",
        "postgresql://user:supersecret@localhost.evil/manngo_test_db",
        "postgresql://user:supersecret@127.0.0.1.evil/manngo_test_db",
        "postgresql://localhost@evil.example/manngo_test_db",
        "mysql://user@localhost/manngo_test_db",
    ],
)
def test_validate_rejects_remote_or_file_databases(url):
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_database_url(url)


def test_prepare_rejects_remote_database_without_silent_fallback():
    remote_url = "postgresql://user:supersecret@evil.example/manngo_test_db"
    env = {"DATABASE_URL": remote_url}

    with pytest.raises(UnsafeTestDatabaseError) as error:
        prepare_test_environment(env)

    assert env["DATABASE_URL"] == remote_url
    assert "supersecret" not in str(error.value)


def test_validate_rejects_remote_url_hidden_with_sqlite_text():
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_database_url(
            "postgresql://user:supersecret@evil.example/manngo_test_db?dbname=sqlite_localhost"
        )


def test_local_postgres_requires_explicit_mode():
    url = "postgresql://user@127.0.0.1:5432/manngo_test_audit"
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_database_url(url, env={})


def test_local_postgres_requires_disposable_database_prefix():
    url = "postgresql://user@127.0.0.1:5432/manngo_production"
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_database_url(
            url,
            env={ALLOW_LOCAL_POSTGRES_ENV: "1"},
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user@127.0.0.1:5432/manngo_test_audit",
        "postgresql://user@localhost:5432/manngo_test_audit",
        "postgresql://user@[::1]:5432/manngo_test_audit",
    ],
)
def test_local_postgres_accepts_only_literal_loopback_with_explicit_mode(url):
    assert (
        validate_testing_database_url(url, env={ALLOW_LOCAL_POSTGRES_ENV: "1"})
        == "postgresql-loopback-disposable"
    )


def test_local_postgres_rejects_query_host_override():
    url = "postgresql://user@127.0.0.1:5432/manngo_test_audit?host=evil.example"
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_database_url(url, env={ALLOW_LOCAL_POSTGRES_ENV: "1"})


def test_unsafe_database_error_does_not_leak_credentials():
    url = "postgresql://user:supersecret@evil.example/manngo_test_db"
    with pytest.raises(UnsafeTestDatabaseError) as error:
        validate_testing_database_url(url)

    message = str(error.value)
    assert "supersecret" not in message
    assert "evil.example" not in message


def test_offline_guards_are_installed_before_tests():
    install_offline_guards()
    assert network_guards_installed() is True


def test_socket_connections_are_blocked():
    install_offline_guards()
    with pytest.raises(OfflineNetworkBlocked):
        socket.create_connection(("127.0.0.1", 9), timeout=0.01)
    with pytest.raises(OfflineNetworkBlocked):
        socket.socket().connect(("127.0.0.1", 9))


def test_requests_are_blocked():
    import requests

    with pytest.raises(OfflineNetworkBlocked):
        requests.get("http://127.0.0.1:9/never-called")


def test_urllib_requests_are_blocked():
    from urllib.request import urlopen

    with pytest.raises(OfflineNetworkBlocked):
        urlopen("http://127.0.0.1:9/never-called", timeout=0.01)


def test_real_gemini_calls_are_blocked():
    genai = pytest.importorskip("google.generativeai")

    with pytest.raises(OfflineNetworkBlocked):
        genai.embed_content(model="models/text-embedding-004", content="offline probe")


def test_pytest_args_exclude_real_storage_test_and_disable_cache():
    args = build_pytest_args(
        [
            "-q",
            "tests/test_supabase_storage_integration.py",
            "tests/test_dashboard_resource.py",
        ],
        no_cache=True,
    )

    assert "-q" in args
    assert "tests/test_dashboard_resource.py" in args
    assert not any(
        not arg.startswith("--ignore=")
        and Path(arg.split("::", 1)[0].replace("\\", "/")).name == EXCLUDED_STORAGE_TEST
        for arg in args
    )
    assert any(
        arg.startswith("--ignore=") and arg.endswith(EXCLUDED_STORAGE_TEST) for arg in args
    )
    assert args[-2:] == ["-p", "no:cacheprovider"]


def test_pytest_args_remove_explicit_ignore_pair_for_storage_test():
    args = build_pytest_args(
        ["--ignore", "tests/test_supabase_storage_integration.py", "-x"],
    )

    assert "-x" in args
    assert "--ignore" not in args
    assert any(
        arg.startswith("--ignore=") and arg.endswith(EXCLUDED_STORAGE_TEST) for arg in args
    )


def test_app_rejects_remote_database_before_db_init_app():
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    clear_integration_environment(env)
    env.update(
        {
            "FLASK_ENV": "testing",
            "MANNGO_OFFLINE_TESTS": "1",
            "DATABASE_URL": "postgresql://user:supersecret@evil.example/manngo_test_db",
            "JWT_SECRET_KEY": SYNTHETIC_JWT_SECRET,
            "PYTHONIOENCODING": "utf-8",
        }
    )

    code = """
import flask_sqlalchemy

called = {"init_app": False}
original_init_app = flask_sqlalchemy.SQLAlchemy.init_app


def tracking_init_app(self, app, **kwargs):
    called["init_app"] = True
    return original_init_app(self, app, **kwargs)


flask_sqlalchemy.SQLAlchemy.init_app = tracking_init_app

try:
    import app
except Exception as exc:
    print("REJECTED", type(exc).__name__, str(exc))
    print("INIT_CALLED", called["init_app"])
    raise

print("NO_ERROR")
print("INIT_CALLED", called["init_app"])
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repository_root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "REJECTED UnsafeTestDatabaseError" in output
    assert "INIT_CALLED False" in output
    assert "supersecret" not in output
    assert "evil.example" not in output
