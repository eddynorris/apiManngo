"""Preflight offline para preparar un despliegue de Manngo.

Este módulo es import-safe: solo usa la biblioteca estándar y nunca importa
``app``, ``config``, ``models``, dotenv, SQLAlchemy ni clientes HTTP. No lee
archivos, no abre sockets y no consulta bases de datos.

El análisis se hace únicamente sobre un mapa de entorno entregado de forma
explícita. El CLI lee ``os.environ`` solo con ``--from-process-env``; no hay
soporte de dotenv ni de archivos ``.env``.

El reporte muestra nombres de variables y estados, nunca valores sensibles.
Las comprobaciones de esquema, roles/RLS, respaldo, smoke test y rotación de
secretos quedan marcadas como ``operator_check``: este script no las verifica.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

OK = "ok"
MISSING = "missing"
ERROR = "error"
WARNING = "warning"
OPERATOR = "operator_check"

REQUIRED_VARIABLES = ("DATABASE_URL", "JWT_SECRET_KEY")

PLACEHOLDER_MARKERS = (
    "changeme",
    "change-me",
    "your-",
    "your_",
    "generate-a-",
    "example.com",
    "supabase-url",
    "user:password@",
    "password@localhost",
    "replace-me",
    "replace_me",
    "todo",
)

SUNAT_KEYS = (
    "SUNAT_ENVIO_HABILITADO",
    "SUNAT_AMBIENTE",
    "SUNAT_RUC",
    "SUNAT_USUARIO_SOL",
    "SUNAT_CLAVE_SOL",
    "MANNGO_ID_SUNAT",
    "MANNGO_CLAVE_SUNAT",
)

TELEGRAM_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_SECRET_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_BOT_USERNAME",
)

AI_KEYS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")

STORAGE_KEYS = (
    "S3_BUCKET",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "SUPABASE_URL",
    "SUPABASE_KEY",
)

OPERATOR_CHECK_IDS = (
    "alembic_schema_version",
    "database_roles_rls",
    "backup_restore",
    "smoke_test",
    "secret_rotation",
    "rollback_plan",
)


def _value(environment: Mapping[str, object], key: str) -> str:
    raw = environment.get(key, "")
    if raw is None:
        return ""
    return str(raw).strip()


def _present(environment: Mapping[str, object], key: str) -> bool:
    return bool(_value(environment, key))


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _add(checks, check_id, status, detail, variables=()):
    checks.append(
        {
            "id": check_id,
            "status": status,
            "detail": detail,
            "variables": list(variables),
        }
    )


def _positive_int(raw: str):
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _postgres_dsn_has_valid_shape(value: str) -> bool:
    """Valida host, base de datos y puerto sin abrir ninguna conexión.

    No devuelve ni registra el DSN; solo un booleano estructural.
    """

    if any(character.isspace() for character in value):
        return False

    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
        database = parts.path.lstrip("/")
    except ValueError:
        return False

    if not hostname or not database:
        return False
    if parts.netloc.endswith(":"):
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    return True


def _redis_url_has_valid_shape(value: str) -> bool:
    """Valida esquema redis(s), host y puerto sin abrir conexión ni imprimir la URL."""

    if any(character.isspace() for character in value):
        return False

    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return False

    if scheme not in {"redis", "rediss"} or not hostname:
        return False
    if parts.netloc.endswith(":"):
        return False
    if port is not None and not 1 <= port <= 65535:
        return False

    path = parts.path
    if path not in {"", "/"}:
        database = path.lstrip("/")
        if not database.isdigit():
            return False

    return True


def _database_check(environment, is_production, checks, missing):
    value = _value(environment, "DATABASE_URL")
    if not value:
        _add(
            checks,
            "database_url",
            MISSING,
            "Falta DATABASE_URL; no se muestra ningún valor.",
            ("DATABASE_URL",),
        )
        missing.append("DATABASE_URL")
        return

    if _looks_placeholder(value):
        _add(
            checks,
            "database_url",
            ERROR,
            "DATABASE_URL parece una plantilla o credencial de ejemplo; no se muestra el valor.",
            ("DATABASE_URL",),
        )
        return

    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        scheme = ""

    if scheme in {"postgresql", "postgres", "postgresql+psycopg2"}:
        if not _postgres_dsn_has_valid_shape(value):
            _add(
                checks,
                "database_url",
                ERROR,
                "DATABASE_URL está mal formada (host, base de datos o puerto inválido); no se muestra el valor.",
                ("DATABASE_URL",),
            )
            return
        _add(checks, "database_url", OK, "DATABASE_URL usa un esquema PostgreSQL válido.")
    elif scheme == "sqlite":
        status = ERROR if is_production else WARNING
        _add(
            checks,
            "database_url",
            status,
            "DATABASE_URL usa SQLite; no es apto para producción.",
            ("DATABASE_URL",),
        )
        if is_production:
            missing.append("DATABASE_URL (PostgreSQL en producción)")
    else:
        _add(
            checks,
            "database_url",
            ERROR,
            "DATABASE_URL no declara un esquema PostgreSQL reconocible; no se muestra el valor.",
            ("DATABASE_URL",),
        )


def _jwt_check(environment, checks, missing):
    value = _value(environment, "JWT_SECRET_KEY")
    if not value:
        _add(
            checks,
            "jwt_secret_key",
            MISSING,
            "Falta JWT_SECRET_KEY; no se muestra ningún valor.",
            ("JWT_SECRET_KEY",),
        )
        missing.append("JWT_SECRET_KEY")
        return

    if _looks_placeholder(value) or len(value) < 32:
        _add(
            checks,
            "jwt_secret_key",
            ERROR,
            "JWT_SECRET_KEY está presente pero es plantilla o no alcanza 32 caracteres; no se muestra el valor.",
            ("JWT_SECRET_KEY",),
        )
        return

    _add(checks, "jwt_secret_key", OK, "JWT_SECRET_KEY presente y con longitud mínima.")


def _cors_check(environment, is_production, checks, missing):
    raw = _value(environment, "ALLOWED_ORIGINS")
    origins = [item.strip() for item in raw.split(",") if item.strip()]

    if not origins:
        if is_production:
            _add(
                checks,
                "allowed_origins",
                MISSING,
                "Falta ALLOWED_ORIGINS; en producción CORS es obligatorio.",
                ("ALLOWED_ORIGINS",),
            )
            missing.append("ALLOWED_ORIGINS")
        else:
            _add(
                checks,
                "allowed_origins",
                WARNING,
                "ALLOWED_ORIGINS no definido; en desarrollo se usa el valor local por defecto.",
                ("ALLOWED_ORIGINS",),
            )
        return

    if any("*" in origin for origin in origins):
        _add(
            checks,
            "allowed_origins",
            ERROR if is_production else WARNING,
            "ALLOWED_ORIGINS contiene comodín '*'; no se permite en producción. No se muestran los orígenes.",
            ("ALLOWED_ORIGINS",),
        )
        return

    if any(not origin.startswith(("http://", "https://")) for origin in origins):
        _add(
            checks,
            "allowed_origins",
            WARNING,
            "ALLOWED_ORIGINS contiene entradas sin esquema http(s); no se muestran los orígenes.",
            ("ALLOWED_ORIGINS",),
        )
        return

    _add(
        checks,
        "allowed_origins",
        OK,
        "ALLOWED_ORIGINS definido sin comodines; no se muestran los orígenes.",
    )


def _debug_check(environment, is_production, checks):
    if is_production and _truthy(_value(environment, "FLASK_DEBUG")):
        _add(
            checks,
            "debug_mode",
            ERROR,
            "FLASK_DEBUG está activo en producción; debe estar deshabilitado.",
            ("FLASK_DEBUG",),
        )
    else:
        _add(checks, "debug_mode", OK, "FLASK_DEBUG no está activo de forma insegura.")


def _deployment_contract_check(environment, checks):
    issues = []

    port = _value(environment, "PORT")
    if port and _positive_int(port) is None:
        issues.append("PORT")

    workers = _value(environment, "GUNICORN_WORKERS")
    if workers and _positive_int(workers) is None:
        issues.append("GUNICORN_WORKERS")

    timeout = _value(environment, "GUNICORN_TIMEOUT")
    if timeout and _positive_int(timeout) is None:
        issues.append("GUNICORN_TIMEOUT")

    if issues:
        _add(
            checks,
            "deployment_contract",
            WARNING,
            "Hay variables del contrato de ejecución con formato inválido; se usan los valores por defecto de la imagen.",
            tuple(issues),
        )
    else:
        _add(
            checks,
            "deployment_contract",
            OK,
            "Contrato PORT/GUNICORN_WORKERS/GUNICORN_TIMEOUT válido o con valores por defecto de la imagen.",
        )


def _limiter_check(environment, is_production, checks, missing):
    explicit = _value(environment, "LIMITER_STORAGE_URI")
    fallback = _value(environment, "RATELIMIT_STORAGE_URI")
    value = explicit or fallback
    variable = "LIMITER_STORAGE_URI" if explicit else "RATELIMIT_STORAGE_URI"

    if not value:
        if is_production:
            _add(
                checks,
                "limiter_storage_uri",
                MISSING,
                "Falta LIMITER_STORAGE_URI (o RATELIMIT_STORAGE_URI) con redis:// o rediss://; "
                "en producción no se permite memory://.",
                ("LIMITER_STORAGE_URI", "RATELIMIT_STORAGE_URI"),
            )
            missing.append("LIMITER_STORAGE_URI")
        else:
            _add(
                checks,
                "limiter_storage_uri",
                OK,
                "Limiter no configurado; no es obligatorio fuera de producción.",
            )
        return

    if value.lower().startswith("memory://"):
        _add(
            checks,
            "limiter_storage_uri",
            ERROR if is_production else WARNING,
            "El almacenamiento del Limiter usa memory://; en producción se exige redis:// o rediss://.",
            (variable,),
        )
        return

    if not _redis_url_has_valid_shape(value):
        _add(
            checks,
            "limiter_storage_uri",
            ERROR if is_production else WARNING,
            "La URL del almacenamiento del Limiter no es una URL redis:// o rediss:// "
            "estructuralmente válida; no se muestra el valor.",
            (variable,),
        )
        return

    _add(
        checks,
        "limiter_storage_uri",
        OK,
        "Almacenamiento del Limiter configurado con redis:// o rediss://; conectividad no verificada.",
    )


def _sunat_check(environment, checks):
    enabled = _truthy(_value(environment, "SUNAT_ENVIO_HABILITADO"))
    configured = [key for key in SUNAT_KEYS if key != "SUNAT_ENVIO_HABILITADO" and _present(environment, key)]

    if enabled:
        _add(
            checks,
            "sunat",
            ERROR,
            "SUNAT_ENVIO_HABILITADO está activo; el envío SUNAT debe permanecer deshabilitado en esta preparación.",
            ("SUNAT_ENVIO_HABILITADO",),
        )
        return

    if configured:
        _add(
            checks,
            "sunat",
            WARNING,
            "Hay configuración SUNAT presente, pero el envío está deshabilitado. No se verificó nada fiscal.",
            tuple(configured),
        )
        return

    _add(
        checks,
        "sunat",
        OK,
        "Sin configuración SUNAT; envío deshabilitado por defecto.",
    )


def _telegram_check(environment, checks):
    configured = [key for key in TELEGRAM_KEYS if _present(environment, key)]
    if configured:
        _add(
            checks,
            "telegram",
            WARNING,
            "Hay credenciales de Telegram presentes; bot no verificado. No debe operarse hasta activación manual.",
            tuple(configured),
        )
        return

    _add(
        checks,
        "telegram",
        OK,
        "Telegram no configurado; bot deshabilitado por defecto.",
    )


def _ai_check(environment, checks):
    configured = [key for key in AI_KEYS if _present(environment, key)]
    if configured:
        _add(
            checks,
            "gemini",
            WARNING,
            "Hay configuración de IA presente; consumo externo no verificado.",
            tuple(configured),
        )
        return

    _add(checks, "gemini", OK, "IA no configurada; integración deshabilitada por defecto.")


def _storage_check(environment, is_production, checks, missing):
    configured = [key for key in STORAGE_KEYS if _present(environment, key)]
    if configured:
        _add(
            checks,
            "storage",
            OPERATOR,
            "Hay configuración de almacenamiento presente; acceso real y permisos no verificados por este script.",
            tuple(configured),
        )
        return

    status = WARNING if is_production else OK
    _add(
        checks,
        "storage",
        status,
        "Sin configuración de almacenamiento; las cargas de archivos podrían fallar. No verificado.",
    )
    if is_production:
        missing.append("S3_BUCKET/AWS_REGION o almacenamiento equivalente")


def _operator_checks(checks):
    details = {
        "alembic_schema_version": "Pendiente de operador: comparar la revisión Alembic actual con head. Este script no abre la base de datos.",
        "database_roles_rls": "Pendiente de operador: revisar roles, RLS y permisos remotos. Este script no consulta la base de datos.",
        "backup_restore": "Pendiente de operador: respaldo previo y restauración probada en un entorno aislado.",
        "smoke_test": "Pendiente de operador: smoke test posterior al despliegue; no ejecutado aquí.",
        "secret_rotation": "Pendiente de operador: cambios/rotación de secretos en el gestor del entorno, nunca en la imagen.",
        "rollback_plan": "Pendiente de operador: plan de rollback de imagen y de esquema revisado.",
    }
    for check_id in OPERATOR_CHECK_IDS:
        _add(checks, check_id, OPERATOR, details[check_id])


def analyze_environment(environment: Mapping[str, object], *, force_production: bool = False) -> dict:
    """Analiza un mapa de entorno explícito y devuelve un reporte sin valores."""

    env = {str(key): "" if value is None else str(value) for key, value in environment.items()}
    flask_env = env.get("FLASK_ENV", "").strip().lower()
    is_production = force_production or flask_env == "production"

    checks: list[dict] = []
    missing: list[str] = []

    if flask_env == "production":
        mode = "production"
    elif flask_env == "testing":
        mode = "testing"
    elif flask_env:
        mode = "development/no-production"
    else:
        mode = "no declarado (la imagen fija production)"

    _add(checks, "environment", OK, f"Modo detectado: {mode}.")
    _database_check(env, is_production, checks, missing)
    _jwt_check(env, checks, missing)
    _cors_check(env, is_production, checks, missing)
    _debug_check(env, is_production, checks)
    _deployment_contract_check(env, checks)
    _limiter_check(env, is_production, checks, missing)
    _sunat_check(env, checks)
    _telegram_check(env, checks)
    _ai_check(env, checks)
    _storage_check(env, is_production, checks, missing)
    _operator_checks(checks)

    errors = sum(1 for check in checks if check["status"] == ERROR)
    warnings = sum(1 for check in checks if check["status"] == WARNING)
    operators = sum(1 for check in checks if check["status"] == OPERATOR)
    blocking_missing = [name for name in REQUIRED_VARIABLES if name in missing]
    if is_production and "ALLOWED_ORIGINS" in missing:
        blocking_missing.append("ALLOWED_ORIGINS")
    if is_production and "LIMITER_STORAGE_URI" in missing:
        blocking_missing.append("LIMITER_STORAGE_URI")
    status = (
        "fail"
        if errors or blocking_missing
        else ("review" if warnings or operators else "pass")
    )

    return {
        "summary": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "operator_checks": operators,
            "production": is_production,
        },
        "missing_variables": sorted(set(missing)),
        "checks": checks,
        "notes": [
            "No se leyó .env, no se importó app y no se usaron red, HTTP ni base de datos.",
            "Los checks de operador no están verificados por este script.",
            "El reporte no contiene valores de variables de entorno.",
        ],
    }


def report_to_json(report: Mapping[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def render_text(report: Mapping[str, object]) -> str:
    labels = {
        OK: "OK",
        MISSING: "FALTA",
        ERROR: "ERROR",
        WARNING: "ADVERTENCIA",
        OPERATOR: "CHECK OPERADOR",
    }
    summary = report.get("summary", {})
    lines = [
        "Preflight de despliegue Manngo (offline, sin valores sensibles)",
        f"Estado: {summary.get('status')} | errores={summary.get('errors')} "
        f"advertencias={summary.get('warnings')} checks_operador={summary.get('operator_checks')}",
        "",
    ]

    for check in report.get("checks", []):
        variables = check.get("variables") or []
        suffix = f" [variables: {', '.join(variables)}]" if variables else ""
        lines.append(
            f"[{labels.get(check.get('status'), check.get('status'))}] "
            f"{check.get('id')}: {check.get('detail')}{suffix}"
        )

    missing = report.get("missing_variables") or []
    lines.append("")
    if missing:
        lines.append("Configuración faltante (solo nombres): " + ", ".join(missing))
    else:
        lines.append("Configuración faltante (solo nombres): ninguna detectada.")

    lines.append("No verificado por este script: esquema Alembic, roles/RLS, respaldo, smoke test, rotación de secretos y rollback.")
    lines.append("Este reporte no reemplaza la verificación del operador ni una prueba en Docker.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight offline de despliegue. No lee .env, no importa app y no usa red/BD. "
            "Debe indicarse explícitamente --from-process-env para analizar os.environ."
        )
    )
    parser.add_argument(
        "--from-process-env",
        action="store_true",
        help="Analiza os.environ del proceso actual de forma explícita.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Fuerza las comprobaciones de producción aunque FLASK_ENV no lo declare.",
    )
    parser.add_argument("--json", action="store_true", help="Emite el reporte en JSON.")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.from_process_env:
        parser.error(
            "indique --from-process-env; este script no lee .env ni archivos de secretos"
        )

    report = analyze_environment(dict(os.environ), force_production=args.production)
    if args.json:
        print(report_to_json(report))
    else:
        print(render_text(report))
    return 1 if report["summary"]["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
