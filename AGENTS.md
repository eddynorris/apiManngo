# AGENTS.md

Contexto técnico compartido para agentes de código (Claude Code, Codex, OpenCode/DeepSeek, Antigravity/Gemini).
Solo va aquí contexto duradero y verificado contra el código. Las tareas, el backlog y el estado viven en Hermes
(`taskdb.py`) y en `.claude/tasks/`. El estado de la auditoría de octubre de 2026 está en `docs/estado-implementacion-manngo.json`.

## 1. Propósito
API REST en Flask para **Manngo**, empresa de producción y venta de carbón vegetal, briquetas e insumos.
Cubre inventario multialmacén con lotes y FIFO, producción por receta (BOM) y mermas, pedidos y ventas
(contado y crédito), pagos, depósitos y cierre de caja, gastos y compras de insumos, reportes y dashboard,
proyección de recompra de clientes, un bot de Telegram con IA (Gemini) y la preparación de la Guía de
Remisión Electrónica de SUNAT (Perú). Zona horaria de negocio: `America/Lima`. El frontend Angular
(`appManngoWeb`) está en otro repositorio.

## 2. Stack (verificado en `requirements.txt` / `requirements.lock`)
- Python 3.12 en Docker (`python:3.12-slim`). El CI todavía usa 3.11 (pendiente). El `venv/` de Windows es 3.13.
- Flask 3.1 + Flask-RESTful 0.3.10, Flask-SQLAlchemy 3.1 / SQLAlchemy 2.0, Flask-Migrate 4.1 (Alembic),
  PostgreSQL alojado en Supabase (`psycopg2-binary`). Los tests usan SQLite en memoria; las pruebas de
  integración, un PostgreSQL y un Redis locales desechables.
- Marshmallow 3.26 + marshmallow-sqlalchemy; Flask-JWT-Extended 4.7 (HS256); Flask-Limiter 4.1 (Redis en
  producción); Flask-Talisman, Flask-CORS, Flask-Compress, Flasgger.
- `supabase` 2.17: Storage de archivos (`utils/file_handlers.py`) y RAG del chat. **boto3 sigue en
  requirements pero ningún módulo lo importa** (S3 es legado).
- `google-generativeai` 0.8.5, modelo `gemini-flash-lite-latest` (elegido por cuota).
- Telegram Bot API vía `requests` (`services/telegram_service.py`). SUNAT GRE con `lxml` y `signxml`
  (`services/sunat_xml.py`) y transporte en `services/sunat_service.py`.
- pandas + openpyxl (exportaciones), Pillow, pytz, redis; gunicorn en producción.
- Instalación reproducible: `requirements.lock` con hashes (`pip install --require-hashes -r requirements.lock`).

## 3. Estructura
```
app.py                Arranque a nivel de módulo (no hay create_app): valida el entorno, CORS/JWT/Limiter/Talisman, /health, registra rutas
extensions.py         db, jwt, migrate, swagger y cliente supabase (None si faltan variables)
models.py             Modelos SQLAlchemy (un solo módulo); models_stock_audit.py: MovimientoScope
schemas.py            Schemas Marshmallow (instancia singular y plural por modelo)
common.py             handle_db_errors, rol_requerido, mismo_almacen_o_admin, paginación, parse_iso_datetime, obtener_usuario_actual
config.py             Constantes (paginación, longitudes, extensiones)
resources/            Un <dominio>_resource.py por dominio; rutas en resources/__init__.py (init_resources)
services/             Lógica reutilizable: venta, pago, stock, produccion, authorization (actor/almacén/objeto),
                      money (montos positivos), storage_cleanup (outbox de borrado en Storage), gemini, telegram, sunat, sunat_xml
telegram/             Bot: router.py, state_machine.py, context.py, resolvers.py, ui.py, handlers/ (venta, pago, produccion, transferencia, consulta, guia_sunat)
utils/                date_parser.py, date_utils.py (hora de Perú), file_handlers.py (Supabase Storage), logger_config.py
migrations/           Alembic: 8 revisiones lineales desde la baseline vacía d312602d0682 (head e8f9a0b1c2d3); manual/ contiene el SQL de RLS (no se aplica solo)
scripts/              run_tests_offline.py, offline_test_env.py, deployment_preflight.py, prepare_historical_load.py (solo dry-run), sync_supabase.py
tests/                pytest (unos 60 archivos; integration/ requiere PostgreSQL/Redis locales y permiso explícito)
docs/                 Guías, notas de implementación oc-notas-*.md, PREPARACION_DESPLIEGUE.md, SUNAT_FUENTES_VERIFICADAS.md, reglas-agente-manngo.md
database/             (ignorado por git) db.sql con vistas y triggers de la BD real, entre ellas vista_clientes_proyeccion
```
`PLAN_DE_MEJORAS.md` es un plan antiguo sin conciliar con el código, no contexto técnico.

## 4. Comandos (desde la raíz del repo)
```bash
# Entorno Linux/WSL reproducible
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.lock && pip install pytest

# Tests: SIEMPRE con el runner offline (bloquea red, limpia credenciales, fuerza SQLite y testing)
python scripts/run_tests_offline.py -q
python scripts/run_tests_offline.py --runner-help
# Integración con PostgreSQL/Redis locales desechables (BD manngo_test_*, solo 127.0.0.1):
docker run -d --rm --name manngo-test-pg -e POSTGRES_PASSWORD=test-only -e POSTGRES_DB=manngo_test_local -p 127.0.0.1:55432:5432 postgres:17-alpine
docker run -d --rm --name manngo-test-redis -p 127.0.0.1:56379:6379 redis:7.4-alpine
MANNGO_RUN_PG_TESTS=1 MANNGO_ALLOW_LOCAL_POSTGRES_TESTS=1 MANNGO_ALLOW_LOCAL_REDIS_TESTS=1 \
MANNGO_LOCAL_POSTGRES_URL=postgresql://postgres:test-only@127.0.0.1:55432/manngo_test_local \
DATABASE_URL=postgresql://postgres:test-only@127.0.0.1:55432/manngo_test_local \
MANNGO_LOCAL_REDIS_URL=redis://127.0.0.1:56379/0 \
python scripts/run_tests_offline.py --allow-local-postgres --allow-local-redis -q

# Lint (el CI lo exige en verde)
ruff check .

# Telegram: procesa updates pendientes y marca los colgados (en producción, cron cada 5 min)
flask --app app telegram-pendientes

# Servidor local: requiere DATABASE_URL y JWT_SECRET_KEY (>= 32 caracteres); ver .env.example
python app.py

# Preflight de despliegue (sin red y sin importar la app)
python scripts/deployment_preflight.py --from-process-env --production

# Docker: gunicorn en :8080, usuario no root, healthcheck con http.client
docker build -t api-manngo .
```
Migraciones: `flask db upgrade` es **un paso manual del operador**, nunca se ejecuta al arrancar. La BD de
producción se creó a mano (`legacy_migrations/`, `database/db.sql`). Antes de la primera migración hay que
alinearla con la baseline (`flask db stamp d312602d0682`) tras un respaldo.

## 5. Convenciones
- Código, dominio, comentarios y mensajes de API en español.
- Resources: `XxxResource(Resource)` con `get/post/put/delete(self, id=None)`. Registrar la ruta en `init_resources`.
- Decoradores habituales: `@jwt_required()` → `@handle_db_errors` → `@rol_requerido(...)` / `@mismo_almacen_o_admin`.
  Roles: `admin`, `gerente`, `usuario`.
- Autorización por objeto con `services/authorization.py` (`autorizar_almacen`, `autorizar_venta`,
  `autorizar_pago`, `autorizar_pedido`); no reimplementar comprobaciones de almacén a mano.
- Montos con `Decimal`, validados con `services/money.monto_decimal_positivo`; no se aceptan negativos.
- Listados paginados con `page`/`per_page` y respuesta `{"data": [...], "pagination": {...}}`.
- Serializar siempre con los schemas; no devolver modelos crudos ni hashes ni campos internos de usuario.
- Errores 5xx saneados (`handle_db_errors` asigna un `error_id`); no exponer excepciones ni SQL.
- Operaciones de varias entidades (venta, detalle, kardex, pago) en una sola transacción con rollback.
- Subidas a Storage con `commit_with_storage_finalization` / `rollback_and_compensate` (`storage_cleanup_service`).
- Fechas con `utils/date_utils.py` y `utils/date_parser.py`; una fecha inválida devuelve error, no se sustituye por la de captura.
- Servicios externos como singletons de módulo (`telegram_service`, `sunat_service`, `gemini_service`).
- Ruff: E, F, W, I, B, UP; line-length 100 (`pyproject.toml`).

## 6. Estado conocido que afecta al trabajo
- `main` es lo que está en producción (Railway). Trabajar en ramas; nunca fusionar ni hacer push a `main` sin orden de Eddy.
- Telegram usa una bandeja de entrada duradera (`services/telegram_inbox.py`, tabla `telegram_updates`). El webhook
  guarda el update y responde 200, y el procesamiento es «como máximo una vez», con orden por chat. Los updates
  colgados los recupera `flask telegram-pendientes`. Requiere la migración `e8f9a0b1c2d3` antes de desplegar.
- El CI tiene dos jobs: `test` (SQLite) e `integration` (PostgreSQL 17 y Redis 7.4 desechables). Ambos deben quedar en verde.
- Los reportes filtran y agrupan por **día de negocio de Lima** (`utils.date_utils.filtro_dias_peru` y `dia_peru_sql`),
  nunca con `func.date(columna_utc)`.
- En producción `app.py` **exige** `LIMITER_STORAGE_URI` (o `RATELIMIT_STORAGE_URI`) con `redis://`/`rediss://`.
- El envío SUNAT está deshabilitado salvo `SUNAT_ENVIO_HABILITADO=true`; la XML se valida en local solo con XSD.

## 7. Zonas sensibles (requieren confirmación humana explícita)
- `.env`, `.env.local`, `.env.production`: no leer, imprimir, crear ni modificar. Usar `.env.example`.
- BD de producción (Supabase/PostgreSQL): ningún SQL, migración, `stamp`, `sync-supabase`, script de
  `legacy_migrations/` ni conexión de prueba. Solo SQLite o PostgreSQL local desechable.
- Cambios de esquema: cada cambio en `models.py` va con su migración Alembic, revisada y no aplicada.
- SUNAT: no llamar al servicio real (ni beta) ni activar `SUNAT_ENVIO_HABILITADO`. Emitir tiene efecto tributario.
- Telegram, WhatsApp, Gemini y Storage reales: no enviar mensajes, no consumir cuota y no borrar ni sobrescribir archivos.
  En tests, solo dobles.
- No relajar validaciones de arranque (CORS cerrado, longitud del JWT secret, Redis obligatorio, sin DEBUG en producción),
  autorización, RLS ni constraints; no permitir stock negativo; no inventar datos de negocio para cargas históricas.
- El contrato detallado para agentes implementadores está en `docs/reglas-agente-manngo.md`.

## 8. Verificación antes de terminar
```bash
python scripts/run_tests_offline.py -q     # la suite completa debe quedar igual o mejor que antes del cambio
ruff check <archivos tocados>             # como mínimo en lo modificado
```
- Si se añade un recurso: comprobar su registro en `resources/__init__.py`.
- Si se cambia un modelo: actualizar el schema y añadir la migración en `migrations/versions/`.
- No hacer commit, push ni despliegue sin que Eddy lo pida.
