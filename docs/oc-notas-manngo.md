# Notas de implementación — Manngo

## Lote L0 · Bootstrap y runner seguro de pruebas

**Estado:** corrección de aislamiento de sesión aplicada y suite ejecutada localmente con el runner seguro. El padre mantiene la verificación independiente en Docker.

**Alcance respetado:** solo L0. No se implementó L1, SUNAT, pagos, autorización de ventas ni carga histórica. No se usó producción, ni dotenv real, ni Supabase/Storage/Gemini/Telegram/SUNAT reales. No se modificaron reglas de negocio, dependencias ni archivos `.env`.

### Archivos modificados/creados

- `scripts/run_tests_offline.py` (nuevo): runner portable Windows/Linux.
- `scripts/offline_test_env.py` (nuevo): configuración de entorno, validación de URL de BD, limpieza de integraciones y bloqueo de red/HTTP/IA.
- `tests/test_test_environment_safety.py` (nuevo): regresiones de seguridad del entorno, incluida la limpieza de variables libpq `PG*`.
- `tests/test_conftest_session_isolation.py` (nuevo): regresión de aislamiento de sesión entre tests del mismo módulo.
- `app.py`: no carga dotenv en modo testing/offline; valida `DATABASE_URL` antes de `db.init_app`.
- `extensions.py`: no crea cliente Supabase en modo testing/offline.
- `tests/conftest.py`: prepara entorno y bloquea red antes de importar `app`; limpia rollback/remove de la sesión del `app_context` vigente antes y después de cada test, sin abrir un contexto anidado.
- `docs/oc-notas-manngo.md` (nuevo): este registro.

### Qué hace el runner

1. Establece `FLASK_ENV=testing` y `MANNGO_OFFLINE_TESTS=1`.
2. Usa `sqlite:///:memory:` por defecto si no hay `DATABASE_URL`.
3. Limpia variables de integraciones (Supabase, Google/Gemini, Telegram, SUNAT, AWS/S3, Redis/Limiter, OpenAI) y todas las variables libpq `PG*` (`PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`, `PGPASSWORD`, etc.) sin imprimir sus valores.
4. Valida `DATABASE_URL` con parser SQLAlchemy (`make_url`), no con búsquedas de substring.
5. Rechaza bases de prueba no desechables antes de importar pytest/app:
   - SQLite solo en memoria.
   - PostgreSQL solo si se autoriza explícitamente, el host es literal loopback y la base empieza con `manngo_test`.
6. Bloquea sockets salientes, clientes HTTP (`requests`, `httpx`, `urllib`, `http.client`, `urllib3`) y llamadas reales a Gemini/Supabase/boto3 antes de los imports de la aplicación.
7. Excluye siempre `tests/test_supabase_storage_integration.py`, que hace operaciones reales de Storage.
8. Pasa argumentos desconocidos a pytest.
9. Opcionalmente desactiva bytecode (`--no-bytecode`) y caché de pytest (`--no-cache`).

### Comando portable exacto

Con el entorno virtual activo, desde la raíz del proyecto:

```bash
python scripts/run_tests_offline.py --no-bytecode --no-cache -q
```

Equivalentes por shell si `python` no resuelve al venv:

```powershell
.\venv\Scripts\python.exe scripts\run_tests_offline.py --no-bytecode --no-cache -q
```

```bash
./venv/bin/python scripts/run_tests_offline.py --no-bytecode --no-cache -q
```

Verificación de protección sin ejecutar pytest ni importar la app:

```bash
python scripts/run_tests_offline.py --check-only --no-bytecode
```

Salida esperada (resumen seguro, sin valores de credenciales): `database=sqlite-memory; integrations_cleared=N; dotenv=disabled; loopback=blocked`.

### Controles de seguridad verificables

- Si `DATABASE_URL` apunta a un host remoto en testing, el runner termina con código 2 y mensaje sin credenciales.
- `tests/test_test_environment_safety.py` comprueba:
  - limpieza de integraciones, variables libpq `PG*` y secreto JWT sintético;
  - rechazo de PostgreSQL remoto, SQLite de archivo, hosts con `localhost` como substring y overrides por query;
  - aceptación de PostgreSQL loopback con `manngo_test*` solo en modo explícito;
  - ausencia de credenciales en mensajes de error;
  - bloqueo de `socket`, `requests`, `urllib` y Gemini;
  - exclusión del test de Storage real y desactivación de caché;
  - import de `app` en subproceso que confirma `UnsafeTestDatabaseError` sin que se llame a `SQLAlchemy.init_app`.
- `app.py` aplica la validación antes de `db.init_app`, no después de cargar dotenv.
- `.env`, `.env.local` y `.env.production` no fueron leídos, editados ni impresos.

### Corrección de aislamiento de sesión

- El padre reportó en Docker `--network none`: 136 casos, 133 passed y 3 errores de setup en `tests/test_pedido_conversion_resource.py`.
- Causa reproducida: el fixture módulo `test_client` mantiene abierto un `app_context`; `clean_db_session` abría otro contexto y limpiaba otra scoped session. Las identidades stale del contexto exterior reutilizaban IDs y fallaban al commit de presentaciones, derivando en `PendingRollbackError`.
- Corrección mínima: `_clean_active_db_session()` hace rollback/remove sobre la sesión del contexto vigente con `has_app_context()`, antes y después de cada test. No abre contextos anidados.
- No se modificaron aserciones, fixtures originales ni reglas de dominio.
- Regresión: `tests/test_conftest_session_isolation.py` captura la sesión del contexto de módulo y verifica que el siguiente test recibe una sesión distinta.

### Pruebas

- **Ejecutadas localmente (Windows, venv):** `.\venv\Scripts\python.exe scripts/run_tests_offline.py --no-bytecode --no-cache -q --disable-warnings`.
- **Resultado real:** `139 passed, 198 warnings in 83.75s (0:01:23)`. Incluye los 136 casos previos más 3 regresiones nuevas (2 de aislamiento de sesión y 1 de variables libpq `PG*`).
- **No se ejecutó** `python app.py`, `flask`, dotenv real ni conexiones PostgreSQL. El runner usó `sqlite:///:memory:` y bloqueó red.
- **Pendiente del padre:** verificación independiente en Docker y revisión del diff.
- **Limitaciones:** el bloqueo de sockets es de proceso y no sustituye un cortafuegos. SQLite no valida locks ni concurrencia PostgreSQL. La opción PostgreSQL local queda preparada y validada por parser, pero no se abrió ninguna conexión en este lote.

### Decisiones y bloqueos

- Se excluyó `tests/test_supabase_storage_integration.py` de forma transparente porque realiza `upload`, descarga y borrado reales.
- No se añadieron dependencias.
- No se modificaron aserciones ni archivos de tests existentes; solo `tests/conftest.py` para el bootstrap seguro. No se añadieron `skip`/`xfail` para ocultar fallos.
- Si el shell ya exporta un `DATABASE_URL` remoto, el runner lo rechazará por diseño. Hay que desexportarlo o ejecutar con el entorno limpio; no se sustituye silenciosamente por SQLite.
- Recordatorio: el usuario debe rotar/cambiar sus secretos reales en los `.env` fuera de este lote; no se tocaron.

## Lote L4 · Revocación de identidad y minimización de respuestas (A05/A20/A21)

**Estado:** implementado en fuente y tests. **No ejecutado** por restricción del encargo (sin shell/red); sin resultados de pytest ni migraciones. El padre mantiene la verificación independiente en Docker.

**Alcance respetado:** solo L4/A05/A20/A21. No se implementó L5, L7, SUNAT, pagos, stock, RLS ni carga histórica. No se tocaron `services/authorization.py`, recursos de stock/pago, `tests/conftest.py`, `scripts/run_tests_offline.py` ni políticas RLS. No se usó producción, dotenv real, red ni secretos.

### Archivos modificados/creados

- `models.py`: columna `Users.auth_version` (entero, no nulo, default 0).
- `schemas.py`: `UserSchema` con `password` solo de carga y exclusión de `auth_version` y campos de Telegram.
- `common.py`: resolución del usuario vigente por el ID numérico del subject; `rol_requerido` y `mismo_almacen_o_admin` usan el rol/almacén actuales de BD. Ya no convierten excepciones de negocio o de BD en 401.
- `resources/auth_resource.py`: el token de login incluye el claim `auth_version`.
- `resources/user_resource.py`: incrementa `auth_version` al cambiar contraseña, rol o almacén.
- `resources/telegram_webhook_resource.py`: vinculación resuelve el subject por ID estricto en BD; no por username. Genera un código que no pertenezca a otra cuenta.
- `app.py`: callback `token_in_blocklist_loader` que revoca tokens de usuarios borrados, versiones obsoletas o claims de rol/almacén desactualizados; loader de token revocado.
- `migrations/versions/b7c1d2e3f4a5_add_user_auth_version.py` (nuevo): migración reversible; agrega `auth_version` con `server_default='0'` para filas existentes.
- `tests/test_auth_revocation.py`, `tests/test_user_sensitive_fields.py`, `tests/test_telegram_link_identity.py` (nuevos): regresiones de los tres hallazgos.

### Decisiones de diseño y compatibilidad

- **Versión de autorización, no JTI/blacklist persistente:** cada login emite `auth_version`; cualquier cambio de contraseña, rol o almacén la incrementa. Los tokens anteriores dejan de ser válidos de inmediato.
- **Tokens legacy:** `create_access_token` conserva su firma. Un token sin claim `auth_version` se acepta solo mientras `Users.auth_version == 0`; tras el primer cambio sensible queda rechazado para siempre. No existe excepción para testing ni bypass por claims. Así los tests antiguos que generan tokens sin la nueva claim siguen funcionando sobre usuarios sin cambios de estado. Los tokens con `sub` no numérico se rechazan (por `JWT_VERIFY_SUB` o por la resolución estricta del callback, según la versión), es decir, fallan de forma segura.
- **Rol/almacén/username vigentes:** además de la versión, si el token trae `username`, `rol` o `almacen_id`, se comparan contra la BD. Un token legacy con claims obsoletos se revoca; un claim `rol=admin` falso no otorga acceso. La comparación de `username` evita que un token viejo con username numérico se interprete como el ID de otra cuenta. Los decoradores resuelven el usuario por ID en BD y autorizan con su rol/almacén actuales.
- **Usuario borrado:** el callback no encuentra la fila y revoca el token (401).
- **Errores:** los decoradores no capturan excepciones amplias; solo la verificación JWT produce 401. Los errores de negocio/HTTP se propagan con su semántica (o llegan a `handle_db_errors`/manejadores Flask).
- **A20:** `password` es `load_only`; `auth_version`, `telegram_chat_id`, `telegram_context`, `telegram_history`, `telegram_linking_code` y `telegram_linking_expires` se excluyen de GET/POST/PUT. Login y actualización siguen funcionando porque el campo se acepta al cargar.
- **A21:** el subject se interpreta como ID entero positivo y se busca con `db.session.get(Users, id)`. Se probó colisión con un username numérico de otra cuenta y usuario borrado.
- **Migración:** no se ejecutó. Debe aplicarse (`flask db upgrade`) antes de desplegar el nuevo código en una BD real; el `server_default='0'` cubre las filas existentes. Los tests usan `db.create_all()`, que ya crea la columna.

### Pruebas (no ejecutadas)

- Comando previsto por el runner existente: `python scripts/run_tests_offline.py --no-bytecode --no-cache -q tests/test_auth_revocation.py tests/test_user_sensitive_fields.py tests/test_telegram_link_identity.py`.
- Cobertura prevista: revocación tras democión, cambio de contraseña, cambio de almacén y borrado; token legacy aceptado con versión 0 y rechazado tras cambio; claims de username/rol/almacén obsoletos; claim admin falso; propagación de errores de negocio/HTTP; ausencia de hash/PIN/contexto en GET/POST/PUT; vinculación por ID con username numérico ajeno y rechazo de usuario borrado.
- **Resultado real:** no ejecutado. No se declaran passed.

### Limitaciones y bloqueos

- Cambios directos en BD que no incrementen `auth_version` solo se detectan por comparación de claims si el token los incluye; la aplicación incrementa la versión en la ruta de actualización de usuarios.
- La migración no se aplicó ni se probó en PostgreSQL en este lote (restricción de shell/red).
- La vinculación de un chat de Telegram ya asociado a otra cuenta pertenece al flujo del bot (L5); aquí solo se garantiza que el endpoint no resuelva ni mute otra cuenta por username.
- Recordatorio: rotar/cambiar secretos reales en los `.env` fuera de este lote.

## Lote L7 · Fechas estrictas y trazabilidad de voz

**Estado:** implementado en fuente y tests. **No ejecutado** por restricción del encargo (shell no habilitado para este agente); sin resultados reales de pytest. El padre mantiene la verificación independiente en Docker. No se implementó L8 ni L5.

**Alcance respetado:** solo L7 de fechas/log de voz. No se modificaron `app.py`, `common.py`, `models.py`, `schemas.py`, recursos de auth/stock/venta, `services/venta_service.py` ni reglas de stock. No se usó producción, dotenv real, red, Gemini/Telegram reales ni secretos.

### Archivos modificados/creados

- `utils/date_parser.py`: helpers de Telegram ahora lanzan `ValueError` ante fecha presente inválida; ausente usa hoy Perú; fecha-día es día calendario Perú. Se agregó `parse_fecha_operacion` estricto (ausente/null/vacío/inválido distinto): ISO con zona conserva instante; ISO/hora sin zona se interpreta Perú y se devuelve UTC; `YYYY-MM-DD` es medianoche Perú.
- `resources/transaccion_resource.py`: fecha explícita inválida/null/vacía responde 400 antes de tocar stock/venta/pago/gasto/movimiento; fecha ausente usa ahora. El gasto asociado usa el día calendario Perú.
- `resources/compra_insumo_resource.py`: misma semántica estricta para `fecha`; inválida responde 400 sin inventario, movimiento ni gasto; compra válida usa día/momento Perú. Se quitó `detalle: str(e)` de la respuesta 500.
- `resources/voice_resource.py`: el `ComandoVozLog` se escribe y confirma en una `Session` propia, independiente de la sesión del request, sin depender de una operación comercial posterior ni confirmar cambios ajenos.
- `tests/test_historical_dates.py` (nuevo): fecha inválida con cero efectos, ausente, null/vacío, límites medianoche Lima/UTC, fecha diaria de compra, ISO con zona, Telegram y persistencia del día Perú.
- `tests/test_voice_log_persistence.py` (nuevo): log de voz leído con una sesión ORM independiente tras hacer rollback de la sesión del request; usa doble de Gemini, sin audio ni IA real.

### Decisiones y compatibilidad

- Se conservó el formato existente `YYYY-MM-DD` de Telegram/Gemini; no se inventó soporte `DD/MM/YYYY`.
- `to_peru_time` no se modificó: mantiene su contrato legado de asumir naive como UTC para no romper reportes.
- La sesión independiente del log es real (no un doble). En SQLite en memoria el pool puede compartir conexión, por lo que la prueba demuestra persistencia y lectura independiente, no aislamiento de locks; en PostgreSQL la sesión usa otra conexión del pool.
- Migraciones: no hubo cambios de esquema.

### Pruebas (no ejecutadas)

- Comando previsto: `python scripts/run_tests_offline.py --no-bytecode --no-cache -q tests/test_historical_dates.py tests/test_voice_log_persistence.py tests/test_compra_insumo_resource.py tests/test_pago_validation.py`.
- **Resultado real:** no ejecutado. No se declaran `passed`.
- Recordatorio: rotar/cambiar secretos reales en los `.env` fuera de este lote.
