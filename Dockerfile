# syntax=docker/dockerfile:1

# --- Build Stage ---
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Solo dependencias de compilación; no se ejecuta la aplicación ni se toca la BD.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencias resueltas y verificadas por hash. El lock es la única fuente.
# Se instala en un venv absoluto /opt/venv verificando los archivos originales
# (sdists/wheels) del lock. No se recompilan wheels para una segunda instalación.
COPY requirements.lock .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /opt/venv/bin/python -m pip check

# --- Runtime Stage ---
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLASK_APP=app.py \
    FLASK_ENV=production \
    FLASK_DEBUG=0 \
    PORT=8080 \
    GUNICORN_WORKERS=3 \
    GUNICORN_TIMEOUT=120

# Usuario no root fijo y directorio de trabajo propiedad del usuario runtime.
RUN groupadd --system --gid 10001 appgroup \
    && useradd --system --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app \
    && chown appuser:appgroup /app

WORKDIR /app

# Copia el venv ya verificado en /opt/venv. No hay segunda instalación ni
# instalación desde wheels recompilados. La ruta absoluta es la misma en ambas
# etapas y la base Python 3.12 es idéntica.
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Solo fuentes runtime necesarias. No se copia el contexto completo ni .env reales.
COPY --chown=appuser:appgroup app.py common.py config.py extensions.py models.py schemas.py models_stock_audit.py ./
COPY --chown=appuser:appgroup resources ./resources
COPY --chown=appuser:appgroup services ./services
COPY --chown=appuser:appgroup telegram ./telegram
COPY --chown=appuser:appgroup utils ./utils
COPY --chown=appuser:appgroup scripts/sync_supabase.py scripts/offline_test_env.py ./scripts/
# Migraciones incluidas para revisión/operación; NO se ejecutan al arrancar.
COPY --chown=appuser:appgroup migrations ./migrations

USER appuser

EXPOSE 8080

# Healthcheck con stdlib de Python: no requiere cliente HTTP externo.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import http.client, os; c = http.client.HTTPConnection('127.0.0.1', int(os.environ.get('PORT', '8080')), timeout=3); c.request('GET', '/health'); r = c.getresponse(); r.read(); raise SystemExit(0 if r.status == 200 else 1)"]

# Contrato explícito: PORT, GUNICORN_WORKERS y GUNICORN_TIMEOUT configurables.
# Shell-form con exec: Gunicorn reemplaza al shell y queda como PID 1 para
# recibir SIGTERM. No se ejecutan pasos de arranque que creen tablas, apliquen
# migraciones o sincronicen contra la base de datos.
CMD exec gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS}" \
    --threads 2 \
    --timeout "${GUNICORN_TIMEOUT}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --logger-class utils.gunicorn_logging.LoggerSinSecretos \
    --error-logfile - \
    app:app
