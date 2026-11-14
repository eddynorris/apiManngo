import ast
import json
import re
from pathlib import Path

from scripts.deployment_preflight import analyze_environment, render_text, report_to_json

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
PREFLIGHT = ROOT / "scripts" / "deployment_preflight.py"


def _dockerignore_patterns():
    patterns = []
    for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _check(report, check_id):
    for check in report["checks"]:
        if check["id"] == check_id:
            return check
    raise AssertionError(f"No existe el check {check_id}")


def _valid_production_env():
    return {
        "FLASK_ENV": "production",
        "DATABASE_URL": "postgresql://app_user:strong-password@db.internal:5432/manngo",
        "JWT_SECRET_KEY": "j" * 64,
        "ALLOWED_ORIGINS": "https://app.manngo.example",
        "PORT": "8080",
        "GUNICORN_WORKERS": "3",
        "GUNICORN_TIMEOUT": "120",
        "LIMITER_STORAGE_URI": "redis://:redis-synthetic-password@redis.internal:6379/0",
        "S3_BUCKET": "manngo-prod-bucket",
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "AKIA-EXAMPLE-KEY",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-value",
    }


def test_dockerfile_installs_locked_dependencies_with_hashes():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert not re.search(r"pip\s+[^\n]*requirements\.txt", dockerfile)
    assert "requirements.txt" not in dockerfile
    assert "python -m venv /opt/venv" in dockerfile
    assert "/opt/venv/bin/python -m pip install" in dockerfile
    assert "/opt/venv/bin/python -m pip check" in dockerfile
    assert "COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv" in dockerfile
    assert 'PATH="/opt/venv/bin:${PATH}"' in dockerfile
    assert dockerfile.count("--require-hashes") == 1
    assert dockerfile.count("pip install") == 1
    assert "pip wheel" not in dockerfile
    assert "find-links" not in dockerfile


def test_dockerfile_healthcheck_uses_python_stdlib_only():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "HEALTHCHECK" in dockerfile
    assert "http.client" in dockerfile
    assert "curl" not in dockerfile.lower()


def test_dockerfile_runs_as_non_root_python312():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert dockerfile.count("FROM python:3.12-slim") == 2
    assert "python:3.11" not in dockerfile
    assert re.search(r"^USER\s+appuser\s*$", dockerfile, re.MULTILINE)
    assert "useradd" in dockerfile


def test_dockerfile_gunicorn_uses_exec_to_receive_sigterm_as_pid1():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "CMD exec gunicorn" in dockerfile


def test_dockerfile_has_no_build_time_app_or_database_side_effects():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8").lower()

    forbidden = (
        "db.create_all",
        "create_all(",
        "flask db",
        "alembic upgrade",
        "sync-supabase",
        "python app.py",
        "import app",
    )
    for fragment in forbidden:
        assert fragment not in dockerfile, f"El Dockerfile no debe contener: {fragment}"


def test_dockerfile_copies_runtime_sources_explicitly():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    for source in (
        "app.py",
        "common.py",
        "config.py",
        "extensions.py",
        "models.py",
        "schemas.py",
        "models_stock_audit.py",
        "./resources",
        "./services",
        "./telegram",
        "./utils",
        "./scripts/",
        "./migrations",
    ):
        assert source in dockerfile, f"Falta COPY explícito para {source}"
    assert "COPY . ." not in dockerfile


def test_dockerignore_excludes_real_env_files_and_private_artifacts():
    patterns = set(_dockerignore_patterns())

    required = {
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        "!.env.example",
        "tests/",
        "database/",
        "legacy_migrations/",
        "*.sql",
        "*.dump",
        "*.backup",
        "*.pem",
        "*.key",
        "*.crt",
        "*.p12",
        "certs/",
        "secrets/",
        ".hermes/",
        ".archify/",
        ".claude/",
        ".agents/",
        "logs/",
        "venv/",
        ".venv/",
        "__pycache__/",
        "*.log",
        ".github/",
    }
    missing = sorted(required - patterns)
    assert not missing, f"Faltan patrones en .dockerignore: {missing}"


def test_dockerignore_negations_are_limited_to_template_and_migrations():
    patterns = _dockerignore_patterns()
    negations = {pattern for pattern in patterns if pattern.startswith("!")}

    assert "!.env.example" in negations
    assert negations <= {"!.env.example", "!migrations/**/*.sql"}


def test_preflight_module_does_not_import_app_dotenv_or_network_libraries():
    tree = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {
        "app",
        "config",
        "models",
        "extensions",
        "dotenv",
        "flask",
        "sqlalchemy",
        "requests",
        "urllib3",
        "httpx",
        "psycopg2",
        "boto3",
        "supabase",
        "telegram",
        "google",
    }
    assert not (imported & forbidden)
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "load_dotenv" not in source


def test_preflight_never_prints_secret_values():
    env = _valid_production_env()
    secret_fragments = (
        "strong-password",
        "app_user",
        "db.internal",
        "j" * 64,
        "aws-secret-value",
        "AKIA-EXAMPLE-KEY",
        "redis-synthetic-password",
        "redis.internal",
    )
    report = analyze_environment(env)

    rendered = render_text(report) + report_to_json(report)
    for fragment in secret_fragments:
        assert fragment not in rendered


def test_preflight_reports_missing_names_without_values():
    report = analyze_environment({"FLASK_ENV": "production"})
    rendered = render_text(report) + json.dumps(report)

    assert "DATABASE_URL" in report["missing_variables"]
    assert "JWT_SECRET_KEY" in report["missing_variables"]
    assert report["summary"]["status"] == "fail"
    assert "no se muestra" in rendered.lower()


def test_preflight_flags_wildcard_origins_in_production():
    env = _valid_production_env()
    env["ALLOWED_ORIGINS"] = "*"

    report = analyze_environment(env)
    check = _check(report, "allowed_origins")

    assert check["status"] == "error"
    assert report["summary"]["status"] == "fail"
    assert check["variables"] == ["ALLOWED_ORIGINS"]
    assert "https://" not in render_text(report)


def test_preflight_rejects_malformed_database_url_without_leaking_dsn():
    malformed_values = (
        "postgresql://",
        "postgresql://user:supersecret@/manngo",
        "postgresql://user:supersecret@db.internal",
        "postgresql://user:supersecret@db.internal:no-port/manngo",
        "postgresql://user:supersecret@db.internal:70000/manngo",
        "postgresql://user:supersecret@db.internal:/manngo",
    )

    for index, malformed in enumerate(malformed_values):
        env = _valid_production_env()
        env["DATABASE_URL"] = malformed
        report = analyze_environment(env)
        rendered = render_text(report) + report_to_json(report)

        assert _check(report, "database_url")["status"] == "error", f"caso {index}"
        assert report["summary"]["status"] == "fail", f"caso {index}"
        assert malformed not in rendered, f"caso {index}"
        assert "supersecret" not in rendered, f"caso {index}"


def test_preflight_accepts_postgresql_without_explicit_port():
    env = _valid_production_env()
    env["DATABASE_URL"] = "postgresql://app_user:strong-password@db.internal/manngo"

    report = analyze_environment(env)

    assert _check(report, "database_url")["status"] == "ok"


def test_preflight_blocks_blank_required_values():
    report = analyze_environment(
        {
            "FLASK_ENV": "production",
            "DATABASE_URL": "",
            "JWT_SECRET_KEY": "",
        }
    )

    assert _check(report, "database_url")["status"] == "missing"
    assert _check(report, "jwt_secret_key")["status"] == "missing"
    assert report["summary"]["status"] == "fail"


def test_preflight_rejects_placeholder_database_and_weak_jwt():
    env = _valid_production_env()
    env["DATABASE_URL"] = "postgresql://user:password@supabase-url:5432/manngo_db"
    env["JWT_SECRET_KEY"] = "short"

    report = analyze_environment(env)

    assert _check(report, "database_url")["status"] == "error"
    assert _check(report, "jwt_secret_key")["status"] == "error"


def test_preflight_requires_redis_limiter_in_production_and_rejects_memory():
    env = _valid_production_env()
    env.pop("LIMITER_STORAGE_URI")

    report = analyze_environment(env)
    assert _check(report, "limiter_storage_uri")["status"] == "missing"
    assert report["summary"]["status"] == "fail"

    for malformed in ("memory://", "redis://", "redis://redis.internal:not-a-port/0"):
        env["LIMITER_STORAGE_URI"] = malformed
        report = analyze_environment(env)
        assert _check(report, "limiter_storage_uri")["status"] == "error"
        assert report["summary"]["status"] == "fail"


def test_preflight_accepts_ratelimit_storage_uri_fallback():
    env = _valid_production_env()
    env.pop("LIMITER_STORAGE_URI")
    env["RATELIMIT_STORAGE_URI"] = "rediss://redis.internal:6380/0"

    report = analyze_environment(env)

    assert _check(report, "limiter_storage_uri")["status"] == "ok"


def test_preflight_keeps_new_integrations_disabled_by_default():
    report = analyze_environment(_valid_production_env())

    assert _check(report, "sunat")["status"] == "ok"
    assert _check(report, "telegram")["status"] == "ok"
    assert _check(report, "gemini")["status"] == "ok"


def test_preflight_flags_sunat_sending_enabled():
    env = _valid_production_env()
    env["SUNAT_ENVIO_HABILITADO"] = "1"
    env["SUNAT_AMBIENTE"] = "produccion"
    env["SUNAT_RUC"] = "20123456789"

    report = analyze_environment(env)

    assert _check(report, "sunat")["status"] == "error"
    assert "20123456789" not in report_to_json(report)


def test_preflight_marks_schema_roles_and_restore_as_operator_checks():
    report = analyze_environment(_valid_production_env())

    for check_id in (
        "alembic_schema_version",
        "database_roles_rls",
        "backup_restore",
        "smoke_test",
        "secret_rotation",
        "rollback_plan",
    ):
        check = _check(report, check_id)
        assert check["status"] == "operator_check"
        assert "no" in check["detail"].lower() or "pendiente" in check["detail"].lower()

    assert report["summary"]["operator_checks"] >= 6
    assert "No verificado por este script" in render_text(report)


def test_preflight_output_is_stable_json_without_values():
    report = analyze_environment(_valid_production_env())
    payload = json.loads(report_to_json(report))

    assert set(payload) == {"summary", "missing_variables", "checks", "notes"}
    assert payload["summary"]["status"] == "review"
    assert payload["missing_variables"] == []
