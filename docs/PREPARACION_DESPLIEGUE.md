# Preparación de despliegue — Manngo API

Documento de preparación. **No se desplegó nada, no se construyó ninguna imagen, no se aplicaron migraciones y no se ejecutaron pruebas desde este lote.** La verificación real corresponde al padre en Docker y al operador.

## 1. Alcance y límites

- Solo se preparan artefactos: `Dockerfile`, `.dockerignore`, `scripts/deployment_preflight.py`, `tests/test_deployment_artifacts.py` y esta documentación.
- No se modifican `app.py`, `common.py`, `config.py`, `models.py`, `requirements.txt`, `requirements.lock`, servicios, recursos ni tests existentes. La API se mantiene igual.
- No se leyeron `.env`, `.env.local`, `.env.production`, certificados, dumps ni secretos reales.
- No se usó producción, PostgreSQL remoto, Supabase/Storage real, Gemini, Telegram ni SUNAT. No se emitió ningún documento fiscal.
- No se hicieron commits, push, reset, clean ni stash.

## 2. Contrato de la imagen

| Elemento | Decisión |
|---|---|
| Base | `python:3.12-slim` en build y runtime |
| Dependencias | Instaladas desde `requirements.lock` con `--require-hashes` en el builder, verificando los archivos originales (sdists/wheels) del lock |
| Build | Venv absoluto `/opt/venv` en builder + `pip check`; runtime copia ese venv; sin segunda instalación ni wheels recompilados |
| Limiter | En producción exige `LIMITER_STORAGE_URI` (o `RATELIMIT_STORAGE_URI`) con `redis://` o `rediss://`; no se acepta `memory://` |
| Usuario | Runtime no root (`appuser`, UID/GID 10001) |
| Copia | Solo fuentes runtime necesarias con `COPY` explícito; incluye `models_stock_audit.py` (creado por L3); sin `COPY . .` |
| Migraciones | `migrations/` se copia para revisión/operación, pero **no se ejecuta nada al arrancar** |
| Efectos secundarios | Sin `db.create_all()`, `flask db upgrade`, `sync-supabase` ni import de `app` durante el build |
| Healthcheck | `http.client` de la stdlib de Python; no usa `curl` |
| Arranque | Gunicorn con contrato explícito `PORT`, `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT` |
| Forma del CMD | Shell-form con `exec`: Gunicorn reemplaza al shell y es PID 1 para recibir SIGTERM; sin pasos de inicialización de BD |
| Puerto por defecto | `8080` |
| `EXPOSE` | `8080` (metadato; el bind real usa `PORT`) |

El healthcheck consulta `/health`. Ese endpoint ya existía y comprueba base de datos; por tanto un fallo de BD puede marcar el contenedor como no saludable. Es el comportamiento actual de la API, no se cambió.

## 3. Contexto de build

`.dockerignore` excluye, entre otros:

- Todos los `.env` reales (`.env`, `.env.*`, `**/.env`, `**/.env.*`), dejando solo `.env.example` como plantilla.
- `tests/`, `database/`, `legacy_migrations/`, dumps `*.sql`, `*.dump`, `*.backup`, `*.bak`.
- Certificados y claves: `*.pem`, `*.key`, `*.crt`, `*.cer`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `certs/`, `certificates/`, `secrets/`, `.ssh/`.
- Herramientas y artefactos locales: `.hermes/`, `.archify/`, `.claude/`, `.agents/`, `.cursor/`, `.trae/`, `.kombai/`, `.vercel/`, `.playwright/`, `.github/`.
- Venvs, cachés, logs, temporales, `uploads/` y `__pycache__/`.

El padre debe construir desde una copia del contexto que **no contenga `.env` reales** y verificar el contenido de la imagen resultante. Este lote no hizo esa construcción.

## 4. Checklist previo al despliegue (operador)

Marcar cada punto como verificado por el operador. El preflight no verifica ninguno de estos puntos.

### 4.1 Imagen y build
- [ ] Construir desde contexto sin `.env` reales.
- [ ] Confirmar que la imagen no contiene `.env`, dumps, tests, certificados ni claves.
- [ ] Confirmar usuario no root y que el proceso no corre como UID 0.
- [ ] Confirmar que la instalación usó `requirements.lock` con hashes sobre los archivos originales y que `pip check` pasó.
- [ ] Confirmar que el runtime usa `/opt/venv` y que no hubo una segunda instalación desde wheels recompilados.
- [ ] Confirmar `LIMITER_STORAGE_URI` (o `RATELIMIT_STORAGE_URI`) con `redis://`/`rediss://` en producción; `memory://` no se acepta.
- [ ] Confirmar que el healthcheck responde sin `curl`.
- [ ] Confirmar que `PORT`, `GUNICORN_WORKERS` y `GUNICORN_TIMEOUT` se respetan al sobrescribirlos.

### 4.2 Respaldo y restauración
- [ ] Tomar respaldo de la base antes de cualquier migración.
- [ ] Probar la restauración en un entorno aislado, nunca en producción.
- [ ] Confirmar que el respaldo no se versionó ni se subió al repositorio.
- [ ] Registrar ubicación y responsable del respaldo.

### 4.3 Migraciones
- [ ] Revisar las revisiones en `migrations/versions/` y el orden esperado.
- [ ] Comparar revisión actual con head con una conexión autorizada y aislada.
- [ ] Ejecutar la migración como paso explícito del operador, **no** desde el arranque del contenedor.
- [ ] Confirmar que la aplicación no crea tablas ni sincroniza datos al iniciar.
- [ ] Confirmar que `migrations/manual/20261022_backend_only_rls.sql` no se aplica en este lote.

### 4.4 Rollback
- [ ] Tener identificada la imagen anterior y el comando de rollback.
- [ ] Definir rollback de esquema (down migration o restauración) antes de migrar.
- [ ] Confirmar que el rollback no reutiliza secretos filtrados.
- [ ] Probar el rollback en entorno aislado.

### 4.5 Smoke test posterior
- [ ] `/health` responde `200` con base conectada.
- [ ] Login y una consulta autenticada funcionan.
- [ ] Una operación de solo lectura de inventario/ventas responde correctamente.
- [ ] Los logs no muestran secretos, DSN completos ni datos personales.
- [ ] No se emite ninguna guía, factura ni documento fiscal.

### 4.6 Cambios de secretos
- [ ] Cargar secretos únicamente en el gestor de variables del entorno.
- [ ] Rotar `JWT_SECRET_KEY` según la política del negocio (esto invalida tokens emitidos con la clave anterior).
- [ ] Rotar credenciales de base de datos, almacenamiento y cualquier integración activada.
- [ ] Confirmar que ningún secreto quedó en la imagen, el repositorio, logs o artefactos.
- [ ] Recordatorio: el usuario debe cambiar sus secretos reales después de este lote.

### 4.7 Activación manual de integraciones
- [ ] SUNAT permanece deshabilitado (`SUNAT_ENVIO_HABILITADO` ausente o falso).
- [ ] No se activa Telegram: el bot **no está listo** y no debe operarse ni emitir.
- [ ] Gemini/IA permanece sin configurar salvo decisión explícita.
- [ ] Cualquier activación futura requiere revisión humana, credenciales propias y pruebas con dobles o entornos aislados.

## 5. Preflight offline

Uso previsto, sin dotenv y sin importar la app:

```bash
python scripts/deployment_preflight.py --from-process-env
python scripts/deployment_preflight.py --from-process-env --json
python scripts/deployment_preflight.py --from-process-env --production
```

El script:

- Solo analiza el mapa de entorno recibido; con el CLI, `os.environ` se usa únicamente si se pasa `--from-process-env`.
- No lee `.env`, no importa `app`, no usa HTTP, red ni base de datos.
- Comprueba presencia de `DATABASE_URL`, `JWT_SECRET_KEY` (sin imprimir valores), `ALLOWED_ORIGINS` sin comodín en producción y el contrato `PORT`/`GUNICORN_WORKERS`/`GUNICORN_TIMEOUT`.
- Valida estructuralmente `DATABASE_URL` PostgreSQL (host, base de datos y puerto cuando corresponda) sin abrir conexión ni registrar el DSN. Una URL mal formada o vacía bloquea (estado `fail`), igual que un error.
- En producción exige `LIMITER_STORAGE_URI` o `RATELIMIT_STORAGE_URI` con estructura `redis://`/`rediss://`; falta o `memory://` bloquean. No imprime la URL ni verifica conectividad.
- Marca integraciones nuevas deshabilitadas por defecto y advierte si hay credenciales presentes.
- Marca esquema Alembic, roles/RLS, respaldo/restauración, smoke test, rotación de secretos y rollback como `operator_check`. **No afirma que estén verificados.**
- Devuelve nombres de variables faltantes y estados; nunca rellena ni imprime valores privados.

Código de salida: `1` si hay errores o falta alguna variable obligatoria; `0` si solo hay advertencias o checks de operador.

## 6. Estado de ejecución de este lote

- Lectura de `docs/reglas-agente-manngo.md`, `Dockerfile`, `.dockerignore`, `requirements.txt`, `requirements.lock` (encabezado), `app.py`, `config.py`, `.env.example`, tests y documentación existente.
- Creación/edición de los seis artefactos de propiedad: `Dockerfile`, `.dockerignore`, `scripts/deployment_preflight.py`, `tests/test_deployment_artifacts.py`, `docs/PREPARACION_DESPLIEGUE.md` y `docs/oc-notas-D1.md`.
- **No ejecutado:** build Docker, tests, `pytest`, `ruff`, import de `app`, migraciones, conexión a base de datos, preflight real contra un entorno, despliegue y smoke test. El entorno de este agente no dispone de shell.
- **Pendiente del padre:** construir la imagen desde una copia sin `.env`, ejecutar `tests/test_deployment_artifacts.py`, revisar el contexto/imagen y verificar el preflight con un entorno controlado.
- **Corrección posterior 1:** el padre reportó `15 passed, 1 failed` para D1 (el fallo era `test_preflight_reports_missing_names_without_values`: devolvía `review` en vez de `fail`). Se corrigió el resumen para que faltantes obligatorios bloqueen, se añadió `exec` a Gunicorn y la validación estructural de `DATABASE_URL`.
- **Resultado real siguiente:** los 20 tests de D1 pasaron; el build real del padre falló al instalar hashes desde wheels recompilados (`flasgger` cambia de hash al compilar).
- **Corrección posterior 2:** el builder ahora instala en `/opt/venv` con `--require-hashes` sobre los archivos originales y ejecuta `pip check`; runtime copia `/opt/venv`. Se añadió el check obligatorio de Limiter Redis en producción. La nueva ejecución de tests/build **no se ejecutó aquí** y queda pendiente del padre.

## 7. Cierre obligatorio: RLS y permisos remotos

**RLS, roles de base de datos y permisos remotos quedan explícitamente al final y NO se ejecutaron en este lote.** No se aplicó `migrations/manual/20261022_backend_only_rls.sql`, no se consultaron roles remotos y no se modificó ninguna política. Cualquier aplicación o auditoría de RLS requiere autorización humana y un entorno aislado.

**SUNAT permanece deshabilitado y no se emitió nada.** El bot de Telegram no está listo: no debe activarse ni operarse todavía.
