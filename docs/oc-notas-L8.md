# Lote L8 · Rendimiento medido, límites e índice real (A15–A18)

**Estado:** implementado en fuente y tests. **No ejecutado** por límite de pasos del agente: no se lanzó pytest, Docker, migraciones ni mediciones reales. No se declaran resultados. El padre verifica en Docker.

**Alcance respetado:** solo A15/A16/A17/A18. No se tocó el harness (`scripts/offline_test_env.py`, `scripts/run_tests_offline.py`, `tests/conftest.py`), `common.py`, `AGENTS.md`, reglas, `.env`, secretos ni otro lote. No se usó producción, red de negocio, Redis real, base real, SUNAT, Gemini ni Telegram. No se hicieron commits. No se añadieron dependencias.

## Código entregado

### A15 · N+1 en listado de ventas
- `resources/venta_resource.py`
  - `_opciones_carga_ventas()` usa `joinedload` para `cliente`, `almacen` y `vendedor`.
  - `selectinload(Venta.detalles).joinedload(VentaDetalle.presentacion)` y `selectinload(Venta.pagos)` para detalles/presentaciones y saldos.
  - Aplicado al listado paginado, a `all=true` y al GET individual (`first_or_404` conserva el 404).
- `tests/test_ventas_query_budget.py`
  - Cuenta SELECT en sesión fría con 1, 10 y 20 ventas de clientes distintos.
  - No fija un presupuesto numérico inventado: exige que 10 y 20 no superen el conteo medido de 1. Un N+1 lo hace fallar.
  - Imprime `SELECTs fríos por listado de ventas: {...}` para registrar la medición real antes/después del padre.

### A16 · `all=true` y exportación acotados
- `resources/venta_resource.py`
  - `all=true`: valida el tope, ejecuta `query.count()` y solo hace `.all()` si no excede. No materializa antes de comprobar.
  - `/ventas/exportar`: idéntico control antes de `order_by(...).all()` y antes del DataFrame/Excel.
  - Respuestas conservadas: `{"data": [...]}` para `all=true`; Excel con el mismo MIME para exportación bajo el tope.
  - Exceso de volumen: **413** JSON con `limite`, `total` y sugerencia. Configuración inválida/fuera de rango: **400**.
- `app.py`
  - `MAX_VENTAS_ALL` (variable de entorno), default **1000**.
  - `MAX_VENTAS_EXPORT` (variable de entorno), default **5000**.
  - Rango aceptado en arranque: 1..1.000.000; valor inválido produce `RuntimeError` de configuración accionable.
- `tests/test_export_limits.py`
  - 413 en `all=true` sin cargar `venta_detalles` ni `pagos` antes del rechazo.
  - Contrato `{"data": [...]}` dentro del tope.
  - 413 en export sin generar archivo; Excel correcto dentro del tope; 400 con configuración 0/fuera de rango.

### A17 · Índice `pagos(venta_id)`
- `models.py`: declarado `Index('idx_pagos_venta', 'venta_id')` en `Pago.__table_args__`.
- `migrations/versions/f1a2b3c4d5e6_add_pagos_venta_index.py`:
  - `revision = 'f1a2b3c4d5e6'`, `down_revision = 'c4d5e6f7a8b9'` → **head nuevo f1a2b3c4d5e6**.
  - Usa `sa.inspect(bind).get_indexes('pagos')` y considera equivalente cualquier índice cuya **primera columna** sea `venta_id`; si existe, no crea otro.
  - Si el nombre `idx_pagos_venta` ya existe pero no es útil por `venta_id`, no duplica ni reemplaza el objeto preexistente.
  - En PostgreSQL marca el índice creado con `COMMENT ON INDEX ... 'manngo:created_by_migration:f1a2b3c4d5e6'`.
  - Downgrade solo elimina `idx_pagos_venta` si el marcador demuestra que lo creó esta migración. Si es preexistente, no hay marcador o el motor no soporta el marcador (SQLite), no lo borra; limitación documentada en la propia migración.
- No se usó base real ni se aplicó la migración.

### A18 · Flask-Limiter, Redis y ausencia de fallback silencioso
- `app.py`
  - Usa el nombre nativo **`RATELIMIT_STORAGE_URI`**; ya no configura `RATELIMIT_STORAGE_URL`.
  - `LIMITER_STORAGE_URI` conserva compatibilidad y tiene prioridad; también acepta `RATELIMIT_STORAGE_URI` desde entorno.
  - `RATELIMIT_SWALLOW_ERRORS = False` y `RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = False`; `RATELIMIT_IN_MEMORY_FALLBACK = None`. Un fallo de Redis no cae silenciosamente a memoria.
  - En producción (`FLASK_ENV=production`) exige esquema `redis://` o `rediss://` y verifica que el backend efectivo no sea `MemoryStorage`; si no, error de arranque accionable. Esto cubre el escenario de 3 workers.
  - Memoria permitida solo fuera de producción y en `FLASK_ENV=testing` explícito.
  - `crear_limiter(app)` centraliza la creación para pruebas.
- `tests/test_rate_limit_backend.py`
  - Verifica que la app usa `RATELIMIT_STORAGE_URI`, no `RATELIMIT_STORAGE_URL`, y que el fallback está deshabilitado.
  - Comprueba el backend efectivo `MemoryStorage` y `RedisStorage` sin abrir conexión.
  - Rechaza memoria/vacío/SQLite en producción; acepta `redis://` y `rediss://`.
  - Simula error de storage y verifica que la petición falla (500), sin responder 200 desde memoria.
- `tests/integration/test_rate_limit_shared_redis.py`
  - Opt-in: solo corre con `MANNGO_ALLOW_LOCAL_REDIS_TESTS=1`; sin flag se omite. Con flag, un Redis caído **no** se omite.
  - Usa `MANNGO_LOCAL_REDIS_URL` (ejemplo `redis://127.0.0.1:6379/0`) y el runner previsto `--allow-local-postgres --allow-local-redis` del harness H1 Redis (no editado aquí).
  - Dos apps Flask independientes con `Limiter`, mismo prefijo UUID de test y misma clave: dos peticiones en la primera y la tercera en la segunda deben dar **429 compartido**.
  - Limpia únicamente claves `LIMITS:<prefijo-uuid>*` con `scan_iter`; no ejecuta flush global.
  - Usa `Flask.test_client` local, sin abrir HTTP ni usar rutas/BD de usuario real.

## Versiones y variables relevantes

- `Flask-Limiter==4.1.1` (instalada en `requirements.txt`; el plan menciona 3.1.0, pero la verificación debe hacerse contra la versión fijada real).
- `redis>=5.0.0` ya existente; sin dependencias nuevas.
- `SQLAlchemy==2.0.38`, `Flask-SQLAlchemy==3.1.1` según `requirements.txt`.
- Variables nuevas/efectivas:
  - `MAX_VENTAS_ALL` (default 1000).
  - `MAX_VENTAS_EXPORT` (default 5000).
  - `LIMITER_STORAGE_URI` / `RATELIMIT_STORAGE_URI` (producción: `redis://` o `rediss://`).
  - Integración: `MANNGO_ALLOW_LOCAL_REDIS_TESTS=1`, `MANNGO_LOCAL_REDIS_URL=redis://127.0.0.1:6379/0`.

## Resultados no ejecutados

- **No ejecutado:** pytest, runner offline, Docker, migraciones, `flask db`, conexiones PostgreSQL/Redis, medición real antes/después y pruebas de contadores compartidos.
- **No se declaran passed, failures ni números de SQL reales.** El test de presupuesto imprime la medición para que el padre la registre.
- No se aplicó `f1a2b3c4d5e6`; no se comprobó el plan de consultas ni la ausencia de duplicados en una base viva.

## Pendiente del padre

1. Ejecutar en Docker el runner con los tests nuevos:
   `scripts/run_tests_offline.py --no-bytecode --no-cache -q tests/test_ventas_query_budget.py tests/test_export_limits.py tests/test_rate_limit_backend.py`
2. Medir SQL antes/después con 1/10/20 ventas y clientes distintos; registrar los conteos reales.
3. Verificar el índice real en PostgreSQL autorizado con inspector/pg_indexes y confirmar que `f1a2b3c4d5e6` es head único tras `c4d5e6f7a8b9`.
4. Con Redis local levantado y el harness H1 Redis disponible, ejecutar la integración con `MANNGO_ALLOW_LOCAL_REDIS_TESTS=1`, `MANNGO_LOCAL_REDIS_URL=redis://127.0.0.1:6379/0` y `--allow-local-postgres --allow-local-redis`; confirmar 429 compartido y limpieza solo de claves propias.
5. Configurar Redis real para producción antes de desplegar. SUNAT real y bot de Telegram siguen pendientes/deshabilitados.
6. Recordar al usuario rotar/cambiar sus secretos reales en sus `.env`; este lote no los leyó ni editó. No se usó producción, dotenv, secretos, red de negocio ni commits.
