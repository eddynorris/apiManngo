# Notas de implementación — Lote D1 (preparación de despliegue)

## Alcance ejecutado

Solo preparación de artefactos de despliegue, sin desplegar. Propiedad exclusiva:

- `Dockerfile`
- `.dockerignore`
- `scripts/deployment_preflight.py` (nuevo)
- `tests/test_deployment_artifacts.py` (nuevo)
- `docs/PREPARACION_DESPLIEGUE.md` (nuevo)
- `docs/oc-notas-D1.md` (este archivo)

No se modificaron `app.py`, `common.py`, `config.py`, `models.py`, `requirements.txt`, `requirements.lock`, servicios, recursos, migraciones, tests existentes ni RLS. No se añadieron secretos. La API no cambió.

No se usó producción, base remota, Supabase/Storage real, Gemini, Telegram ni SUNAT. No se emitieron documentos fiscales. No se hicieron commits, push, reset, clean ni stash. No se leyeron `.env`, `.env.local` ni `.env.production`.

## Cambios y decisiones

### Dockerfile

- Build y runtime en `python:3.12-slim`.
- Dependencias desde `requirements.lock` con `--require-hashes` en el builder, verificando los archivos originales (sdists/wheels), con `pip check`.
- Venv absoluto `/opt/venv` en builder; runtime copia `/opt/venv` y usa `PATH=/opt/venv/bin:$PATH`. Sin segunda instalación ni wheels recompilados.
- Runtime no root: `appuser`/`appgroup` con UID/GID 10001.
- `COPY` explícito de fuentes runtime: `app.py`, `common.py`, `config.py`, `extensions.py`, `models.py`, `schemas.py`, `models_stock_audit.py` (creado por L3; no se editó su contenido), `resources/`, `services/`, `telegram/`, `utils/`, dos scripts necesarios y `migrations/`.
- Sin `COPY . .`, sin `pip install -r requirements.txt`, sin `gcc`/`postgresql-client` en runtime, sin `curl`.
- Healthcheck con `http.client` de la stdlib; consulta `/health` en `127.0.0.1:$PORT` con timeout de 3 s.
- Gunicorn con contrato explícito `PORT`, `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, `--threads 2`, `--graceful-timeout 30`.
- CMD en forma shell con `exec gunicorn`: el shell se reemplaza para que Gunicorn sea PID 1 y reciba SIGTERM. No es un `exec` de Python ni un paso de arranque; no hay `db.create_all`, `flask db`, migraciones, sincronización ni import de la app durante el build.
- `migrations/` se copia para revisión/operación, pero no se ejecuta al arrancar.

### .dockerignore

- Excluye todos los `.env` reales (`.env`, `.env.*`, `**/.env`, `**/.env.*`) y conserva solo `!.env.example`.
- Excluye `tests/`, `database/`, `legacy_migrations/`, dumps/backups SQL, certificados y claves, venvs, logs, temporales, `uploads/`, cachés y herramientas locales (`.hermes/`, `.archify/`, `.claude/`, `.agents/`, `.cursor/`, `.trae/`, `.kombai/`, `.vercel/`, `.playwright/`, `.github/`).
- El padre debe construir desde una copia sin `.env` y verificar la imagen; este lote no construyó nada.

### scripts/deployment_preflight.py

- Import-safe, solo stdlib. No importa `app`, `config`, `models`, `extensions`, dotenv, Flask, SQLAlchemy, HTTP ni clientes de integraciones.
- No lee archivos ni usa red/BD. El CLI solo lee `os.environ` con `--from-process-env`; no soporta dotenv.
- Analiza `DATABASE_URL` (esquema/plantilla y estructura host/base/puerto, sin imprimir el valor ni abrir conexión), `JWT_SECRET_KEY` (presencia/longitud/plantilla, sin valor), `ALLOWED_ORIGINS` (sin comodín en producción, sin imprimir orígenes), `FLASK_DEBUG` y contrato de ejecución.
- En producción exige `LIMITER_STORAGE_URI` o `RATELIMIT_STORAGE_URI` con estructura `redis://`/`rediss://`; `memory://` y faltante bloquean. No imprime la URL ni verifica conectividad.
- Integraciones nuevas por defecto off: SUNAT deshabilitado; Telegram y Gemini solo se reportan si hay credenciales presentes. No se editó configuración de otros módulos.
- Emite nombres de variables faltantes, no valores. No rellena valores privados.
- Esquema Alembic, roles/RLS, respaldo/restauración, smoke test, rotación de secretos y rollback quedan como `operator_check`; el script no los verifica ni afirma que estén verificados.
- Salida de texto y JSON; exit `1` si hay errores o falta una variable obligatoria. Un faltante obligatorio ya no queda como `review`.

### tests/test_deployment_artifacts.py

Cubre, sin importar `app` directamente:

- Dockerfile usa el lock con hashes sobre archivos originales, `pip check`, venv `/opt/venv` y sin segunda instalación desde wheels recompilados; no usa `requirements.txt`.
- Healthcheck con `http.client`, sin `curl`.
- Python 3.12, usuario no root, `COPY` explícito de `models_stock_audit.py` y `CMD exec gunicorn` para PID 1/SIGTERM.
- Ausencia de `db.create_all`, migraciones, sync o import de app en build/arranque.
- `.dockerignore` excluye `.env` reales, tests, dumps, certificados, logs, venvs y artefactos; conserva solo `.env.example` como plantilla.
- Preflight import-safe (AST sin imports prohibidos), sin `load_dotenv`.
- Reporte sin valores secretos, con faltantes por nombre y bloqueo cuando falta una variable obligatoria.
- `DATABASE_URL` mal formada (vacía, sin host, sin base, puerto inválido) marcada como error sin registrar el DSN.
- Comodín en `ALLOWED_ORIGINS` en producción marcado como error.
- Plantillas/credenciales de ejemplo y JWT débil marcados como error.
- Integraciones deshabilitadas por defecto y SUNAT con envío activo marcado como error.
- Limiter Redis obligatorio en producción: faltante, `memory://` o URL mal formada marcados como bloqueantes; fallback `RATELIMIT_STORAGE_URI` aceptado.
- Checks de operador presentes y no afirmados como verificados.

## Comandos y resultados reales

- Lectura de archivos con herramientas de lectura/edición. No hubo ejecución de shell.
- **No ejecutado:** `pytest`, `ruff`, build Docker, `docker run`, import de `app`, `flask db`, conexiones de BD, preflight contra entorno real, despliegue y smoke test. El agente no dispone de herramienta de shell en esta sesión.
- **No hay resultados de pruebas que reportar como propios.** El padre debe construir la imagen desde una copia sin `.env` y ejecutar `tests/test_deployment_artifacts.py` en Docker; no se inventan resultados.
- **Resultado real previo 1:** `15 passed, 1 failed` en D1. El fallo fue `test_preflight_reports_missing_names_without_values`, que esperaba `summary.status == "fail"` y recibió `review`.
- **Corrección 1 aplicada:** resumen bloqueante con faltantes obligatorios (`DATABASE_URL`, `JWT_SECRET_KEY`, `ALLOWED_ORIGINS` en producción), validación estructural de `DATABASE_URL`, `exec` en el CMD y `COPY` de `models_stock_audit.py`.
- **Resultado real previo 2:** los 20 tests de D1 pasaron. El build real del padre falló en runtime al instalar hashes desde wheels recompilados: `flasgger==0.9.7.1` conserva el hash del archivo fuente en el lock, pero el wheel generado por el builder tiene otro hash legítimo.
- **Corrección 2 aplicada:** builder crea `/opt/venv`, instala `requirements.lock` con `--require-hashes` sobre los archivos originales y ejecuta `pip check`; runtime copia `/opt/venv` con la misma ruta absoluta y Python 3.12. No se recompilan wheels para una segunda instalación ni se alteró el lock. Se añadió el check obligatorio de Limiter Redis en producción. La nueva ejecución de tests/build **no se ejecutó aquí**; queda pendiente de verificación del padre.

## Bloqueos y límites

- El lock `requirements.lock` no se modificó. El build del padre debe confirmar la nueva estrategia de venv y que `pip check` pasa en Python 3.12 slim.
- El build real falló por el hash de `flasgger` recompilado; no se añadieron hashes ficticios ni se relajó `--require-hashes`.
- El healthcheck depende de `/health`, que a su vez consulta la base de datos; un fallo de BD puede marcar el contenedor como no saludable. Es el contrato actual.
- El CMD usa `exec gunicorn` en forma shell: Gunicorn reemplaza al shell y queda como PID 1 para recibir SIGTERM. Esto no ejecuta Python `exec`, migraciones ni inicialización de BD.
- No se verificó el contenido final de la imagen; `.dockerignore` y `COPY` explícito son la preparación, no una prueba de que no haya secretos en la imagen.
- RLS/roles/permisos remotos no se aplicaron ni se consultaron. SUNAT sigue deshabilitado y el bot de Telegram no está listo; no deben activarse.
- Recordatorio: los secretos reales del usuario deben cambiarse/rotarse en el gestor del entorno; nunca en la imagen ni en el repositorio.
