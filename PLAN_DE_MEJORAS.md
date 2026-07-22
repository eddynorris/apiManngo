# PLAN DE MEJORAS — API Manngo (`eddynorris/apiManngo`)

> **Fecha de análisis:** 21 de julio de 2026
> **Commit analizado:** `2b14595` — "Correcion de FIFO en inventario"
> **Stack:** Flask 2.3.2 + Flask-RESTful + SQLAlchemy 2.0 + Marshmallow + JWT-Extended + Flask-Limiter + Talisman + Flasgger · PostgreSQL (Supabase) · Bot de Telegram + Gemini (IA) · Supabase Storage · SUNAT GRE · Docker/Gunicorn
> **Propósito del documento:** plan de mejoras **autosuficiente y accionable**. Cada mejora incluye el problema con archivo:línea reales, fragmentos del código actual, la solución paso a paso con snippets listos para adaptar, criterios de aceptación y dependencias. Puede ser implementado por cualquier desarrollador o agente de IA sin necesidad de repetir este análisis.

---

## 1. Resumen ejecutivo

API Manngo es un backend de gestión comercial (ventas, inventario multi-almacén con lotes FIFO, pagos/depósitos, producción por recetas, transferencias, gastos, reportes) con dos interfaces de IA: un **bot de Telegram** y un **módulo de voz**, ambos apoyados en **Gemini**, además de integración con **SUNAT** para guías de remisión electrónicas.

El código está razonablemente organizado (resources por dominio, algunas capas de servicio como `PagoService` y `TransferenciaService`), pero el análisis identificó **57 mejoras**, de las cuales **8 son críticas**:

1. **Sin migraciones de base de datos versionadas** — no existe carpeta `migrations/`; Flask-Migrate está en `requirements.txt` pero no se usa. El esquema real (documentado en `indexes.md`) **ya divergió del modelo ORM**: la BD tiene un índice único `(presentacion_id, almacen_id)` en `inventario`, mientras el modelo declara `(presentacion_id, almacen_id, lote_id)`. Ese desfase **rompe el inventario multi-lote (FIFO)** en cuanto se intente insertar un segundo lote de la misma presentación.
2. **Condiciones de carrera en el descuento de stock** — el FIFO de ventas (`venta_resource.py`), el ensamblaje de producción y las transferencias descuentan inventario **sin `SELECT … FOR UPDATE`**; dos ventas simultáneas pueden vender el mismo stock.
3. **Vínculo Venta↔Movimiento por texto** — los movimientos de inventario se asocian a la venta mediante `Movimiento.motivo LIKE 'Venta ID: {id}%'`, un antipatrón que produce **colisiones** (la venta 12 "matchea" los movimientos de la 123, 124…) y escaneos completos de tabla. En el `PUT /ventas` esto puede **corromper el inventario al editar una venta**.
4. **N+1 severo con `Cliente.saldo_pendiente`** — es una `property` Python que itera `ventas` y `pagos`; se serializa en cada listado de clientes y en el formulario de ventas (que además carga **todos** los clientes), generando cientos de consultas por request.
5. **Secretos y credenciales débiles por defecto** — `app.py` hay datos de SUNAT (RUC, placa, DNI del chofer) incrustados en el código del bot.
6. **Webhook de Telegram síncrono y sin defensa** — procesa Gemini + BD dentro del request del webhook (riesgo de timeout → reintentos de Telegram → **operaciones duplicadas**), no valida el header secreto de Telegram, no deduplica `update_id` y está exento del rate limiting.
7. **Fuga de detalles internos en errores** — `handle_db_errors` (`common.py`) y varios endpoints devuelven `str(e)` al cliente, exponiendo SQL, rutas y estructura interna.
8. **Ausencia de tests reales** — el único archivo de pruebas es un script de integración de 1 154 líneas sin framework (0 funciones `test_`), que además altera el esquema con `ALTER TABLE` manuales, evidenciando el problema de migraciones.

La duplicación de la lógica de negocio de ventas en **tres lugares** (API REST, bot de Telegram y módulo de voz) es el problema arquitectónico de fondo: cualquier corrección debe aplicarse tres veces y ya existen divergencias (el bot usa `datetime.now()` naive, permite stock negativo y crea clientes con teléfono ficticio).

### Distribución de mejoras por prioridad

| Prioridad | Cantidad | IDs |
|---|---|---|
| **Crítica** | 8 | BD-01, BD-02, VEN-01, VEN-02, SEG-01, SEG-02, TG-01, PERF-01 |
| **Alta** | 15 | ARQ-01, ARQ-02, BD-03, BD-04, VEN-03, VEN-04, INV-01, PROD-01, TG-02, TG-03, SEG-03, SEG-04, SEG-05, CAL-01, DEV-01 |
| **Media** | 27 | ARQ-03, ARQ-04, BD-05, BD-06, BD-07, VEN-05, VEN-06, INV-02, INV-03, PROD-02, TG-04, TG-05, TG-06, TG-08, GEM-01, GEM-02, GEM-03, SEG-06, SEG-07, SEG-08, SEG-09, PERF-02, PERF-03, CAL-02, CAL-03, DEV-02, DEV-03 |
| **Baja** | 7 | ARQ-05, PROD-03, TG-07, GEM-04, SEG-10, CAL-04, DEV-04 |

**Total: 57 mejoras.**

---

## 2. Tabla completa de mejoras

| ID | Título | Prioridad | Esfuerzo | Área |
|---|---|---|---|---|
| ARQ-01 | Capa de servicios de dominio unificada (ventas/pagos compartidos por REST, bot y voz) | Alta | 5-8 días | Arquitectura |
| ARQ-02 | Descomponer la God Class `TelegramWebhookResource` (2 217 líneas) | Alta | 3-5 días | Arquitectura |
| ARQ-03 | Eliminar invocación interna de resources vía `test_request_context` y `mock.patch` | Media | 1-2 días | Arquitectura |
| ARQ-04 | Application Factory (`create_app`) para testabilidad y configuración por entorno | Media | 1-2 días | Arquitectura |
| ARQ-05 | Blueprints, versionado `/api/v1` y formato de error estándar | Baja | 2-3 días | Arquitectura |
| BD-01 | Adoptar migraciones versionadas con Flask-Migrate/Alembic | Crítica | 1-2 días | Base de Datos |
| BD-02 | Corregir desfase del constraint único de `inventario` (multi-lote roto) | Crítica | 0.5-1 día | Base de Datos |
| BD-03 | Añadir FK `movimientos.venta_id` y eliminar el vínculo por `motivo LIKE` | Alta | 1-2 días | Base de Datos |
| BD-04 | Crear índices faltantes (ventas, venta_detalles, movimientos, gastos, pagos) | Alta | 0.5 día | Base de Datos |
| BD-05 | Unificar tipos numéricos: `VentaDetalle.cantidad` Integer vs `Inventario.cantidad` Numeric | Media | 1 día | Base de Datos |
| BD-06 | `Inventario.ultima_actualizacion` con `onupdate` automático | Media | 0.25 día | Base de Datos |
| BD-07 | Constraints CHECK de no-negatividad en stock y montos | Media | 0.5 día | Base de Datos |
| TG-01 | Webhook asíncrono + deduplicación de `update_id` + respuesta 200 inmediata | Crítica | 2-3 días | Bot Telegram |
| TG-02 | `_execute_deposito` afecta pagos de todos los usuarios/almacenes: filtrar por ámbito | Alta | 0.5 día | Bot Telegram |
| TG-03 | El bot debe usar los servicios de dominio (eliminar lógica duplicada de ventas/pagos) | Alta | 3-4 días | Bot Telegram |
| TG-04 | Fechas naive (`datetime.now()`) en `_execute_venta`/`_execute_ventas_lote`: usar UTC | Media | 0.5 día | Bot Telegram |
| TG-05 | Cachear catálogos (almacenes/presentaciones) usados en cada mensaje | Media | 0.5-1 día | Bot Telegram |
| TG-06 | `telegram_service`: reintentos con backoff, manejo de errores y `escape` HTML | Media | 1 día | Bot Telegram |
| TG-07 | Mover `telegram_history`/`telegram_context` de columnas JSON del usuario a tabla propia | Baja | 1-2 días | Bot Telegram |
| TG-08 | No autocrear clientes con teléfono ficticio en ventas por lote | Media | 0.5 día | Bot Telegram |
| GEM-01 | Fijar la versión del modelo Gemini y hacerla configurable | Media | 0.25 día | Servicio Gemini |
| GEM-02 | Registrar auditoría `ComandoVozLog` también desde el bot de Telegram | Media | 0.5 día | Servicio Gemini |
| GEM-03 | Inicialización perezosa del cliente Gemini + endpoint de health | Media | 0.5 día | Servicio Gemini |
| GEM-04 | Reintentos con jitter no bloqueantes y presupuesto de latencia | Baja | 0.5 día | Servicio Gemini |
| VEN-01 | FIFO de ventas con bloqueo pesimista (`with_for_update`) | Crítica | 1-2 días | Ventas |
| VEN-02 | Reescribir la reversión de inventario del `PUT /ventas` (pierde lotes) | Crítica | 2-3 días | Ventas |
| VEN-03 | Usar `venta_id` real en movimientos al crear/editar/borrar ventas | Alta | 1 día | Ventas |
| VEN-04 | Eliminar `?all=true` sin límite y añadir eager loading al listado de ventas | Alta | 0.5-1 día | Ventas |
| VEN-05 | `VentaFormDataResource`: no volcar todos los clientes con `saldo_pendiente` | Media | 0.5 día | Ventas |
| VEN-06 | Política explícita de stock insuficiente (hoy el bot vende en negativo) | Media | 1 día | Ventas |
| INV-01 | Eliminar N+1 de `InventarioGlobalResource` (consulta agregada única) | Alta | 0.5-1 día | Inventario |
| INV-02 | Módulo único de FIFO (`services/stock_service.py`) reutilizado por todos los flujos | Media | 2-3 días | Inventario |
| INV-03 | Bloqueos y validación de stock en transferencias | Media | 0.5-1 día | Inventario |
| PROD-01 | Ensamblaje: transacción atómica con bloqueo de filas (evitar doble descuento) | Alta | 1-2 días | Producción |
| PROD-02 | `ProduccionResource` debe invocar un servicio, no otro resource con request falso | Media | 0.5 día | Producción |
| PROD-03 | Validaciones de receta/lote destino coherentes y con mensajes correctos | Baja | 0.5 día | Producción |
| SEG-01 | Eliminar credencial de BD por defecto embebida en `app.py` | Crítica | 0.25 día | Seguridad |
| SEG-02 | Exigir `JWT_SECRET_KEY` fuerte en todos los entornos (sin default inseguro) | Crítica | 0.25 día | Seguridad |
| SEG-03 | Dejar de devolver `str(e)` al cliente (fuga de información) | Alta | 0.5-1 día | Seguridad |
| SEG-04 | Endurecer el webhook de Telegram (header secreto, rate limit, tamaño de payload) | Alta | 0.5-1 día | Seguridad |
| SEG-05 | Anti fuerza bruta en `/auth` (rate limit específico + lockout progresivo) | Alta | 0.5-1 día | Seguridad |
| SEG-06 | Corregir el parseo de `ALLOWED_ORIGINS` (CORS) | Media | 0.25 día | Seguridad |
| SEG-07 | Nunca ejecutar con `debug=True` derivado del entorno | Media | 0.25 día | Seguridad |
| SEG-08 | Externalizar datos SUNAT incrustados (RUC, placa, DNI chofer, direcciones) | Media | 0.5 día | Seguridad |
| SEG-09 | Escapar HTML en todos los mensajes del bot (nombres de clientes/productos) | Media | 0.5 día | Seguridad |
| SEG-10 | Rate limiter con storage Redis (hoy `memory://`, inútil con varios workers) | Baja | 0.5 día | Seguridad |
| PERF-01 | Reemplazar propiedades `saldo_pendiente` por agregados SQL | Crítica | 1-2 días | Rendimiento |
| PERF-02 | Capa de caché (Redis) para catálogos y form-data | Media | 1-2 días | Rendimiento |
| PERF-03 | Paginación obligatoria en todos los listados | Media | 0.5-1 día | Rendimiento |
| CAL-01 | Suite de tests real con pytest + fixtures + CI | Alta | 3-5 días | Calidad |
| CAL-02 | Linting y formateo automáticos (ruff + pre-commit) | Media | 0.5 día | Calidad |
| CAL-03 | Manejo de errores homogéneo con error handlers globales | Media | 1 día | Calidad |
| CAL-04 | Limpieza de código muerto, comentarios "MEJORA:" y docs desactualizadas | Baja | 0.5-1 día | Calidad |
| DEV-01 | Pipeline CI/CD con GitHub Actions (lint + tests + build Docker) | Alta | 1 día | DevOps |
| DEV-02 | Endpoint `/health` + `HEALTHCHECK` Docker + `FLASK_ENV=production` | Media | 0.5 día | DevOps |
| DEV-03 | Gestión de secretos: `.env.example`, validación al arranque, rotación | Media | 0.5 día | DevOps |
| DEV-04 | Logging estructurado (JSON) + monitoreo de errores (Sentry) | Baja | 1 día | DevOps |

---

## 3. Roadmap por fases

### Fase 0 — Estabilización urgente (1 semana)
Objetivo: eliminar riesgos de corrupción de datos y exposición de secretos, sin cambios funcionales visibles.

1. **SEG-01, SEG-02, SEG-07** — sanear secretos y arranque (medio día).
2. **BD-01** — inicializar Flask-Migrate y capturar el esquema actual como migración base.
3. **BD-02** — corregir el constraint único de `inventario` (desbloquea el FIFO multi-lote).
4. **SEG-03** — dejar de filtrar `str(e)`.
5. **SEG-04 + TG-01 (parte mínima)** — header secreto del webhook + deduplicación de `update_id`.

### Fase 1 — Integridad de datos y concurrencia (2 semanas)
1. **BD-03 + VEN-03** — FK `movimientos.venta_id` con backfill; reescritura de las consultas `LIKE`.
2. **VEN-01** — FIFO con `with_for_update(skip_locked=False)` en POST /ventas.
3. **VEN-02** — reversión de inventario correcta en PUT /ventas.
4. **PROD-01, INV-03** — bloqueos en ensamblaje y transferencias.
5. **BD-04, BD-06, BD-07** — índices y constraints.
6. **VEN-06** — política explícita de stock.

### Fase 2 — Arquitectura y bot (2-3 semanas)
1. **ARQ-01 + INV-02** — `services/venta_service.py`, `services/stock_service.py`, `services/pago_service.py` (mover `PagoService`).
2. **TG-03 + ARQ-02** — el bot consume los servicios; dividir `telegram_webhook_resource.py` en handlers.
3. **TG-01 (completa)** — procesamiento asíncrono del webhook.
4. **TG-02, TG-04, TG-05, TG-06, TG-08, SEG-09** — correcciones puntuales del bot.
5. **ARQ-03, ARQ-04, PROD-02** — application factory y eliminación de request falsos.
6. **GEM-01…GEM-04** — mejoras del servicio Gemini.

### Fase 3 — Rendimiento y experiencia (1-2 semanas)
1. **PERF-01** — `saldo_pendiente` como agregado SQL (elimina el mayor N+1).
2. **VEN-04, VEN-05, INV-01, PERF-03** — listados paginados y eager loading.
3. **PERF-02, SEG-10** — Redis para caché y rate limiting.
4. **SEG-05, SEG-06, SEG-08** — pendientes de seguridad.

### Fase 4 — Calidad y operación continua (1-2 semanas, en paralelo desde Fase 2)
1. **CAL-01** — pytest + fixtures + BD efímera.
2. **DEV-01** — GitHub Actions.
3. **CAL-02, CAL-03, DEV-02, DEV-03** — homogeneización.
4. **DEV-04, CAL-04, TG-07, ARQ-05** — mejoras finales.

> **Regla de dependencias global:** BD-01 (migraciones) es prerrequisito de todo cambio de esquema (BD-02…BD-07, VEN-03, TG-07). ARQ-01 es prerrequisito de TG-03. VEN-03 depende de BD-03.

---


## 4. Arquitectura (ARQ)

### ARQ-01 — Capa de servicios de dominio unificada
- **Prioridad:** Alta · **Esfuerzo:** 5-8 días · **Dependencias:** ninguna (habilita TG-03, VEN-01, VEN-02, INV-02)

**Problema actual.** La lógica de negocio de "crear una venta con descuento FIFO de inventario y registro de movimientos" está implementada **tres veces**, con divergencias entre sí:

1. `resources/venta_resource.py:169-231` (POST REST): FIFO por lotes, valida stock y falla si no alcanza.
2. `resources/telegram_webhook_resource.py` `_execute_venta` (~línea 849 en adelante): reimplementa el FIFO, usa `datetime.now()` naive y **sí** usa `with_for_update` (línea 892), a diferencia del REST.
3. `resources/telegram_webhook_resource.py:2038-2189` `_execute_ventas_lote`: tercera reimplementación que **permite stock negativo** (línea 2156: `inv_sin_lote.cantidad -= cantidad_restante` sin validar) y autocrea clientes.

Lo mismo ocurre con pagos (lógica en `PagoService` y duplicada en `_prepare_pago`/`_execute_pago` del bot) y transferencias (el bot invoca `TransferenciaService` pero parcheando `get_jwt` con `unittest.mock.patch` en producción, `telegram_webhook_resource.py:1866-1871`):

```python
# telegram_webhook_resource.py:1865-1871 — mock.patch en código de producción
from resources.transferencia_resource import TransferenciaService
from unittest.mock import patch

with patch('resources.transferencia_resource.get_jwt', return_value={"sub": user.id, ...}):
    service = TransferenciaService(payload)
    result = service.ejecutar_transferencia()
```

**Solución paso a paso.**

1. Crear `services/venta_service.py` con una API sin dependencia de `request`/`get_jwt` (el contexto entra por parámetros):

```python
# services/venta_service.py
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timezone
from extensions import db
from models import Venta, VentaDetalle, Cliente, Movimiento
from services.stock_service import StockService, StockInsuficienteError  # ver INV-02

@dataclass
class ItemVenta:
    presentacion_id: int
    cantidad: Decimal
    precio_unitario: Decimal

@dataclass
class ContextoUsuario:          # sustituye a get_jwt() dentro de la lógica
    usuario_id: int
    rol: str
    almacen_id: int | None

class VentaService:
    @staticmethod
    def crear_venta(ctx: ContextoUsuario, cliente_id: int, almacen_id: int,
                    items: list[ItemVenta], tipo_pago: str,
                    fecha: datetime | None = None,
                    permitir_stock_negativo: bool = False) -> Venta:
        """Crea la venta, descuenta stock FIFO con bloqueo y registra movimientos.
        NO hace commit: el llamador (resource/bot) controla la transacción."""
        fecha = fecha or datetime.now(timezone.utc)
        total = sum(i.cantidad * i.precio_unitario for i in items)
        venta = Venta(cliente_id=cliente_id, almacen_id=almacen_id,
                      vendedor_id=ctx.usuario_id, total=total,
                      tipo_pago=tipo_pago, fecha=fecha)
        db.session.add(venta)
        db.session.flush()

        for item in items:
            # StockService encapsula el FIFO con with_for_update (VEN-01/INV-02)
            consumos = StockService.descontar_fifo(
                almacen_id=almacen_id,
                presentacion_id=item.presentacion_id,
                cantidad=item.cantidad,
                permitir_negativo=permitir_stock_negativo,
            )
            for consumo in consumos:   # un detalle+movimiento por lote consumido
                db.session.add(VentaDetalle(
                    venta_id=venta.id, presentacion_id=item.presentacion_id,
                    cantidad=consumo.cantidad, precio_unitario=item.precio_unitario,
                    lote_id=consumo.lote_id))
                db.session.add(Movimiento(
                    tipo='salida', tipo_operacion='venta', venta_id=venta.id,  # FK real, ver BD-03
                    presentacion_id=item.presentacion_id, lote_id=consumo.lote_id,
                    cantidad=consumo.cantidad, usuario_id=ctx.usuario_id,
                    fecha=fecha, motivo=f"Venta ID: {venta.id}"))
        venta.actualizar_estado()
        return venta
```

2. Refactorizar `VentaResource.post` para que solo haga: parseo/validación Marshmallow → `VentaService.crear_venta(...)` → `db.session.commit()` → serialización.
3. Refactorizar el bot (`_execute_venta`, `_execute_ventas_lote`) para que construya `ContextoUsuario(user.id, user.rol, user.almacen_id)` y llame al mismo servicio (ver TG-03).
4. Repetir el patrón con `PagoService` (moverlo de `resources/pago_resource.py:33` a `services/pago_service.py`) y `TransferenciaService` (quitarle la llamada a `get_jwt()` del `__init__`, `transferencia_resource.py:29`, y pasar el contexto por parámetro — eso elimina el `mock.patch` del bot).

**Criterios de aceptación.**
- Existe `services/venta_service.py` y `grep -rn "unittest.mock" resources/` no devuelve resultados.
- POST /ventas, bot (`venta` y `ventas_lote`) y voz producen exactamente los mismos registros (venta, detalles por lote, movimientos con `venta_id`) para una misma entrada.
- Ninguna función de `services/` importa `flask.request` ni `get_jwt`.

---

### ARQ-02 — Descomponer la God Class `TelegramWebhookResource`
- **Prioridad:** Alta · **Esfuerzo:** 3-5 días · **Dependencias:** ARQ-01 (recomendada)

**Problema actual.** `resources/telegram_webhook_resource.py` tiene **2 217 líneas** y una sola clase concentra: verificación del webhook, vinculación de cuentas, parsing de callbacks, resolución difusa de clientes/productos/almacenes, y ~20 métodos `_prepare_*`/`_execute_*` (venta, pago, gasto, depósito, producción, guía SUNAT, transferencia, ventas por lote…). Es imposible de testear unitariamente y cada bugfix arriesga romper otro flujo.

**Solución paso a paso.**
1. Crear el paquete `telegram/`:
```
telegram/
├── __init__.py
├── router.py            # decide el handler según action de Gemini / callback_data
├── resolvers.py         # _buscar_presentacion, _resolver_cliente, _resolver_almacen
├── ui.py                # construcción de tarjetas HTML y teclados inline (+ escape, SEG-09)
├── handlers/
│   ├── venta.py         # prepare/execute venta y ventas_lote
│   ├── pago.py          # prepare/execute pago y deposito
│   ├── produccion.py
│   ├── transferencia.py
│   ├── guia_sunat.py
│   └── consulta.py      # stock, deudas, reportes
└── context.py           # lectura/escritura de telegram_context del usuario
```
2. Cada handler expone `prepare(chat_id, user, args) -> None` y `execute(chat_id, user, context, message_id) -> None`, y usa los servicios de dominio (ARQ-01).
3. `TelegramWebhookResource.post` queda en <100 líneas: validar secreto (SEG-04), deduplicar update (TG-01), extraer mensaje/callback y delegar a `router.dispatch(...)`.
4. Migrar flujo por flujo (empezar por `venta`), manteniendo los métodos antiguos hasta que su handler nuevo tenga tests.

**Criterios de aceptación.**
- `telegram_webhook_resource.py` < 200 líneas; ningún módulo de `telegram/` supera ~400 líneas.
- Cada handler tiene al menos un test unitario que lo ejercita sin pasar por Flask ni Telegram (mock de `telegram_service`).

---

### ARQ-03 — Eliminar la invocación interna de resources con requests falsos
- **Prioridad:** Media · **Esfuerzo:** 1-2 días · **Dependencias:** ARQ-01

**Problema actual.** `ProduccionResource.post` invoca a otro resource fabricando un request falso en producción (`resources/produccion_resource.py:93-100`):

```python
ensamblaje_resource = ProduccionEnsamblajeResource()
...
with current_app.test_request_context('/api/produccion/ensamblaje', method='POST',
                                      headers=headers, data=json.dumps(ensamblaje_payload)):
    return ensamblaje_resource.post()
```

`test_request_context` es una utilidad **de testing**: crea un contexto artificial, vuelve a ejecutar `@jwt_required()` sobre un header copiado, y rompe el rastreo de errores y los decoradores de rate limit. El mismo antipatrón aparece en el bot con `mock.patch` (ver ARQ-01).

**Solución.** Extraer la lógica de `ProduccionEnsamblajeResource.post` a `services/produccion_service.py::ejecutar_ensamblaje(ctx, almacen_id, entradas, salidas, descripcion)` y hacer que ambos resources (y el bot) llamen a esa función directamente.

**Criterios de aceptación.** `grep -rn "test_request_context" resources/` sin resultados; los dos endpoints de producción devuelven las mismas respuestas que antes (verificado con tests de contrato).

---

### ARQ-04 — Application Factory (`create_app`)
- **Prioridad:** Media · **Esfuerzo:** 1-2 días · **Dependencias:** ninguna (facilita CAL-01)

**Problema actual.** `app.py` construye la aplicación a nivel de módulo (instancia global `app`, configuración leída al importar, `debug=not IS_PRODUCTION` en el `app.run` final). Esto impide crear una app de test con configuración aislada — razón por la que `test_telegram_webhook.py:14` hace `from app import app` y muta la BD real configurada.

**Solución paso a paso.**
1. Reestructurar `app.py`:

```python
# app.py (nuevo esqueleto)
def create_app(config_object="config.ProductionConfig"):
    app = Flask(__name__)
    app.config.from_object(config_object)
    _validar_config_critica(app)          # ver SEG-01/SEG-02/DEV-03
    db.init_app(app)
    migrate.init_app(app, db)             # ver BD-01
    jwt.init_app(app)
    limiter.init_app(app)
    _init_cors(app)
    _init_error_handlers(app)             # ver CAL-03
    api = Api(app)
    init_resources(api)                   # ya existe en resources/__init__.py
    return app

app = create_app(os.environ.get("APP_CONFIG", "config.ProductionConfig"))  # para gunicorn app:app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
```
2. Convertir `config.py` en clases (`BaseConfig`, `DevelopmentConfig`, `ProductionConfig`, `TestingConfig`).
3. En tests: `app = create_app("config.TestingConfig")` con SQLite/PostgreSQL efímero.

**Criterios de aceptación.** Gunicorn sigue arrancando con `app:app`; los tests crean su propia app sin tocar variables de entorno globales; `debug` nunca se activa implícitamente.

---

### ARQ-05 — Blueprints, versionado y formato de error estándar
- **Prioridad:** Baja · **Esfuerzo:** 2-3 días · **Dependencias:** ARQ-04

**Problema actual.** Los 27 resources se registran planos en `resources/__init__.py`; no hay versionado de API y las respuestas de error mezclan formatos: `{"error": ...}`, `{"message": ...}`, `{"error": ..., "detalle": ...}` (p. ej. `produccion_resource.py:45` vs `pago_resource.py:341` vs `dashboard_resource.py:175`).

**Solución.** Registrar la `Api` bajo el prefijo `/api/v1`, mantener alias temporales para las rutas antiguas, y definir un envelope único de error `{"error": {"code": "...", "message": "...", "details": {...}}}` emitido solo desde los error handlers globales (CAL-03).

**Criterios de aceptación.** Toda respuesta con status >= 400 cumple el envelope; documentación Swagger regenerada.

---

## 5. Base de Datos (BD)

### BD-01 — Migraciones versionadas con Flask-Migrate/Alembic
- **Prioridad:** Crítica · **Esfuerzo:** 1-2 días · **Dependencias:** ninguna (prerrequisito de BD-02…BD-07, VEN-03, TG-07)

**Problema actual.** El repositorio **no contiene carpeta `migrations/`** (verificado). `Flask-Migrate` está en `requirements.txt` pero no se inicializa en `app.py`/`extensions.py`. Consecuencias observables en el propio repo:
- `test_telegram_webhook.py:25-41` "migra" a mano con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS es_planta ...` y `... telegram_history JSONB` dentro del script de pruebas.
- El esquema real (volcado en `indexes.md`) diverge del ORM (ver BD-02).
- No hay forma reproducible de crear la BD desde cero ni de auditar cambios de esquema.

**Solución paso a paso.**
1. Registrar Migrate en `extensions.py` y en la factory:
```python
# extensions.py
from flask_migrate import Migrate
migrate = Migrate()
```
2. Generar la migración base **contra la BD real** (no contra los modelos) para capturar el estado actual:
```bash
flask db init
flask db migrate -m "esquema base (estado actual de produccion)"
# REVISAR a mano el archivo generado: Alembic detectará las diferencias
# reales entre modelos y BD (p. ej. el unique de inventario, BD-02).
flask db stamp head   # en producción, marca el esquema existente como migrado
```
3. Política de equipo: **prohibido** todo `ALTER TABLE` manual (incluidos scripts y tests); cada cambio de modelo va acompañado de `flask db migrate` + revisión del script.
4. Añadir un paso de CI (DEV-01) que ejecute `flask db upgrade` sobre un PostgreSQL efímero y falle si `alembic check`/`flask db migrate` detecta drift.

**Criterios de aceptación.**
- `migrations/` versionado en git; `flask db upgrade` desde cero crea un esquema idéntico al de producción.
- El script de tests ya no contiene sentencias `ALTER TABLE`.

---

### BD-02 — Corregir el constraint único de `inventario` (multi-lote roto)
- **Prioridad:** Crítica · **Esfuerzo:** 0.5-1 día · **Dependencias:** BD-01

**Problema actual.** El modelo declara unicidad por presentación+almacén+lote (`models.py:127`):

```python
UniqueConstraint('presentacion_id', 'almacen_id', 'lote_id', name='uq_inventario_compuesto'),
```

pero la BD real tiene (según `indexes.md:19`):

```
CREATE UNIQUE INDEX inventario_presentacion_id_almacen_id_key
    ON public.inventario USING btree (presentacion_id, almacen_id)
```

Todo el código nuevo de FIFO asume **múltiples filas de `inventario` por (presentación, almacén)** — una por lote (`transferencia_resource.py:98-118`, `venta_resource.py:169-231`, `_execute_ventas_lote` líneas 2100-2156). Con el índice único actual, el primer `INSERT` de un segundo lote lanzará `UniqueViolation` y el flujo caerá al `except` genérico. Además, en PostgreSQL el constraint del modelo (`uq_inventario_compuesto`) **no impide duplicados cuando `lote_id IS NULL`** (los NULL no colisionan), de modo que pueden acumularse varias filas "sin lote".

**Solución paso a paso** (migración Alembic):

```python
def upgrade():
    # 1) Eliminar el unique obsoleto de 2 columnas
    op.drop_constraint('inventario_presentacion_id_almacen_id_key', 'inventario', type_='unique')
    # 2) Unique de 3 columnas (si no existe ya en la BD)
    op.create_unique_constraint('uq_inventario_compuesto', 'inventario',
                                ['presentacion_id', 'almacen_id', 'lote_id'])
    # 3) Unicidad parcial para el caso lote_id IS NULL
    op.create_index('uq_inventario_sin_lote', 'inventario',
                    ['presentacion_id', 'almacen_id'], unique=True,
                    postgresql_where=sa.text('lote_id IS NULL'))
```

Antes de aplicar en producción, ejecutar un chequeo de duplicados:
```sql
SELECT presentacion_id, almacen_id, lote_id, COUNT(*)
FROM inventario GROUP BY 1,2,3 HAVING COUNT(*) > 1;
```
y consolidar duplicados sumando `cantidad` si aparecieran.

**Criterios de aceptación.**
- Se pueden insertar dos filas de inventario con lotes distintos para la misma presentación/almacén.
- Insertar dos filas con `lote_id NULL` para la misma presentación/almacén falla.
- Test de integración de venta multi-lote (FIFO consume 2 lotes) pasa.

---

### BD-03 — FK `movimientos.venta_id` (eliminar el vínculo por texto)
- **Prioridad:** Alta · **Esfuerzo:** 1-2 días · **Dependencias:** BD-01 · **Habilita:** VEN-02, VEN-03

**Problema actual.** Los movimientos de una venta se identifican por coincidencia de texto sobre `motivo`. Ejemplos reales:

```python
# venta_resource.py:358 (PUT) — también en :488 y :531 (DELETE)
movimientos_anteriores = Movimiento.query.filter(
    Movimiento.motivo.like(f"Venta ID: {venta_id}%")
).all()
```

Problemas: (a) **colisión de prefijos** — `LIKE 'Venta ID: 12%'` también captura las ventas 120-129, 1200…, con lo cual editar/borrar la venta 12 puede **revertir inventario de otras ventas**; (b) `LIKE` sin índice = full scan de `movimientos`; (c) si alguien edita el texto del motivo, se pierde la trazabilidad.

**Solución paso a paso.**
1. Migración:
```python
def upgrade():
    op.add_column('movimientos', sa.Column('venta_id', sa.Integer(),
                  sa.ForeignKey('ventas.id', ondelete='SET NULL'), nullable=True))
    op.create_index('idx_movimientos_venta_id', 'movimientos', ['venta_id'])
    # Backfill defensivo: ancla el fin del número con ' -' o fin de cadena
    op.execute("""
        UPDATE movimientos m
        SET venta_id = sub.vid
        FROM (
            SELECT id, (regexp_match(motivo, '^Venta ID: (\\d+)'))[1]::int AS vid
            FROM movimientos
            WHERE motivo ~ '^Venta ID: \\d+'
        ) sub
        WHERE m.id = sub.id
          AND EXISTS (SELECT 1 FROM ventas v WHERE v.id = sub.vid)
    """)
```
2. Modelo: añadir `venta_id = db.Column(db.Integer, db.ForeignKey('ventas.id', ondelete='SET NULL'), index=True)` a `Movimiento` y relación `venta`.
3. Escribir siempre `venta_id=` al crear movimientos de venta (REST, bot, voz) — el `motivo` queda solo como texto descriptivo.
4. Reemplazar todas las consultas `motivo.like(...)` por `Movimiento.venta_id == venta_id` (ver VEN-03).

> Nota: el backfill con regex sí es exacto (a diferencia del `LIKE`), porque `^Venta ID: (\d+)` captura el número completo.

**Criterios de aceptación.**
- `grep -rn "motivo.like" resources/` sin resultados.
- Editar/borrar la venta 12 no toca movimientos de la venta 123 (test dedicado).

---

### BD-04 — Índices faltantes
- **Prioridad:** Alta · **Esfuerzo:** 0.5 día · **Dependencias:** BD-01

**Problema actual.** Comparando los patrones de consulta del código con los índices reales (`indexes.md`), faltan índices para las rutas calientes:

| Consulta en el código | Índice faltante |
|---|---|
| `venta_resource.py` filtros por `almacen_id` + rango de `fecha`; dashboard | `ventas (almacen_id, fecha DESC)` |
| filtro por vendedor (`VentaResource.get`, reportes) | `ventas (vendedor_id)` |
| `dashboard_resource.py:63` `estado_pago.in_(['pendiente','parcial'])` global (el índice existente `idx_ventas_estado_pago` empieza por `cliente_id` y no sirve aquí) | `ventas (estado_pago)` parcial: `WHERE estado_pago <> 'pagado'` |
| carga de detalles por venta (relación `Venta.detalles`) | `venta_detalles (venta_id)` |
| `venta_detalles` por presentación (reportes de producto) | `venta_detalles (presentacion_id)` |
| movimientos por lote (`kardex`, FIFO) | `movimientos (lote_id)` |
| movimientos por fecha+tipo (`reporte_produccion`) | `movimientos (tipo_operacion, fecha DESC)` |
| `pago_resource.py:602-605` cierre de caja por fecha | `pagos (fecha)` |
| `pago_resource.py:68-69` filtro por usuario | `pagos (usuario_id)` |
| `gastos` por fecha/almacén/usuario (cierre de caja) | `gastos (fecha)`, `gastos (almacen_id)` |
| `inventario` por lote (transferencias FIFO) | `inventario (lote_id)` |
| resolución de usuario del bot por chat | `users (telegram_chat_id)` (si no es ya UNIQUE en BD) |

**Solución.** Una única migración Alembic con `op.create_index(...)` para cada entrada (usar `postgresql_where` para el parcial de `estado_pago`). En Supabase/producción, crear con `CREATE INDEX CONCURRENTLY` fuera de transacción si las tablas ya son grandes.

**Criterios de aceptación.** `EXPLAIN ANALYZE` de los listados de ventas filtrados por almacén+fecha y del dashboard muestran Index Scan; sin regresión de escritura apreciable.

---

### BD-05 — Unificar tipos numéricos de cantidades
- **Prioridad:** Media · **Esfuerzo:** 1 día · **Dependencias:** BD-01

**Problema actual.** `Inventario.cantidad` es `Numeric(12,4)` (`models.py:116`) y los movimientos usan `Decimal`, pero `VentaDetalle.cantidad` es `Integer`. El bot ya sufre esta inconsistencia: en `_execute_ventas_lote` trunca con `int(cantidad_a_tomar)` (`telegram_webhook_resource.py:2126` y `:2161`), de modo que si el FIFO parte una cantidad en fracciones, el detalle registrado **pierde los decimales** y deja de cuadrar con el movimiento y el inventario.

**Solución.** Migración `op.alter_column('venta_detalles', 'cantidad', type_=sa.Numeric(12, 4))`, actualizar el modelo y el schema Marshmallow (`fields.Decimal(as_string=True)`), y eliminar los casts `int(...)` en el bot y resources. Si el negocio realmente solo vende unidades enteras, en su lugar **validar** que la cantidad sea entera en el schema, pero mantener el mismo tipo en toda la cadena.

**Criterios de aceptación.** Sumatoria de `venta_detalles.cantidad` = sumatoria de `movimientos.cantidad` de la venta en los tests de FIFO fraccionado.

---

### BD-06 — `ultima_actualizacion` automático
- **Prioridad:** Media · **Esfuerzo:** 0.25 día · **Dependencias:** BD-01

**Problema actual.** `models.py:119` define `ultima_actualizacion` solo con `default`, por lo que **no se actualiza** al modificar la fila; algunos flujos lo actualizan a mano (`transferencia_resource.py:167`, `produccion_resource.py:192`) y otros no (FIFO de ventas), dejando el dato inconsistente.

**Solución.**
```python
ultima_actualizacion = db.Column(
    db.DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
    onupdate=lambda: datetime.now(timezone.utc),   # <— añadir
    server_default=db.func.now())
```
Eliminar las asignaciones manuales redundantes.

**Criterios de aceptación.** Tras cualquier venta/transferencia/ensamblaje, `ultima_actualizacion` de las filas tocadas queda en el instante de la operación sin código explícito.

---

### BD-07 — Constraints CHECK de no-negatividad
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** BD-01, VEN-06 (definir política primero)

**Problema actual.** Nada impide stock negativo a nivel BD, y el código lo produce activamente (`telegram_webhook_resource.py:2156`). Tampoco hay CHECK sobre `pagos.monto > 0` ni `lotes.cantidad_disponible_kg >= 0` (el ensamblaje descuenta sin lock, PROD-01).

**Solución.** Una vez decidida la política (VEN-06), migración:
```python
op.create_check_constraint('ck_inventario_cantidad_no_negativa', 'inventario', 'cantidad >= 0')
op.create_check_constraint('ck_lotes_kg_no_negativo', 'lotes', 'cantidad_disponible_kg >= 0')
op.create_check_constraint('ck_pagos_monto_positivo', 'pagos', 'monto > 0')
```
Antes, sanear datos existentes (`UPDATE ... SET cantidad = 0 WHERE cantidad < 0` con reporte previo al negocio). La BD se convierte en la última línea de defensa: el código debe validar antes y traducir la `IntegrityError` a un 409 legible (CAL-03).

**Criterios de aceptación.** Intentar dejar stock negativo desde cualquier flujo devuelve error controlado 409/400 y no persiste nada.

---


## 6. Ventas (VEN)

### VEN-01 — FIFO de ventas con bloqueo pesimista
- **Prioridad:** Crítica · **Esfuerzo:** 1-2 días · **Dependencias:** BD-02 (multi-lote operativo); idealmente vía INV-02

**Problema actual.** El POST de ventas (`resources/venta_resource.py:169-231`) consulta los inventarios FIFO y los descuenta **sin bloquear las filas**:

```python
# venta_resource.py (~169-231, esquema real del flujo)
invs_disponibles = (Inventario.query
    .join(Lote, Inventario.lote_id == Lote.id, isouter=True)
    .filter(Inventario.presentacion_id == presentacion_id,
            Inventario.almacen_id == almacen_id,
            Inventario.cantidad > 0)
    .order_by(Lote.fecha_ingreso.asc(), Inventario.id.asc())
    .all())                       # ← sin with_for_update()
...
inv.cantidad -= cantidad_a_tomar  # ← read-modify-write con dato posiblemente obsoleto
```

Con Gunicorn (3 workers × 2 threads, `Dockerfile:47,57`), dos requests simultáneos que vendan la misma presentación leerán el mismo stock y ambos lo descontarán → **sobreventa / stock negativo**. Curiosamente el bot sí bloquea (`telegram_webhook_resource.py:892` usa `.with_for_update()`), pero el REST y las ventas por lote no. La transferencia (`transferencia_resource.py:98-110`) y el ensamblaje (PROD-01) tienen el mismo defecto.

**Solución paso a paso.**
1. Añadir `.with_for_update()` a la consulta FIFO (las filas de inventario de esa presentación/almacén quedan bloqueadas hasta el commit):

```python
invs_disponibles = (
    Inventario.query
    .filter(Inventario.presentacion_id == presentacion_id,
            Inventario.almacen_id == almacen_id,
            Inventario.cantidad > 0)
    .order_by(Inventario.lote_id.asc().nullsfirst(), Inventario.id.asc())
    .with_for_update()          # ← SELECT ... FOR UPDATE
    .all())
```
> Ojo: `with_for_update()` con `OUTER JOIN` a `lotes` falla en PostgreSQL (`FOR UPDATE` no aplica al lado nullable del join). Solución: bloquear solo `inventario` con `with_for_update(of=Inventario)` o hacer dos pasos — primero `SELECT id FROM inventario ... FOR UPDATE`, después ordenar en memoria por `lote.fecha_ingreso` (los lotes se cargan con `selectinload` tras el bloqueo).
2. Re-verificar el stock **después** de obtener el lock (el total puede haber cambiado entre la validación previa y el bloqueo) y abortar con 409 si ya no alcanza.
3. Mantener toda la operación (venta + detalles + movimientos + descuento) en **una sola transacción** con un único `commit` al final (ya es así; no introducir commits intermedios).
4. Aplicar el mismo patrón en `_execute_venta` del bot para el flujo completo (hoy solo bloquea la consulta final) y en `_execute_ventas_lote`.

**Criterios de aceptación.**
- Test de concurrencia: 2 hilos venden 8 unidades cada uno con stock 10 → exactamente una venta falla con 409 y el stock final es 2.
- Ningún flujo de venta deja `inventario.cantidad < 0`.

---

### VEN-02 — Reescribir la reversión de inventario del `PUT /ventas`
- **Prioridad:** Crítica · **Esfuerzo:** 2-3 días · **Dependencias:** BD-03, VEN-01, INV-02

**Problema actual.** `VentaResource.put` (`venta_resource.py:314-478`) revierte el inventario de la venta original antes de aplicar los nuevos detalles, pero:

1. Busca los movimientos por `motivo.like(f"Venta ID: {venta_id}%")` (`venta_resource.py:358`) → colisiones de prefijo (BD-03): puede revertir stock de **otras ventas**.
2. Construye un dict `{presentacion_id: inventario}` con **una sola fila por presentación**, perdiendo los demás lotes: si la venta original consumió 2 lotes, la reversión devuelve todo al primero que encuentre, descuadrando el stock por lote.
3. No usa `with_for_update`, así que compite con ventas concurrentes.

**Solución paso a paso.**
1. Con BD-03 aplicado, obtener los movimientos exactos de la venta: `Movimiento.query.filter_by(venta_id=venta_id, tipo_operacion='venta')`.
2. Implementar en `StockService` (INV-02) la operación inversa exacta por movimiento:

```python
# services/stock_service.py
@staticmethod
def revertir_movimientos_venta(venta_id: int):
    """Devuelve al inventario exactamente lo que cada movimiento sacó,
    lote por lote, con bloqueo de las filas implicadas."""
    movimientos = (Movimiento.query
                   .filter_by(venta_id=venta_id, tipo='salida', tipo_operacion='venta')
                   .all())
    for mov in movimientos:
        inv = (Inventario.query
               .filter_by(almacen_id=mov_almacen_id(mov),   # ver nota
                          presentacion_id=mov.presentacion_id,
                          lote_id=mov.lote_id)
               .with_for_update()
               .first())
        if inv is None:   # el registro de lote pudo haberse agotado/eliminado
            inv = Inventario(almacen_id=..., presentacion_id=mov.presentacion_id,
                             lote_id=mov.lote_id, cantidad=0)
            db.session.add(inv)
            db.session.flush()
        inv.cantidad += mov.cantidad
        db.session.add(Movimiento(tipo='entrada', tipo_operacion='ajuste_edicion',
                                  venta_id=venta_id, presentacion_id=mov.presentacion_id,
                                  lote_id=mov.lote_id, cantidad=mov.cantidad,
                                  motivo=f"Reversión por edición de venta {venta_id}"))
```
> Nota: `Movimiento` no guarda `almacen_id`; el almacén se obtiene de `venta.almacen_id`. Si se contempla que una venta pueda cambiar de almacén al editarse, conviene añadir `almacen_id` a `movimientos` en la misma migración de BD-03.
3. El PUT queda: bloquear venta (`with_for_update`) → `revertir_movimientos_venta` → borrar detalles → `VentaService.crear_detalles(...)` (mismo código del POST) → recálculo de total/estado → commit único.
4. Prohibir editar ventas con pagos que excederían el nuevo total (validación ya parcialmente presente; convertirla en regla explícita con mensaje claro).

**Criterios de aceptación.**
- Editar una venta que consumió 2 lotes restaura exactamente las cantidades por lote (test con fixture multi-lote).
- Editar la venta 12 no altera inventario asociado a la venta 123.
- El kardex (movimientos) refleja la reversión como movimientos de ajuste, sin borrar historia.

---

### VEN-03 — Usar `venta_id` real en movimientos
- **Prioridad:** Alta · **Esfuerzo:** 1 día · **Dependencias:** BD-03

**Problema actual.** Además del PUT (VEN-02), el DELETE de ventas (`venta_resource.py:488` y `:531`) y las consultas de auditoría usan el patrón `motivo.like("Venta ID: {id}%")`. El bot también genera motivos de texto (`telegram_webhook_resource.py:2139`, `:2173`).

**Solución.** Barrido completo: (1) todos los `Movimiento(...)` de ventas reciben `venta_id=venta.id`; (2) todas las lecturas usan `filter_by(venta_id=...)`; (3) el `motivo` se mantiene como descripción humana pero deja de ser criterio de búsqueda. Aplicar el mismo criterio conceptual a producción/transferencias, que hoy embeben un UUID en el motivo (`produccion_resource.py:141`, `transferencia_resource.py:180-181`): añadir columna `operacion_id` (UUID) indexada en `movimientos` para agrupar operaciones.

**Criterios de aceptación.** `grep -rn "motivo.like\|motivo LIKE" .` sin resultados; el DELETE de venta restaura stock solo de sus propios movimientos.

---

### VEN-04 — Eliminar `?all=true` y añadir eager loading al listado de ventas
- **Prioridad:** Alta · **Esfuerzo:** 0.5-1 día · **Dependencias:** ninguna

**Problema actual.** `VentaResource.get` (`venta_resource.py:92-94`) permite `?all=true`, que devuelve **todas** las ventas con el dump completo (cliente, detalles anidados con presentación, pagos), sin eager loading → una consulta por venta y por relación (N+1 masivo). Con unos miles de ventas la respuesta tarda decenas de segundos y consume cientos de MB.

**Solución paso a paso.**
1. Eliminar la rama `all=true` (o limitarla con tope duro `min(total, 500)` y schema resumido sin detalles anidados).
2. Añadir eager loading a la consulta paginada:
```python
query = Venta.query.options(
    joinedload(Venta.cliente),
    joinedload(Venta.almacen),
    joinedload(Venta.vendedor),
    selectinload(Venta.detalles).joinedload(VentaDetalle.presentacion),
    selectinload(Venta.pagos),
)
```
3. Para los consumidores que usaban `all=true` (exportaciones), ofrecer un endpoint de exportación en streaming como `PagoExportResource`.

**Criterios de aceptación.** El listado paginado de ventas ejecuta un número de queries constante (verificable con `sqlalchemy` echo o pytest-sqlalchemy) independiente del número de filas.

---

### VEN-05 — `VentaFormDataResource` sin dump masivo de clientes
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** PERF-01 (ideal)

**Problema actual.** `venta_resource.py:574-575` carga **todos** los clientes y los serializa con `ClienteSchema`, que incluye la property `saldo_pendiente` → por **cada cliente** se cargan sus ventas y los pagos de cada venta (N+1 al cubo). El mismo patrón se repite en `voice_resource.py:283-289` (`clientes_disponibles`).

**Solución.** (1) Servir una lista ligera `{id, nombre, telefono, ciudad, almacen_preferido_id}` con un schema reducido (`ClienteLiteSchema`) sin `saldo_pendiente`; (2) si el formulario necesita el saldo, obtenerlo con la consulta agregada de PERF-01 en un solo query; (3) considerar un endpoint de autocompletado (`?q=texto`, límite 20) en lugar de volcar el catálogo completo.

**Criterios de aceptación.** GET del form-data ejecuta ≤ 5 queries totales; payload < 200 KB con 1 000 clientes.

---

### VEN-06 — Política explícita ante stock insuficiente
- **Prioridad:** Media · **Esfuerzo:** 1 día · **Dependencias:** decisión de negocio; habilita BD-07

**Problema actual.** Cada flujo decide distinto: el POST REST rechaza la venta; el bot en lote **advierte pero vende igual** dejando stock negativo (`telegram_webhook_resource.py:2144-2156`, comentario "se descuenta de lo que haya o del inventario sin lote"); el ensamblaje valida pero sin lock (puede quedar negativo bajo concurrencia). No hay ninguna marca en la venta que indique que se vendió "en descubierto".

**Solución.** Definir una única política en `StockService.descontar_fifo(..., permitir_negativo: bool)`:
- `permitir_negativo=False` (REST, voz): error 409 con detalle de faltantes.
- `permitir_negativo=True` (bot, si el negocio lo requiere): permitir el descubierto **solo** sobre la fila "sin lote", registrar un `Movimiento` con `tipo_operacion='venta_descubierto'` y añadir al mensaje de confirmación del bot el aviso de stock negativo resultante, para regularización posterior.
Documentar la política en `docs/` y reflejarla en BD-07 (si se decide prohibir siempre, activar el CHECK).

**Criterios de aceptación.** La política es idéntica para los tres canales salvo el flag explícito; existe un reporte/endpoint que lista inventarios negativos pendientes de regularizar.

---

## 7. Inventario (INV)

### INV-01 — Eliminar el N+1 de `InventarioGlobalResource`
- **Prioridad:** Alta · **Esfuerzo:** 0.5-1 día · **Dependencias:** ninguna

**Problema actual.** `resources/inventario_resource.py:27-57` construye el inventario global consultando, **dentro de un bucle por presentación**, los registros/detalles de cada una (patrón N+1: ~1 query por presentación, más las de lotes/almacenes).

**Solución.** Reemplazar el bucle por una consulta agregada única:

```python
filas = (db.session.query(
            Inventario.presentacion_id,
            PresentacionProducto.nombre.label('presentacion'),
            Inventario.almacen_id,
            Almacen.nombre.label('almacen'),
            Inventario.lote_id,
            func.sum(Inventario.cantidad).label('cantidad'),
            func.min(Inventario.stock_minimo).label('stock_minimo'))
         .join(PresentacionProducto, Inventario.presentacion_id == PresentacionProducto.id)
         .join(Almacen, Inventario.almacen_id == Almacen.id)
         .group_by(Inventario.presentacion_id, PresentacionProducto.nombre,
                   Inventario.almacen_id, Almacen.nombre, Inventario.lote_id)
         .all())
# Agrupar en Python el resultado plano en la estructura jerárquica esperada por el frontend.
```
El mismo patrón de "una query plana + agrupación en memoria" que ya usa correctamente `TransferenciaInventarioResource.get` (`transferencia_resource.py:226-272`).

**Criterios de aceptación.** El endpoint ejecuta ≤ 3 queries totales sea cual sea el número de presentaciones; la respuesta JSON mantiene el mismo contrato.

---

### INV-02 — Módulo único de FIFO: `services/stock_service.py`
- **Prioridad:** Media (habilitador de VEN-01/02, PROD-01, TG-03) · **Esfuerzo:** 2-3 días · **Dependencias:** BD-02

**Problema actual.** La lógica "consumir stock FIFO por lote" está reescrita al menos en: `venta_resource.py` (POST y PUT), `transferencia_resource.py:136-205`, `telegram_webhook_resource.py` (`_execute_venta`, `_execute_ventas_lote:2100-2176`, `_prepare_ventas_lote:1963-1976`) y parcialmente en `produccion_resource.py`. Cada copia tiene matices distintos (con/sin lock, con/sin fila "sin lote", int vs Decimal).

**Solución.** Crear el servicio único:

```python
# services/stock_service.py
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class ConsumoLote:
    inventario_id: int
    lote_id: int | None
    cantidad: Decimal

class StockInsuficienteError(Exception):
    def __init__(self, presentacion_id, requerido, disponible):
        self.presentacion_id, self.requerido, self.disponible = presentacion_id, requerido, disponible
        super().__init__(f"Stock insuficiente: requerido {requerido}, disponible {disponible}")

class StockService:
    @staticmethod
    def descontar_fifo(almacen_id, presentacion_id, cantidad: Decimal,
                       permitir_negativo=False) -> list[ConsumoLote]: ...
    @staticmethod
    def ingresar(almacen_id, presentacion_id, lote_id, cantidad: Decimal) -> None: ...
    @staticmethod
    def transferir_fifo(origen_id, destino_id, presentacion_id, cantidad) -> list[ConsumoLote]: ...
    @staticmethod
    def revertir_movimientos_venta(venta_id) -> None: ...   # VEN-02
    @staticmethod
    def stock_disponible(almacen_id, presentacion_id) -> Decimal: ...
```
Reglas internas: siempre `with_for_update` sobre `inventario`, siempre `Decimal`, orden FIFO por `lote.fecha_ingreso NULLS FIRST, inventario.id`. Migrar consumidores uno a uno (ventas → transferencias → producción → bot).

**Criterios de aceptación.** Un solo lugar del código contiene `order_by(...fecha_ingreso...)` para consumo de stock; tests unitarios del servicio cubren: multi-lote, sin lote, exacto, insuficiente, negativo permitido.

---

### INV-03 — Bloqueos y revalidación en transferencias
- **Prioridad:** Media · **Esfuerzo:** 0.5-1 día · **Dependencias:** INV-02 (o aplicar directamente)

**Problema actual.** `TransferenciaService` valida stock (`transferencia_resource.py:120-134`) y luego descuenta (`:136-205`) sobre objetos leídos **sin bloqueo** en `_obtener_inventarios` (`:98-110`). Entre la validación y el commit, otra venta puede consumir el mismo stock → transferencia deja negativo el origen. Además `_obtener_inventarios` hace outer join a `lotes`, lo que impide añadir `with_for_update` directamente (mismo problema técnico que VEN-01).

**Solución.** En `_obtener_inventarios`: primera consulta `select(Inventario.id).filter(...).with_for_update()` para bloquear; segunda consulta con `joinedload` de presentación/lote sobre esos IDs; revalidar stock tras el bloqueo. Con INV-02, sustituir todo el método por `StockService.transferir_fifo`.

**Criterios de aceptación.** Test de concurrencia venta+transferencia simultáneas nunca deja stock negativo.

---

## 8. Producción (PROD)

### PROD-01 — Ensamblaje atómico con bloqueo de filas
- **Prioridad:** Alta · **Esfuerzo:** 1-2 días · **Dependencias:** INV-02 recomendable

**Problema actual.** `ProduccionEnsamblajeResource.post` (`resources/produccion_resource.py:102-220`) hace una "fase de verificación" (líneas 113-136) y luego una "fase de ejecución" (143-212) **sin bloquear filas**: entre ambas fases otro request puede consumir el mismo insumo o kg de lote. Además:
- `produccion_resource.py:117` y `:153` leen el mismo `Inventario` dos veces sin `FOR UPDATE`; `:148` descuenta `lote.cantidad_disponible_kg` también sin lock.
- `:118` puede lanzar `AttributeError` si `inv` es None dentro del f-string (`inv.cantidad if inv else 0` está bien, pero la resta de `:154` asume que `inv` sigue existiendo con saldo).
- La validación del lote destino se repite dos veces con mensajes contradictorios ("lote de destino" en `:136` vs "lote de origen" en `:169`) — ver PROD-03.

**Solución paso a paso.**
1. Unificar verificación y ejecución: leer cada `Inventario`/`Lote` **una sola vez con `with_for_update()`**, validar sobre el objeto bloqueado y descontar inmediatamente:
```python
inv = (Inventario.query
       .filter_by(almacen_id=almacen_id, presentacion_id=pres_id, lote_id=None)
       .with_for_update().first())
if not inv or inv.cantidad < cantidad_req:
    raise StockInsuficienteError(pres_id, cantidad_req, inv.cantidad if inv else 0)
inv.cantidad -= cantidad_req

lote = db.session.get(Lote, lote_id, with_for_update=True)
if lote is None or lote.cantidad_disponible_kg < cantidad_req_kg:
    raise StockInsuficienteError(...)
lote.cantidad_disponible_kg -= cantidad_req_kg
```
2. Ordenar los locks de forma determinista (por `inventario.id` / `lote.id` ascendente) para evitar deadlocks entre ensamblajes concurrentes.
3. Mantener el `commit` único al final (ya existe, `:214`) y traducir errores a 409 sin `str(e)` (ver SEG-03; hoy `:220` devuelve `"detalle": str(e)`).

**Criterios de aceptación.** Dos ensamblajes concurrentes sobre el mismo lote: uno completa, el otro recibe 409; `lotes.cantidad_disponible_kg` nunca queda negativo.

---

### PROD-02 — `ProduccionResource` llama a un servicio, no a otro resource
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** ARQ-03 (es su caso concreto)

**Problema actual.** Ver ARQ-03: `produccion_resource.py:93-100` fabrica un `test_request_context` para invocar `ProduccionEnsamblajeResource.post()`, re-ejecutando `@jwt_required` sobre un header copiado.

**Solución.** Extraer `ejecutar_ensamblaje(ctx, payload)` a `services/produccion_service.py`; ambos resources la invocan con el contexto ya autenticado. Eliminar imports de `current_app`/`json` innecesarios.

**Criterios de aceptación.** Sin `test_request_context` en producción; respuesta idéntica en ambos endpoints.

---

### PROD-03 — Validaciones y mensajes coherentes en ensamblaje
- **Prioridad:** Baja · **Esfuerzo:** 0.5 día · **Dependencias:** ninguna

**Problema actual.**
- Doble validación del lote destino con mensajes contradictorios (`produccion_resource.py:132-136` y `:166-171`), la segunda dentro de la fase de ejecución (puede abortar a mitad con cambios ya aplicados a la sesión — se revierte por rollback, pero devuelve 400 tras trabajo inútil).
- `:170` compara `lote_destino.producto_id != presentacion_final.producto_id` pero el mensaje dice "presentación diferente".
- En `ProduccionResource`, la herencia del lote de origen (`:75-88`) toma "el primero" sin criterio documentado.

**Solución.** Consolidar todas las validaciones en la fase previa (con locks de PROD-01), corregir textos, documentar la regla de herencia de lote en un docstring y en `docs/`.

**Criterios de aceptación.** Todos los caminos de error se producen antes de mutar la sesión; mensajes revisados en tests.

---


## 9. Bot de Telegram (TG)

### TG-01 — Webhook asíncrono + deduplicación de `update_id` + respuesta 200 inmediata
- **Prioridad:** Crítica · **Esfuerzo:** 2-3 días · **Dependencias:** ninguna

**Problema actual.** `resources/telegram_webhook_resource.py` procesa todo el update **dentro del ciclo request/response**: parseo, resolución de usuario, llamada a Gemini (con reintentos y `time.sleep`), consultas a BD, ejecución de ventas/pagos y envío de la respuesta por la API de Telegram. Con Gemini lento o BD cargada, el request supera fácilmente el timeout de Telegram (~60 s con reintentos internos del bot mucho antes), y Telegram **reenvía el mismo update**. Como no se deduplica `update_id`, un "vende 5 cajas a Juan" reintentado puede **registrar la venta dos o tres veces** (con descuento de inventario cada vez). Agravantes:

- No se guarda ni consulta el `update_id` en ninguna parte (`grep update_id` solo aparece al parsear).
- El endpoint está **exento del rate limiter** y autenticado únicamente por el token en la ruta `/telegram/webhook/<webhook_token>` (ver SEG-04).
- El worker de gunicorn queda bloqueado (3 workers × 2 threads, `Dockerfile`): dos mensajes de voz simultáneos pueden dejar la API entera sin capacidad.

**Solución paso a paso.**
1. **Responder 200 de inmediato** y procesar en segundo plano. Opción recomendada: cola ligera con Redis + RQ (o Celery si se prefiere):

```python
# resources/telegram_webhook_resource.py (nuevo flujo)
class TelegramWebhookResource(Resource):
    def post(self, webhook_token):
        _validar_token(webhook_token)          # + header secreto, ver SEG-04
        update = request.get_json(silent=True) or {}
        update_id = update.get('update_id')
        if update_id is None:
            return {'ok': True}, 200
        if not _marcar_update_procesado(update_id):   # dedupe
            return {'ok': True}, 200                  # ya visto: ignorar
        telegram_queue.enqueue(procesar_update_telegram, update)
        return {'ok': True}, 200
```

2. **Deduplicación de `update_id`**: tabla `telegram_updates(update_id BIGINT PRIMARY KEY, recibido_en timestamptz)` con `INSERT ... ON CONFLICT DO NOTHING` (la fila insertada = primera vez), o `SET NX` en Redis con TTL de 24 h:

```python
def _marcar_update_procesado(update_id: int) -> bool:
    fila = db.session.execute(text(
        "INSERT INTO telegram_updates (update_id) VALUES (:u) "
        "ON CONFLICT (update_id) DO NOTHING RETURNING update_id"), {'u': update_id})
    db.session.commit()
    return fila.first() is not None
```

3. Mover todo el procesamiento actual (`_procesar_mensaje`, `_execute_*`, envío de respuesta) a un módulo `services/telegram_processor.py` ejecutado por el worker de la cola. El worker abre su propio contexto de aplicación (`app.app_context()`).
4. Mientras la cola no exista (paso intermedio de bajo riesgo): al menos implementar la deduplicación (paso 2) y un `ThreadPoolExecutor(max_workers=2)` propio del proceso para no bloquear el worker HTTP, devolviendo 200 tras encolar. Documentar que es transitorio.
5. Añadir a `requirements.txt`: `redis`, `rq` (versión fijada). Nuevo proceso en el despliegue: `rq worker telegram` (documentar en `docs/`).

**Criterios de aceptación.** El endpoint responde 200 en <500 ms con Gemini artificialmente lento (mock con `sleep(30)`); reenvíos del mismo `update_id` no generan ventas duplicadas (test con dos POST idénticos); los mensajes se procesan y responden por el worker.

---

### TG-02 — `_execute_deposito` afecta pagos de todos los usuarios/almacenes: filtrar por ámbito
- **Prioridad:** Alta · **Esfuerzo:** 0.5 día · **Dependencias:** ninguna

**Problema actual.** En `telegram_webhook_resource.py` (~línea 1160 en adelante), `_execute_deposito` marca como depositados pagos en **efectivo pendientes de TODA la base**, sin filtrar por el usuario que ordena el depósito ni por su almacén:

```python
pagos_pendientes = Pago.query.filter(
    Pago.metodo_pago == 'efectivo',
    Pago.depositado == False
).all()
```

Un usuario de un almacén que dicte "deposité 500 soles" puede "consumir" pagos en efectivo cobrados por otro usuario en otro almacén, descuadrando ambas cajas.

**Solución paso a paso.**
1. Filtrar por el ámbito del usuario del bot: `Pago.usuario_id == user.id` (o, si el flujo de caja es por almacén, join a `Venta.almacen_id == user.almacen_id`; decidir y documentar la regla de negocio).
2. Ordenar por fecha (`Pago.fecha.asc()`) para aplicar el depósito FIFO de forma determinista.
3. Si el monto depositado no cubre exactamente pagos completos, responder al usuario indicando el sobrante/faltante en lugar de ajustar silenciosamente.
4. Test: dos usuarios con pagos en efectivo; el depósito del usuario A no toca los pagos del usuario B.

**Criterios de aceptación.** Un depósito solo marca pagos del ámbito del emisor; existe test de aislamiento entre usuarios/almacenes.

---

### TG-03 — El bot debe usar los servicios de dominio (eliminar lógica duplicada de ventas/pagos)
- **Prioridad:** Alta · **Esfuerzo:** 3-4 días · **Dependencias:** ARQ-01, VEN-01

**Problema actual.** Es el caso más grave de la duplicación descrita en ARQ-01: `_execute_venta` (~849+), `_execute_ventas_lote` (~2045+), `_execute_pago`, `_execute_gasto`, etc., reimplementan dentro del webhook la creación de ventas (validación de stock, descuento de inventario, movimientos) y de pagos, con reglas **ligeramente distintas** a las de `venta_resource.py`/`pago_resource.py` (fechas naive, sin bloqueo de inventario, autocreación de clientes). Cualquier corrección hecha en la API (p. ej. VEN-01, VEN-02) **no** protege al bot, que seguirá corrompiendo stock.

**Solución paso a paso.**
1. Completar ARQ-01 (crear `services/venta_service.py`, `services/pago_service.py` con funciones puras `crear_venta(ctx, payload)`, `registrar_pago(ctx, payload)` usadas por los resources REST).
2. Sustituir el cuerpo de `_execute_venta`/`_execute_ventas_lote`/`_execute_pago` por: construir el payload equivalente al de la API → llamar al servicio → formatear la respuesta para Telegram. Eliminar los bloques duplicados (descuento de inventario manual, creación de `Movimiento`, etc.).
3. Los mensajes de error del servicio (stock insuficiente, cliente inexistente) se traducen a texto amigable del bot en un solo lugar (`_formatear_error_bot`).
4. Test de paridad: la misma venta creada por API y por bot produce filas idénticas en `ventas`, `venta_detalles`, `inventarios` y `movimientos`.

**Criterios de aceptación.** `telegram_webhook_resource.py` no contiene lógica de descuento de inventario ni creación directa de `Venta`/`Pago`; test de paridad API/bot en verde; el archivo baja sustancialmente de sus 2 217 líneas actuales.

---

### TG-04 — Fechas naive (`datetime.now()`) en `_execute_venta`/`_execute_ventas_lote`: usar UTC
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** BD-04 (misma convención)

**Problema actual.** En `_execute_venta` (`telegram_webhook_resource.py:849-857`) la fecha de la venta se construye con `datetime.now()` **naive** (hora local del contenedor, que en Docker suele ser UTC pero sin tzinfo), y en `_execute_ventas_lote` (`:2045-2054`) se llega a leer `ahora.tzinfo` de un datetime naive (siempre `None`), de modo que la rama "con zona horaria" es código muerto. Las ventas del bot y las de la API pueden quedar con convenciones de hora distintas, rompiendo reportes por día (cortes a medianoche de Lima vs UTC).

**Solución paso a paso.**
1. Crear helper único en `common.py`:

```python
from datetime import datetime, timezone

def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)
```

2. Reemplazar todos los `datetime.now()`/`datetime.utcnow()` del webhook (y del resto del código, ver BD-04) por `ahora_utc()`.
3. Cuando el usuario dicte una fecha ("ayer", "el lunes"), interpretar en `America/Lima` (`zoneinfo.ZoneInfo('America/Lima')`) y convertir a UTC antes de persistir.
4. Eliminar la rama muerta de `:2045-2054`.

**Criterios de aceptación.** `grep -rn "datetime.now()" resources/` sin resultados naive; test que verifica que una venta dictada "hoy 8 pm" en Lima cae en el día correcto del reporte.

---

### TG-05 — Cachear catálogos (almacenes/presentaciones) usados en cada mensaje
- **Prioridad:** Media · **Esfuerzo:** 0.5-1 día · **Dependencias:** PERF-02 (comparte infraestructura de caché)

**Problema actual.** Por **cada mensaje** del bot, `_resolver_almacen` ejecuta `Almacen.query.all()` y hace matching por subcadena, y `_buscar_presentacion` (`:218-237`) intenta `func.similarity` de pg_trgm con un `try/except` que, si la extensión no existe, hace fallback silencioso a otra consulta. Catálogos que cambian pocas veces al día se releen en cada interacción, sumando latencia al webhook ya sobrecargado (TG-01).

**Solución paso a paso.**
1. Cachear `[(id, nombre), ...]` de almacenes y presentaciones activas en Redis (TTL 5 min) o, transitoriamente, en un dict de módulo con timestamp.
2. Invalidar la caché en los `post/put/delete` de `AlmacenResource`/`PresentacionResource` (o aceptar el TTL como única invalidación; documentarlo).
3. Detectar **una sola vez al arranque** si pg_trgm está disponible (`SELECT 1 FROM pg_extension WHERE extname='pg_trgm'`) y fijar la estrategia de matching, en lugar del try/except por consulta; crear la extensión vía migración (BD-01): `CREATE EXTENSION IF NOT EXISTS pg_trgm;`.
4. Hacer el matching de nombres sobre la lista cacheada con `difflib.get_close_matches` como fallback puro-Python.

**Criterios de aceptación.** Un mensaje del bot ejecuta ≤1 consulta de catálogo cuando la caché está caliente (verificado con `SQLALCHEMY_ECHO` o contador de queries en test).

---

### TG-06 — `telegram_service`: reintentos con backoff, manejo de errores y escape HTML
- **Prioridad:** Media · **Esfuerzo:** 1 día · **Dependencias:** ninguna

**Problema actual.** `services/telegram_service.py` (87 líneas) hace `requests.post(..., timeout=10)` sin reintentos; ante cualquier excepción registra el error y **devuelve `None` silenciosamente**, con lo que el usuario del bot se queda sin respuesta y sin pista (la venta pudo haberse registrado igualmente). Además envía `parse_mode='HTML'` sin escapar los textos interpolados: un cliente llamado `<b>Juan` o un producto con `&` rompe el mensaje (Telegram devuelve 400 y de nuevo, silencio).

**Solución paso a paso.**
1. Añadir reintentos con backoff exponencial para errores de red y HTTP 429/5xx (respetando `retry_after` del cuerpo de error de Telegram):

```python
import html, time, requests

def enviar_mensaje(chat_id, texto, intentos=3):
    for i in range(intentos):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 429:
                time.sleep(r.json().get('parameters', {}).get('retry_after', 2 ** i)); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == intentos - 1: raise
            time.sleep(2 ** i)
```

2. Escapar SIEMPRE los datos dinámicos con `html.escape()` antes de interpolarlos (helper `b(texto)` que devuelva `f"<b>{html.escape(str(texto))}</b>"`), ver SEG-09.
3. Si el envío falla definitivamente, registrar en log con `chat_id` y dejar rastro (tabla o log estructurado) para reintento manual; nunca `return None` mudo.
4. Con TG-01 implementado, los reintentos viven en el worker de cola, no en el request HTTP.

**Criterios de aceptación.** Test con mock de `requests` que simula 429 y 500: el mensaje termina entregado; un cliente llamado `<script>` se muestra literal en Telegram sin error 400.

---

### TG-07 — Mover `telegram_history`/`telegram_context` de columnas JSON del usuario a tabla propia
- **Prioridad:** Baja · **Esfuerzo:** 1-2 días · **Dependencias:** BD-01 (migración)

**Problema actual.** El historial conversacional se guarda en columnas JSON de `users` (`telegram_history`, límite de 10 mensajes, con el patrón copiar-lista-mutar-reasignar para que SQLAlchemy detecte el cambio) y el estado del flujo en `telegram_context`. Cada mensaje del bot implica un `UPDATE users` (fila caliente que también se toca en login), el historial no es consultable/auditable, y un fallo a mitad de conversación puede dejar `telegram_context` corrupto sin forma de inspeccionarlo.

**Solución paso a paso.**
1. Migración (BD-01): tablas `telegram_mensajes(id, user_id FK, rol, contenido, creado_en)` y `telegram_sesiones(user_id PK, contexto JSONB, actualizado_en)`.
2. Adaptar `_get_history`/`_save_history` para leer los últimos N mensajes con `ORDER BY creado_en DESC LIMIT 10` y hacer `INSERT` en lugar de reescribir el JSON completo.
3. Job/consulta de limpieza: borrar mensajes con más de 30 días.
4. Migrar datos existentes en la propia migración (leer los JSON actuales e insertarlos) y luego eliminar las columnas de `users` en una migración posterior (despliegue en dos pasos, compatible hacia atrás).

**Criterios de aceptación.** `users` ya no se actualiza por cada mensaje del bot; el historial es consultable por SQL; las conversaciones activas sobreviven la migración.

---

### TG-08 — No autocrear clientes con teléfono ficticio en ventas por lote
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** ninguna

**Problema actual.** En `_execute_ventas_lote` (`telegram_webhook_resource.py:2066-2071`), si el cliente dictado no existe se crea automáticamente con datos inventados:

```python
cliente = Cliente(
    nombre=nombre_cliente,
    telefono='999999999',
    direccion='',
    ciudad='Lima'
)
```

Esto contamina la base de clientes (duplicados por errores de transcripción de voz: "Jose", "José", "Jose P."), fabrica teléfonos falsos que luego alguien puede intentar usar, y difiere del flujo de venta individual, que pide confirmación.

**Solución paso a paso.**
1. Ante cliente no encontrado: buscar aproximados (reutilizar el matching de TG-05 / pg_trgm) y **preguntar** vía `telegram_context`: "No encuentro a 'Jose P.'. ¿Es *José Pérez*? Responde sí, o dame el nombre completo para crearlo".
2. Si el usuario confirma crear, crear el cliente con `telefono=None`, `ciudad=None` (ajustar el modelo/esquema para permitir nulos si hoy no los permite) y marcar `origen='telegram'` para poder auditarlos.
3. Nunca inventar valores de contacto; los reportes deben distinguir "sin teléfono" de un teléfono real.

**Criterios de aceptación.** Ninguna ruta del bot inserta `'999999999'`; test de flujo: nombre desconocido → pregunta → confirmación → cliente creado con campos nulos y origen marcado.

---

## 10. Servicio Gemini (GEM)

### GEM-01 — Fijar la versión del modelo Gemini y hacerla configurable
- **Prioridad:** Media · **Esfuerzo:** 0.25 día · **Dependencias:** ninguna

**Problema actual.** `services/gemini_service.py` usa el alias flotante `gemini-flash-lite-latest`, hardcodeado. Google puede cambiar el modelo subyacente en cualquier momento y alterar el comportamiento del parser de comandos (formato JSON de salida, tolerancia del prompt) **sin ningún cambio en el repo**, imposible de correlacionar con despliegues.

**Solución paso a paso.**
1. Leer el modelo de configuración con default fijado a una versión concreta:

```python
# config.py
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash-lite-001')  # versión pineada
```

2. Usar `current_app.config['GEMINI_MODEL']` (o parámetro del servicio) en la construcción del cliente.
3. Registrar el modelo usado en cada log de invocación (junto con la latencia) para poder auditar regresiones.
4. Documentar en `.env.example` (DEV-03).

**Criterios de aceptación.** Cambiar de modelo no requiere tocar código; los logs indican qué modelo respondió cada comando.

---

### GEM-02 — Registrar auditoría `ComandoVozLog` también desde el bot de Telegram
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** ninguna

**Problema actual.** `voice_resource.py:51-69` registra cada comando de voz en `ComandoVozLog` (texto, intención, éxito, duración), pero el bot de Telegram —que ejecuta exactamente el mismo tipo de comandos con efectos de escritura sobre ventas/pagos/inventario— **no registra nada**. No hay trazabilidad de qué dijo el usuario cuando una venta del bot resulta incorrecta. Nota menor adicional: en `voice_resource.py:298`, `duration_ms` puede no estar definida si la excepción ocurre antes de su asignación (usar `duration_ms = None` inicial).

**Solución paso a paso.**
1. Extraer un helper `registrar_comando(usuario_id, canal, texto, intencion, exito, detalle, duracion_ms)` en `services/` que ambos canales llamen; añadir columna `canal` (`'voz' | 'telegram'`) a `ComandoVozLog` vía migración (BD-01) — o renombrar conceptualmente la tabla a `comandos_log`.
2. Llamarlo en el webhook tras cada `_execute_*` (éxito o error), incluyendo el texto original del mensaje y la acción JSON devuelta por Gemini.
3. Inicializar `duration_ms = None` al inicio del try en `voice_resource.py` para evitar el `NameError` latente.

**Criterios de aceptación.** Cada comando del bot deja una fila de auditoría; consulta SQL puede reconstruir "qué se dictó y qué se ejecutó" para cualquier venta creada por Telegram.

---

### GEM-03 — Inicialización perezosa del cliente Gemini + endpoint de health
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** DEV-02 (health)

**Problema actual.** `gemini_service.py` crea una instancia global **al importar el módulo**; si `GEMINI_API_KEY` falta, el servicio queda deshabilitado de forma silenciosa y los endpoints de voz/bot fallan más tarde con mensajes genéricos. El error de configuración se descubre con el primer usuario, no en el despliegue.

**Solución paso a paso.**
1. Patrón de inicialización perezosa con verificación explícita:

```python
_cliente = None

def get_gemini():
    global _cliente
    if _cliente is None:
        api_key = current_app.config.get('GEMINI_API_KEY')
        if not api_key:
            raise RuntimeError('GEMINI_API_KEY no configurada')
        _cliente = _crear_cliente(api_key)
    return _cliente
```

2. En la validación de arranque (DEV-03), comprobar la presencia de `GEMINI_API_KEY` y fallar el despliegue si falta (o loguear WARNING explícito si el bot es opcional).
3. Exponer el estado en `/health` (DEV-02): `{"gemini": "configurado" | "ausente"}` sin llamar a la API externa.

**Criterios de aceptación.** Arrancar sin API key produce un error/advertencia visible en logs de arranque; `/health` refleja el estado; los endpoints devuelven 503 con mensaje claro ("servicio de IA no disponible") en lugar de errores genéricos.

---

### GEM-04 — Reintentos con jitter no bloqueantes y presupuesto de latencia
- **Prioridad:** Baja · **Esfuerzo:** 0.5 día · **Dependencias:** TG-01 (mover a worker)

**Problema actual.** El retry de `gemini_service.py` usa `time.sleep` bloqueante dentro del request HTTP: con gunicorn en 3 workers × 2 threads (`Dockerfile`), dos comandos con reintentos simultáneos consumen un tercio de la capacidad total de la API mientras duermen.

**Solución paso a paso.**
1. Con TG-01/cola implementada, los reintentos del bot viven en el worker RQ (donde `sleep` es aceptable). Para `voice_resource` (request síncrono), fijar **presupuesto total**: máx. 2 intentos y ~10 s acumulados; si se agota, responder 503 "inténtalo de nuevo".
2. Añadir jitter al backoff (`sleep(base * 2**i + random.uniform(0, 0.5))`) para evitar sincronización de reintentos.
3. Registrar métrica de latencia por intento (log estructurado, DEV-04).

**Criterios de aceptación.** Ningún request HTTP puede quedar >15 s bloqueado por reintentos de Gemini; los reintentos largos solo ocurren en el worker de cola.

---

## 11. Seguridad (SEG)

> **Nota:** en esta sección se señalan ubicaciones de secretos y credenciales **sin reproducir sus valores**. Quien implemente debe además **rotar** cualquier credencial que haya estado en el historial de git.

### SEG-01 — Eliminar credencial de BD por defecto embebida en `app.py`
- **Prioridad:** Crítica · **Esfuerzo:** 0.25 día · **Dependencias:** DEV-03

**Problema actual.** `app.py:52` define el fallback de `SQLALCHEMY_DATABASE_URI` como una **cadena de conexión completa con usuario y contraseña reales embebidos** (no se reproduce aquí). Consecuencias: (a) la credencial está en el historial de git de un repo potencialmente compartido; (b) si `DATABASE_URL` no se define por error, la app se conecta silenciosamente a esa BD por defecto (posible mezcla de datos de entornos).

**Solución paso a paso.**
1. Eliminar el default y fallar en el arranque si falta la variable:

```python
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    raise RuntimeError('DATABASE_URL es obligatoria (ver .env.example)')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
```

2. **Rotar la contraseña** de esa BD inmediatamente (estuvo expuesta en git).
3. Añadir `DATABASE_URL` a `.env.example` con un valor ficticio (DEV-03).
4. Opcional: limpiar el historial (`git filter-repo`) si el repo se hace público.

**Criterios de aceptación.** `grep -rn "postgresql://" --include='*.py'` no devuelve credenciales; arrancar sin `DATABASE_URL` falla con mensaje claro; contraseña rotada.

---

### SEG-02 — Exigir `JWT_SECRET_KEY` fuerte en todos los entornos (sin default inseguro)
- **Prioridad:** Crítica · **Esfuerzo:** 0.25 día · **Dependencias:** DEV-03

**Problema actual.** `app.py:86` asigna a `JWT_SECRET_KEY` un valor por defecto conocido e inseguro cuando la variable de entorno falta; solo se lanza error si además `IS_PRODUCTION` es verdadero. Pero `IS_PRODUCTION` depende de `FLASK_ENV`, y el propio `Dockerfile:44` fija `ENV FLASK_ENV=development` en la **imagen de producción** (ver DEV-02): la protección puede no activarse nunca, dejando la firma de tokens con un secreto público → **cualquiera puede forjar JWTs de admin**.

**Solución paso a paso.**
1. Exigir siempre la variable, sin condicionar al entorno:

```python
jwt_secret = os.environ.get('JWT_SECRET_KEY')
if not jwt_secret or len(jwt_secret) < 32:
    raise RuntimeError('JWT_SECRET_KEY es obligatoria y debe tener >= 32 caracteres')
app.config['JWT_SECRET_KEY'] = jwt_secret
```

2. Generar el secreto con `python -c "import secrets; print(secrets.token_urlsafe(48))"` y documentarlo en `.env.example`.
3. Rotar el secreto actual (invalida sesiones activas; anunciarlo) porque el default estuvo en git.
4. Corregir `FLASK_ENV` en el Dockerfile (DEV-02) para que `IS_PRODUCTION` sea fiable.

**Criterios de aceptación.** Arrancar sin `JWT_SECRET_KEY` (o con una corta) falla en cualquier entorno; ningún valor default de secreto en el código.

---

### SEG-03 — Dejar de devolver `str(e)` al cliente (fuga de información)
- **Prioridad:** Alta · **Esfuerzo:** 0.5-1 día · **Dependencias:** CAL-03

**Problema actual.** Varios puntos devuelven la excepción cruda en la respuesta HTTP: `common.py:100` (decorador `handle_db_errors` → `str(e)` a todos los resources que lo usan), `dashboard_resource.py:175` (`'details': str(e)`), `produccion_resource.py:45` y `:220` (`'detalle': str(e)`). Los mensajes de SQLAlchemy/psycopg2 exponen nombres de tablas/columnas, fragmentos de SQL, hostname de la BD y hasta valores de parámetros — información valiosa para un atacante y confusa para el frontend.

**Solución paso a paso.**
1. En `handle_db_errors` (`common.py`): loguear `str(e)` con stacktrace y devolver un mensaje genérico + identificador de correlación:

```python
except SQLAlchemyError as e:
    db.session.rollback()
    error_id = uuid.uuid4().hex[:8]
    logger.exception('Error de BD [%s]', error_id)
    return {'error': 'Error interno de base de datos', 'error_id': error_id}, 500
```

2. Reemplazar los `str(e)` de `dashboard_resource.py:175` y `produccion_resource.py:45,220` por el mismo patrón (idealmente vía los error handlers globales de CAL-03).
3. Mapear `IntegrityError` a 409 con mensajes de negocio ("registro duplicado", "referencia inexistente") sin texto del driver.
4. Test: forzar un `IntegrityError` y verificar que la respuesta no contiene "psycopg2" ni nombres de tablas.

**Criterios de aceptación.** `grep -rn "str(e)" resources/ common.py` sin apariciones en cuerpos de respuesta; toda respuesta 500 lleva `error_id` correlacionable con el log.

---

### SEG-04 — Endurecer el webhook de Telegram (header secreto, rate limit, tamaño de payload)
- **Prioridad:** Alta · **Esfuerzo:** 0.5-1 día · **Dependencias:** TG-01

**Problema actual.** El webhook se autentica solo por el token en la **ruta** (`/telegram/webhook/<webhook_token>`), que queda en logs de acceso, proxies y herramientas de monitoreo. No se valida el header estándar `X-Telegram-Bot-Api-Secret-Token` que Telegram envía si se configura `secret_token` en `setWebhook`. Además el endpoint está **exento del rate limiter** y acepta payloads de tamaño arbitrario: cualquiera que descubra la URL puede inundar el endpoint (y con TG-01 sin resolver, cada request bloquea un worker y llama a Gemini → coste económico directo).

**Solución paso a paso.**
1. Configurar el webhook con secreto: `setWebhook(url=..., secret_token=<valor de entorno TELEGRAM_WEBHOOK_SECRET>)` y validar en el endpoint:

```python
if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != current_app.config['TELEGRAM_WEBHOOK_SECRET']:
    return {'error': 'no autorizado'}, 403
```

2. Comparar tokens con `hmac.compare_digest` (token de ruta y header) para evitar timing attacks.
3. Aplicar un rate limit propio (p. ej. `60/minute`) en lugar de la exención total, y `MAX_CONTENT_LENGTH` (p. ej. 1 MB) para el payload.
4. Rechazar con 200 vacío updates que no traigan `message`/`callback_query` esperados (no procesar tipos desconocidos).

**Criterios de aceptación.** Requests sin el header secreto correcto reciben 403; test de payload de 5 MB → 413; el endpoint tiene límite de tasa activo.

---

### SEG-05 — Anti fuerza bruta en `/auth` (rate limit específico + lockout progresivo)
- **Prioridad:** Alta · **Esfuerzo:** 0.5-1 día · **Dependencias:** SEG-10 (storage compartido del limiter)

**Problema actual.** `auth_resource.py` no tiene límite específico de intentos: aplica solo el límite global del limiter (que además usa `memory://`, ver SEG-10, y por tanto se resetea por worker y por reinicio). No hay lockout por cuenta ni registro de intentos fallidos → una contraseña débil de un usuario del bot puede ser forzada por diccionario sin fricción.

**Solución paso a paso.**
1. Decorar el login con un límite estricto por IP: `@limiter.limit('5 per minute; 20 per hour')`.
2. Lockout progresivo por cuenta: columnas `failed_logins`, `locked_until` en `users` (migración BD-01); tras 5 fallos, bloquear 15 min; resetear el contador en login exitoso. Responder 429 con `Retry-After` sin revelar si el usuario existe.
3. Loguear cada intento fallido con IP y usuario (DEV-04) para detección posterior.
4. Mensaje idéntico para "usuario no existe" y "contraseña incorrecta" (evitar enumeración de usuarios).

**Criterios de aceptación.** Sexto intento fallido en un minuto → 429; la cuenta se bloquea temporalmente tras fallos repetidos; test automatizado del lockout.

---

### SEG-06 — Corregir el parseo de `ALLOWED_ORIGINS` (CORS)
- **Prioridad:** Media · **Esfuerzo:** 0.25 día · **Dependencias:** ninguna

**Problema actual.** `app.py:42` parsea la variable de orígenes CORS de forma frágil: si el valor no contiene coma, se pasa como **string** a flask-cors en lugar de lista, y valores con espacios o cadena vacía producen orígenes inválidos (`''`) o comportamientos distintos según el formato. Riesgo de terminar con CORS más permisivo (o roto) de lo previsto.

**Solución paso a paso.**
1. Normalizar siempre a lista limpia:

```python
raw = os.environ.get('ALLOWED_ORIGINS', '')
origins = [o.strip() for o in raw.split(',') if o.strip()]
if not origins:
    origins = ['http://localhost:5173']  # solo para desarrollo; en producción exigir la variable
CORS(app, origins=origins, supports_credentials=True)
```

2. En producción (con `IS_PRODUCTION` ya fiable tras DEV-02), fallar si la lista queda vacía o contiene `*` junto con `supports_credentials`.
3. Test unitario del parseo con: un origen, varios, espacios, vacío.

**Criterios de aceptación.** `ALLOWED_ORIGINS='https://app.manngo.pe'` (sin coma) produce `['https://app.manngo.pe']`; producción con lista vacía no arranca.

---

### SEG-07 — Nunca ejecutar con `debug=True` derivado del entorno
- **Prioridad:** Media · **Esfuerzo:** 0.25 día · **Dependencias:** DEV-02

**Problema actual.** El bloque final de `app.py` ejecuta `app.run(debug=not IS_PRODUCTION, ...)`. Como `IS_PRODUCTION` depende de `FLASK_ENV` y el `Dockerfile:44` fija `FLASK_ENV=development`, si alguien arranca el contenedor con `python app.py` (en lugar de gunicorn) obtiene el **debugger de Werkzeug expuesto**: ejecución remota de código con el PIN de consola como única barrera.

**Solución paso a paso.**
1. Cambiar a `debug` explícito solo por variable dedicada:

```python
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000,
            debug=os.environ.get('FLASK_DEBUG') == '1')
```

2. Corregir `FLASK_ENV` en el Dockerfile (DEV-02) y documentar que producción SOLO se sirve con gunicorn.
3. Añadir a la validación de arranque (DEV-03): si `IS_PRODUCTION` y `FLASK_DEBUG=1`, abortar.

**Criterios de aceptación.** Ninguna combinación de variables de producción arranca el debugger; el default de `app.run` es `debug=False`.

---

### SEG-08 — Externalizar datos SUNAT incrustados (RUC, placa, DNI chofer, direcciones)
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** DEV-03

**Problema actual.** El flujo de guías de remisión del bot tiene datos reales incrustados en el código: RUC del emisor como fallback (`telegram_webhook_resource.py:1605`), placa vehicular `D8M790`, DNI del chofer y direcciones de partida/llegada (`:1527-1541` y `:1609-1611`). Son datos personales/fiscales en un repo de código (exposición innecesaria) y además operativamente frágiles: cambiar de vehículo o chofer requiere un despliegue.

**Solución paso a paso.**
1. Crear tabla de configuración `sunat_config` (o sección en una tabla `configuracion` clave-valor) con: RUC emisor, razón social, dirección de partida por almacén, y catálogo `vehiculos(placa)` y `choferes(dni, nombre, licencia)` — migración BD-01.
2. El flujo del bot pregunta o autocompleta desde esos catálogos (último vehículo/chofer usado como default), nunca desde constantes.
3. Retirar del código todos los literales (RUC, placa, DNI, direcciones) y cargarlos por seed/migración de datos.
4. Validar RUC (11 dígitos, checksum) y placa con formato peruano en el esquema de entrada.

**Criterios de aceptación.** `grep -rn "D8M790" .` sin resultados; cambiar chofer/vehículo es una operación de datos, no de código.

---

### SEG-09 — Escapar HTML en todos los mensajes del bot (nombres de clientes/productos)
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** TG-06

**Problema actual.** Los mensajes del bot se construyen por interpolación de f-strings con `parse_mode='HTML'` en decenas de puntos del webhook (`f"<b>{cliente.nombre}</b>"`, listados de productos, resúmenes de ventas). Cualquier nombre con `<`, `>` o `&` — introducido por la API de clientes o por el propio bot — rompe el renderizado y hace que Telegram rechace el mensaje (400), dejando al usuario sin respuesta (agravado por el `return None` silencioso de TG-06).

**Solución paso a paso.**
1. Crear helpers centralizados en `services/telegram_service.py`:

```python
import html

def esc(v) -> str:
    return html.escape(str(v), quote=False)

def negrita(v) -> str:
    return f"<b>{esc(v)}</b>"
```

2. Reemplazar todas las interpolaciones directas de datos dinámicos del webhook por `esc()`/`negrita()` (búsqueda dirigida: `grep -n "<b>{" resources/telegram_webhook_resource.py`).
3. Alternativa defensiva adicional: escapar en el punto único de envío (`enviar_mensaje`) todo salvo un conjunto blanco de etiquetas ya generadas por los helpers — documentar la elegida.
4. Test: cliente `"<b>Pepe & Cía"` aparece literal en el texto enviado (mock de requests).

**Criterios de aceptación.** Ningún dato de BD se interpola sin escape; test de nombres maliciosos en verde.

---

### SEG-10 — Rate limiter con storage Redis (hoy `memory://`, inútil con varios workers)
- **Prioridad:** Baja · **Esfuerzo:** 0.5 día · **Dependencias:** PERF-02 (mismo Redis)

**Problema actual.** `extensions.py` configura flask-limiter con `storage_uri='memory://'`. Con gunicorn en 3 workers × 2 threads (`Dockerfile`), cada worker lleva su contador **independiente**: un límite de "5/min" real se convierte en hasta 15/min, y todo contador se resetea en cada reinicio/despliegue. Los límites anti fuerza bruta (SEG-05) serían decorativos.

**Solución paso a paso.**
1. Aprovisionar Redis (el mismo de TG-01/PERF-02) y configurar:

```python
limiter = Limiter(key_func=get_remote_address,
                  storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'))
```

con `RATELIMIT_STORAGE_URI=redis://...` en producción (documentado en `.env.example`).
2. Validación de arranque (DEV-03): en producción, advertir/fallar si el storage sigue siendo `memory://`.
3. Verificar que `key_func` considera `X-Forwarded-For` correctamente si hay proxy delante (usar `ProxyFix` con la profundidad correcta, no confiar ciegamente en el header).

**Criterios de aceptación.** Los contadores de límite son consistentes entre workers (test: exceder el límite repartiendo requests) y sobreviven reinicios de un worker.

---

## 12. Rendimiento (PERF)

### PERF-01 — Reemplazar propiedades `saldo_pendiente` por agregados SQL
- **Prioridad:** Crítica · **Esfuerzo:** 1-2 días · **Dependencias:** ninguna

**Problema actual.** `models.py:253-259` define `Cliente.saldo_pendiente` como property Python que **itera todas las ventas del cliente y, por cada venta, todos sus pagos**; `Venta.saldo_pendiente` (`models.py:156-159`) hace lo propio con los pagos. Estas properties se serializan en `ClienteSchema` (cada listado de clientes) y se usan en el bot (`_prepare_pago`, `telegram_webhook_resource.py:559`). Efecto: **N+1 al cuadrado** — listar 200 clientes dispara cientos de consultas (ventas por cliente + pagos por venta). Es, junto con VEN-01, el mayor problema de rendimiento del sistema y crecerá linealmente con el historial de ventas.

**Solución paso a paso.**
1. Crear consulta agregada única para listados:

```python
saldos = dict(db.session.query(
        Venta.cliente_id,
        func.coalesce(func.sum(Venta.total), 0) -
        func.coalesce(func.sum(sub_pagos.c.pagado), 0))
    .outerjoin(sub_pagos, sub_pagos.c.venta_id == Venta.id)
    .group_by(Venta.cliente_id).all())
# donde sub_pagos = select(Pago.venta_id, func.sum(Pago.monto).label('pagado')).group_by(Pago.venta_id).subquery()
```

y exponer `saldo_pendiente` en el schema desde ese diccionario (método `dump` con contexto) en lugar de la property.
2. Alternativa por fila: `column_property` con subconsulta escalar correlacionada en `Venta` (`total - COALESCE(SELECT SUM(monto) FROM pagos WHERE venta_id = ventas.id, 0)`) — una sola query por listado de ventas.
3. Mantener la property Python solo para usos unitarios (detalle de un cliente) o eliminarla para evitar regresiones accidentales; en el bot, calcular el saldo con la consulta agregada filtrada por cliente.
4. Índices de apoyo (coordinar con BD-03): `pagos(venta_id)`, `ventas(cliente_id)` — verificar contra `indexes.md`.
5. Medir antes/después con `EXPLAIN ANALYZE` y un dataset sintético de 1 000 clientes × 50 ventas.

**Criterios de aceptación.** Listar clientes con saldo ejecuta un número constante de consultas (≤3) independiente del número de clientes/ventas (verificado con contador de queries en test); resultados idénticos a la property original en un dataset de control.

---

### PERF-02 — Capa de caché (Redis) para catálogos y form-data
- **Prioridad:** Media · **Esfuerzo:** 1-2 días · **Dependencias:** SEG-10/TG-01 (mismo Redis)

**Problema actual.** Catálogos casi estáticos (almacenes, presentaciones, productos, listas para formularios que el frontend pide al cargar cada pantalla) se consultan a BD en cada request. Sumado al patrón del bot (TG-05), la BD recibe carga repetitiva de datos que cambian pocas veces al día.

**Solución paso a paso.**
1. Introducir `flask-caching` con backend Redis (`CACHE_REDIS_URL` en config; fijar versión en `requirements.txt`).
2. Decorar los GET de catálogo: `@cache.cached(timeout=300, query_string=True)` en los listados de `almacen_resource`, `presentacion_resource`, `producto_resource` y los endpoints de form-data del dashboard.
3. Invalidación explícita en los POST/PUT/DELETE correspondientes (`cache.delete_memoized`/claves por prefijo) para no servir catálogos obsoletos tras una edición.
4. No cachear nada dependiente del usuario/JWT sin incluir la identidad en la clave (regla documentada).
5. Métrica simple de hit-rate en logs (DEV-04) para validar utilidad.

**Criterios de aceptación.** Segundo GET consecutivo de un catálogo no toca la BD (contador de queries); editar una presentación refleja el cambio en el siguiente GET.

---

### PERF-03 — Paginación obligatoria en todos los listados
- **Prioridad:** Media · **Esfuerzo:** 0.5-1 día · **Dependencias:** ninguna

**Problema actual.** Varios listados devuelven tablas completas sin límite: el patrón `query.all()` aparece en resources y en el bot (`Almacen.query.all()`, listados de `cliente_resource.py` —771 líneas— y otros), pese a que `common.py` ya define `MAX_ITEMS_PER_PAGE`. Con el crecimiento de ventas/clientes, estos endpoints degradarán linealmente (memoria + serialización marshmallow, agravado por PERF-01).

**Solución paso a paso.**
1. Auditar con `grep -rn "\.all()" resources/` y clasificar: catálogos pequeños (aceptable + caché PERF-02) vs. tablas crecientes (ventas, pagos, clientes, movimientos, gastos → paginar SIEMPRE).
2. Helper único en `common.py`:

```python
def paginar(query, schema):
    page = min(request.args.get('page', 1, type=int), 10_000)
    per_page = min(request.args.get('per_page', 20, type=int), MAX_ITEMS_PER_PAGE)
    p = query.paginate(page=page, per_page=per_page, error_out=False)
    return {'data': schema.dump(p.items, many=True),
            'pagination': {'page': p.page, 'per_page': p.per_page,
                           'total': p.total, 'pages': p.pages}}
```

2. Aplicarlo a todos los listados crecientes con orden estable (`ORDER BY id DESC` o fecha) para paginación determinista.
3. Coordinar con el frontend el formato `{data, pagination}` (mantener compatibilidad si algún endpoint ya pagina con otro formato: unificar).

**Criterios de aceptación.** Ningún endpoint de tabla creciente responde sin límite; pedir `per_page=100000` devuelve como máximo `MAX_ITEMS_PER_PAGE`.

---

## 13. Calidad de código y pruebas (CAL)

### CAL-01 — Suite de tests real con pytest + fixtures + CI
- **Prioridad:** Alta · **Esfuerzo:** 3-5 días · **Dependencias:** ninguna (habilita casi todo lo demás)

**Problema actual.** El único "test" del repo es `test_telegram_webhook.py`: un **script** de 1 154 líneas con `run_tests()` manual, **cero funciones `def test_*`** (pytest no recolecta nada), que además **muta la base de datos real con `ALTER TABLE` directos** (líneas 25-41) para "preparar" columnas. No hay aserciones automatizables, ni aislamiento, ni forma de ejecutar verificaciones en CI. Todas las mejoras críticas de este plan (VEN-01, BD-02, TG-01…) son imposibles de validar con seguridad sin tests.

**Solución paso a paso.**
1. Estructura estándar:

```
tests/
  conftest.py          # app de test, BD efímera, fixtures de datos
  test_auth.py
  test_ventas.py       # incluye concurrencia de stock (VEN-01)
  test_inventario.py
  test_telegram.py     # webhook con Gemini mockeado
```

2. `conftest.py` con BD PostgreSQL efímera (contenedor de servicio en CI; localmente `pytest-postgresql` o una BD `manngo_test`), `db.create_all()` por sesión y transacción con rollback por test:

```python
@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture(autouse=True)
def _tx(app):
    with app.app_context():
        db.session.begin_nested()
        yield
        db.session.rollback()
```

3. Mockear Gemini y Telegram (`monkeypatch` de `gemini_service`/`telegram_service`) — ningún test llama servicios externos.
4. Reescribir los escenarios útiles del script actual como tests reales y **eliminar** los `ALTER TABLE` (las columnas se gestionan por migraciones, BD-01). Borrar el script una vez migrado.
5. Objetivo inicial de cobertura: auth, creación de venta (feliz + stock insuficiente + concurrencia), pagos, webhook (dedupe + venta por voz). Medir con `pytest-cov`; umbral inicial 60 % en `resources/` y `services/`.

**Criterios de aceptación.** `pytest -q` en verde local y en CI (DEV-01); ningún test toca BD/servicios reales; el script antiguo eliminado; cobertura reportada en CI.

---

### CAL-02 — Linting y formateo automáticos (ruff + pre-commit)
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** ninguna

**Problema actual.** No hay linter ni formateador configurados: conviven estilos distintos, imports sin usar, f-strings sin placeholders, comparaciones `== False` (p. ej. en queries del webhook), y nada impide que se reintroduzcan errores triviales (variables no definidas como el `duration_ms` de GEM-02).

**Solución paso a paso.**
1. Añadir `ruff` (linter + formateador) con configuración en `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "S110"]  # S110 = try/except/pass
ignore = ["E501"]  # transitorio hasta formatear
```

2. `pre-commit` con hooks de ruff (lint + format) y `check-merge-conflict`, `end-of-file-fixer`.
3. Primera pasada: `ruff check --fix .` + `ruff format .` en un commit propio "solo formato" para no ensuciar diffs funcionales.
4. Nota: en queries SQLAlchemy, `== False` es intencional; marcar con `# noqa: E712` o usar `.is_(False)`.
5. Integrar en CI (DEV-01) como job obligatorio.

**Criterios de aceptación.** `ruff check .` limpio en CI; pre-commit instalado y documentado en el README.

---

### CAL-03 — Manejo de errores homogéneo con error handlers globales
- **Prioridad:** Media · **Esfuerzo:** 1 día · **Dependencias:** SEG-03

**Problema actual.** Los formatos de error están mezclados: `{'error': ...}`, `{'message': ...}`, `{'error': ..., 'details': str(e)}` (dashboard), `{'error': ..., 'detalle': str(e)}` (producción), respuestas de marshmallow con su propio formato, y errores de flask-restful/JWT con otros. El frontend y el bot deben adivinar el formato según el endpoint; cada resource repite try/except similares.

**Solución paso a paso.**
1. Definir contrato único documentado: `{"error": {"code": "STOCK_INSUFICIENTE", "message": "...", "details": {...}, "error_id": "..."}}`.
2. Registrar handlers globales en `app.py`:

```python
@app.errorhandler(ValidationError)      # marshmallow
def _val(e): return {'error': {'code': 'VALIDACION', 'message': 'Datos inválidos', 'details': e.messages}}, 400

@app.errorhandler(IntegrityError)
def _integ(e): db.session.rollback(); return {'error': {'code': 'CONFLICTO', 'message': 'Conflicto de datos'}}, 409

@app.errorhandler(Exception)
def _any(e): db.session.rollback(); logger.exception('No controlado'); return {'error': {'code': 'INTERNO', 'message': 'Error interno', 'error_id': ...}}, 500
```

3. Crear excepciones de dominio (`StockInsuficienteError`, `RecursoNoEncontrado`) lanzadas por los servicios (ARQ-01) y mapeadas por handlers → los resources dejan de capturar y formatear.
4. Ir eliminando try/except redundantes de los resources en el mismo PR que migra cada uno (aprovechar TG-03/ARQ-01).
5. Actualizar el frontend/consumidores sobre el contrato (changelog de API).

**Criterios de aceptación.** Todos los errores 4xx/5xx comparten el mismo esquema JSON (test de contrato sobre 5 endpoints representativos); ningún `str(e)` en respuestas.

---

### CAL-04 — Limpieza de código muerto, comentarios "MEJORA:" y docs desactualizadas
- **Prioridad:** Baja · **Esfuerzo:** 0.5-1 día · **Dependencias:** mejor tras ARQ-01/TG-03

**Problema actual.** El código arrastra: comentarios `# MEJORA:` que describen cambios ya hechos o nunca hechos (sin tracking), ramas muertas (p. ej. el manejo de `tzinfo` imposible de TG-04), imports sin uso, y documentación (`docs/`, `indexes.md`, README) que no refleja el estado real — `indexes.md` de hecho contradice a `models.py` (ver BD-02), y la doc de despliegue no menciona variables hoy obligatorias.

**Solución paso a paso.**
1. Convertir cada `# MEJORA:` vigente en un issue de GitHub referenciando el ID de este plan y borrar el comentario; borrar los obsoletos.
2. Eliminar código muerto detectado por ruff (`F401`, `F841`) y las ramas inalcanzables identificadas en este análisis.
3. Actualizar `docs/`: variables de entorno (enlazar `.env.example` de DEV-03), procesos (gunicorn + worker RQ de TG-01), y regenerar `indexes.md` desde la BD real (o mejor: eliminarlo en favor de migraciones BD-01 como fuente de verdad).
4. README: cómo levantar en local (docker compose con Postgres+Redis), correr tests, y convenciones.

**Criterios de aceptación.** Cero comentarios `# MEJORA:`; docs regeneradas coinciden con el código; un desarrollador nuevo levanta el entorno solo con el README.

---

## 14. DevOps y operación (DEV)

### DEV-01 — Pipeline CI/CD con GitHub Actions (lint + tests + build Docker)
- **Prioridad:** Alta · **Esfuerzo:** 1 día · **Dependencias:** CAL-01, CAL-02

**Problema actual.** No existe `.github/workflows/`: nada valida los PRs; cualquier commit puede romper producción sin aviso. Todas las garantías de este plan dependen de que los tests corran automáticamente.

**Solución paso a paso.**
1. Crear `.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: test, POSTGRES_DB: manngo_test }
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: ruff check .
      - run: pytest -q --cov=resources --cov=services
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/manngo_test
          JWT_SECRET_KEY: ${{ github.run_id }}-secret-solo-para-ci-0123456789
  docker:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t apimanngo:ci .
```

2. Proteger la rama principal: PRs obligatorios con CI en verde.
3. (Opcional, fase posterior) job de despliegue con aprobación manual.

**Criterios de aceptación.** Todo PR muestra checks de lint+tests+build; un test roto bloquea el merge.

---

### DEV-02 — Endpoint `/health` + `HEALTHCHECK` Docker + `FLASK_ENV=production`
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** ninguna

**Problema actual.** (a) No hay endpoint de salud: el orquestador/monitor no puede distinguir "proceso vivo" de "app funcional con BD accesible". (b) El `Dockerfile` no define `HEALTHCHECK`. (c) **`Dockerfile:44` fija `ENV FLASK_ENV=development` en la imagen de producción**, lo que desactiva las protecciones condicionadas a `IS_PRODUCTION` (ver SEG-02, SEG-07) — probablemente un descuido con consecuencias de seguridad en cadena.

**Solución paso a paso.**
1. Endpoint mínimo sin auth y exento de rate limit:

```python
@app.route('/health')
def health():
    try:
        db.session.execute(text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False
    estado = 200 if db_ok else 503
    return {'status': 'ok' if db_ok else 'degraded', 'db': db_ok,
            'gemini': bool(app.config.get('GEMINI_API_KEY'))}, estado
```

2. Dockerfile: cambiar `ENV FLASK_ENV=development` → `ENV FLASK_ENV=production` y añadir:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1
```

3. Verificar tras el cambio que `IS_PRODUCTION` activa las validaciones de SEG-02/SEG-07 (test de arranque).

**Criterios de aceptación.** `curl /health` responde 200 con BD arriba y 503 con BD caída; `docker inspect` muestra el healthcheck; la imagen declara `FLASK_ENV=production`.

---

### DEV-03 — Gestión de secretos: `.env.example`, validación al arranque, rotación
- **Prioridad:** Media · **Esfuerzo:** 0.5 día · **Dependencias:** SEG-01, SEG-02

**Problema actual.** No existe `.env.example`; las variables necesarias (BD, JWT, Gemini, Telegram, CORS, SUNAT) solo se descubren leyendo el código, y varias tienen defaults inseguros (SEG-01/SEG-02). Nada valida la configuración al arranque: los errores aparecen en runtime frente al usuario.

**Solución paso a paso.**
1. Crear `.env.example` con TODAS las variables (valores ficticios) y comentario de propósito: `DATABASE_URL`, `JWT_SECRET_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `ALLOWED_ORIGINS`, `RATELIMIT_STORAGE_URI`, `CACHE_REDIS_URL`, credenciales SUNAT, `FLASK_ENV`, `FLASK_DEBUG`.
2. Función `validar_config(app)` llamada al final de `create_app`/inicialización:

```python
OBLIGATORIAS = ['DATABASE_URL', 'JWT_SECRET_KEY']
PROD_OBLIGATORIAS = ['ALLOWED_ORIGINS', 'RATELIMIT_STORAGE_URI']

def validar_config(app):
    faltan = [v for v in OBLIGATORIAS if not os.environ.get(v)]
    if app.config['IS_PRODUCTION']:
        faltan += [v for v in PROD_OBLIGATORIAS if not os.environ.get(v)]
    if faltan:
        raise RuntimeError(f'Variables de entorno faltantes: {", ".join(faltan)}')
```

3. Confirmar que `.env` está en `.gitignore` y **rotar** todo secreto que haya aparecido alguna vez en el historial (BD de SEG-01, JWT de SEG-02, tokens de Telegram/Gemini si estuvieron en commits).
4. Documentar el procedimiento de rotación en `docs/operacion.md`.

**Criterios de aceptación.** Clonar el repo + copiar `.env.example` + rellenar = app arranca; faltar una variable obligatoria aborta el arranque con mensaje que la nombra; checklist de rotación completada.

---

### DEV-04 — Logging estructurado (JSON) + monitoreo de errores (Sentry)
- **Prioridad:** Baja · **Esfuerzo:** 1 día · **Dependencias:** CAL-03 (error_id)

**Problema actual.** Los logs son texto plano heterogéneo (`print`/`logger` mezclados según el módulo) sin campos estructurados: imposible filtrar por usuario, endpoint o `error_id` (SEG-03), ni medir latencias de Gemini (GEM-04). No hay agregación de errores: las excepciones de producción solo existen si alguien lee los logs del contenedor a tiempo.

**Solución paso a paso.**
1. `python-json-logger` (o `structlog`) con formato único: `timestamp, level, logger, message, request_id, user_id, path, duration_ms, error_id`.
2. Middleware `before_request/after_request` que genere `request_id` (header `X-Request-ID` si viene del proxy) y loguee método, ruta, estado y duración.
3. Integrar Sentry (`sentry-sdk[flask]`) activado solo si `SENTRY_DSN` está definido; enlazar `error_id` de CAL-03 como tag.
4. Reemplazar los `print(...)` residuales por logger; nivel por variable `LOG_LEVEL`.
5. Log dedicado para comandos de IA: modelo, latencia, éxito (complementa GEM-01/GEM-02).

**Criterios de aceptación.** Los logs de un request son JSON parseable con `request_id` común; una excepción forzada aparece en Sentry con su `error_id`; cero `print` en `resources/` y `services/`.

---

## 15. Cómo usar este documento

1. **Orden de ejecución:** seguir el roadmap de la sección 3 (Fase 0 → 4). Dentro de cada fase, respetar el campo **Dependencias** de cada ficha; las mejoras sin dependencias pueden paralelizarse.
2. **Una mejora = un PR:** cada ID (p. ej. `VEN-01`) debe implementarse en una rama/PR propia titulada con su ID, citando sus criterios de aceptación en la descripción. No mezclar IDs salvo dependencia directa (indicada en la ficha).
3. **Criterios de aceptación como definición de "hecho":** un ID no se cierra hasta que todos sus criterios se verifican (idealmente con un test automatizado que quede en la suite de CAL-01).
4. **Referencias de código:** todos los `archivo:línea` corresponden al commit `2b14595` del repositorio `eddynorris/apiManngo`; si la base avanza, localizar los fragmentos citados por su contenido.
5. **Secretos:** las fichas SEG-01/SEG-02 y la nota de la sección 11 señalan credenciales presentes en el código **sin reproducirlas**; toda implementación debe incluir su **rotación**, no solo su retirada del código.

---

*Fin del plan — 57 mejoras (8 críticas, 15 altas, 27 medias, 7 bajas) sobre el análisis completo del repositorio `eddynorris/apiManngo`.*
