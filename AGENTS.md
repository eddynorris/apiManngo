# Project Context & AI Guidelines: apiFlaskManngo (AGENTS.md)

Este documento es la referencia canónica y exhaustiva del backend **apiFlaskManngo**. Ha sido diseñado para que cualquier agente de Inteligencia Artificial (Claude, Gemini, GPT, DeepSeek, Cursor, Trae, Roo, etc.) o desarrollador comprenda a profundidad la arquitectura, reglas de negocio, modelos de datos, endpoints, convenciones y flujos operativos del sistema.

---

## 1. Visión General del Proyecto

**apiFlaskManngo** es una API RESTful empresarial de alto rendimiento desarrollada en Python con Flask. Funciona como el núcleo transaccional para la empresa **Manngo**, especializada en la producción, ensacado, distribución y comercialización de carbón vegetal, briquetas ecológicas, presentaciones comerciales e insumos.

### Capacidades Principales
1. **Gestión Multialmacén & Stock en Tiempo Real**: Trazabilidad de lotes, pesos húmedo/seco, control estricto de existencias por almacén/planta y transferencias entre almacenes.
2. **Transformación Productiva & Recetas (BOM)**: Conversión de materia prima bruta en producto final o briquetas, control de mermas, ensamblaje basado en recetas e inventario de insumos (sacos, hilos, etiquetas).
3. **Flujo Comercial Integral**: Gestión de pedidos (preventa), conversión a ventas, facturación flexible (contado/crédito), amortizaciones parciales, cobranzas por lotes y arqueos de caja.
4. **Tesorería & Conciliación Bancaria**: Trazabilidad de dinero en manos de gerencia vs. depósitos bancarios efectivos en cuenta corporativa.
5. **Inteligencia Comercial y Fidelización**: Proyecciones automáticas de recompra de clientes basadas en consumo diario y algoritmos de recurrencia.
6. **IA Generativa & Procesamiento de Lenguaje Natural (Gemini)**: Endpoints para comandos de voz, chat operativo y parseo inteligente de operaciones.
7. **Bot Transaccional de Telegram**: Asistente conversacional multi-paso con máquina de estados finita (FSM) para registrar ventas, cobros, transferencias, producción y consultar stock desde campo.
8. **Integración Tributaria SUNAT (Perú)**: Emisión y consulta de Guías de Remisión Electrónica (GRE) Remitente vía API REST JSON con autenticación OAuth2.

---

## 2. Stack Tecnológico

| Componente | Tecnología / Librería | Propósito |
|---|---|---|
| **Framework Base** | Flask 3.x, Flask-RESTful | Ruteo y arquitectura basada en recursos |
| **Base de Datos & ORM** | PostgreSQL, SQLAlchemy 2.x, Flask-SQLAlchemy | Persistencia relacional, pooling y constraints |
| **Migraciones** | Alembic, Flask-Migrate | Control de versiones de esquema de base de datos |
| **Serialización / Validación** | Marshmallow, Flask-Marshmallow | Validación estricta de esquemas entrada/salida |
| **Autenticación & Autorización** | Flask-JWT-Extended | Tokens JWT, RBAC (`admin`, `gerente`, `usuario`) |
| **Seguridad & Rate Limiting** | Flask-Talisman, Flask-Limiter, Gzip Compress | Headers HTTP seguros, CSP, limitador de peticiones |
| **IA / LLM** | Google Generative AI (`google-genai` / Gemini 2.5 Flash) | Parseo de voz, chat corporativo y extracción de entidades |
| **Bot Conversacional** | python-telegram-bot / Telegram Bot API Webhook | Interacción operativa en campo con State Machine |
| **Almacenamiento de Archivos** | AWS S3 / Supabase Storage | Comprobantes de pago, fotos de presentaciones y documentos |
| **Integración Fiscal** | Requests / OAuth2 SUNAT | Conexión con Servicios Web oficiales de SUNAT Perú |
| **Testing** | Pytest | Pruebas unitarias e integración con base en memoria |

---

## 3. Arquitectura del Código

```text
apiFlaskManngo/
├── app.py                      # Punto de entrada de la aplicación, configuración y middlewares
├── common.py                   # Decoradores (@handle_db_errors, @rol_requerido, @mismo_almacen_o_admin), utilidades
├── config.py                   # Configuraciones globales (límites de paginación, etc.)
├── extensions.py               # Instancias de extensiones Flask (db, jwt, swagger, migrate)
├── models.py                   # Modelos relacionales SQLAlchemy y constraints de negocio
├── schemas.py                  # Esquemas Marshmallow para serialización y validación
├── resources/                  # Controladores RESTful (Flask-RESTful Resources)
│   ├── __init__.py             # Registro de todas las rutas y limitadores en la API
│   ├── auth_resource.py        # Login, renovación de token
│   ├── user_resource.py        # CRUD de usuarios y asignación de almacenes
│   ├── producto_resource.py    # Catálogo de productos base
│   ├── presentacion_resource.py# Presentaciones comerciales, tipos y precios
│   ├── almacen_resource.py     # Almacenes y plantas de producción
│   ├── inventario_resource.py  # Stock por almacén, alertas y reporte global
│   ├── transferencia_resource.py # Transferencias de stock entre almacenes
│   ├── lote_resource.py        # Gestión de lotes de carbón y materia prima
│   ├── produccion_resource.py  # Registro de producción y ensamblaje por receta
│   ├── receta_resource.py      # Definición de fórmulas/recetas (BOM)
│   ├── merma_resource.py       # Registro de mermas y su reutilización
│   ├── cliente_resource.py     # Clientes, proyecciones de recompra y exportación
│   ├── proveedor_resource.py   # Gestión de proveedores
│   ├── pedido_resource.py      # Pedidos/preventa y conversión a venta
│   ├── venta_resource.py       # Ventas, filtros avanzados, exportación
│   ├── ventadetalle_resource.py# Detalles individuales de líneas de venta
│   ├── pago_resource.py        # Pagos, amortizaciones, pagos batch, depósitos bancarios, cierre de caja
│   ├── gasto_resource.py       # Gastos operativos y logísticos
│   ├── compra_insumo_resource.py# Compras de insumos y alertas de reposición
│   ├── dashboard_resource.py   # Métricas y KPIs de panel de control
│   ├── reporte_financiero_resource.py # Balances, ventas por presentación, depósitos
│   ├── reporte_produccion_resource.py # Reportes de briquetas y producción general
│   ├── voice_resource.py       # Procesamiento de comandos de voz con Gemini
│   ├── chat_resource.py        # Chat contextual de consulta
│   ├── telegram_webhook_resource.py # Webhook y vinculación de cuentas Telegram
│   └── transaccion_resource.py # Endpoint transaccional unificado (Venta + Pago + Stock)
├── services/                   # Capa de servicios e integraciones externas
│   ├── gemini_service.py       # Interacción con Gemini (prompts estructurados, audio, JSON mode)
│   ├── pago_service.py         # Lógica de registro de pagos y actualización de estados
│   ├── produccion_service.py   # Lógica de ensamblaje, deducción de insumos y materia prima
│   ├── stock_service.py        # Validaciones y movimientos atómicos de stock
│   ├── sunat_service.py        # Autenticación OAuth2 y despacho de Guías de Remisión (GRE)
│   ├── telegram_service.py     # Envío de mensajes y keyboards a la API de Telegram
│   └── venta_service.py        # Creación y anulación de ventas con movimientos de kardex
├── telegram/                   # Módulo del Asistente Bot de Telegram
│   ├── context.py              # Gestión de estado temporal e historial en memoria/JSON
│   ├── handlers/               # Manejadores de intenciones (venta, pago, produccion, guia, consulta)
│   ├── resolvers.py            # Resolución difusa de nombres de productos, clientes y almacenes
│   ├── router.py               # Enrutador principal de mensajes y control de errores seguros
│   ├── state_machine.py        # Máquina de estados conversacional multi-paso
│   └── ui.py                   # Generación de teclados interactivos Inline/Reply
├── utils/                      # Utilidades accesorias
│   ├── date_parser.py          # Parseo de fechas naturales y formatos mixtos
│   ├── date_utils.py           # Conversiones de zona horaria Perú (`America/Lima`) y UTC
│   ├── file_handlers.py        # Subida y borrado de archivos en S3 / almacenamiento local
│   └── logger_config.py        # Configuración de logging estructurado
├── scripts/                    # Scripts utilitarios y de sincronización
│   └── sync_supabase.py        # Comando CLI `flask sync-supabase`
└── tests/                      # Suite completa de tests unitarios y de integración
```

---

## 4. Modelo de Datos Relacional

### Entidades Clave y Relaciones
- **`Users`**: Usuarios del sistema. Roles: `admin`, `gerente`, `usuario`. Vinculación a un almacén (`almacen_id`) y campos para Telegram (`telegram_chat_id`, `telegram_linking_code`).
- **`Producto`**: Producto general (ej. "Carbón Vegetal", "Briqueta").
- **`PresentacionProducto`**: Presentación comercial específica (ej. "Bolsa 5kg Supermercado", "Saco 20kg Granel", "Briqueta Caja 3kg", "Saco Vacío"). Tipos: `'bruto'`, `'procesado'`, `'merma'`, `'briqueta'`, `'detalle'`, `'insumo'`.
- **`Lote`**: Lote de materia prima o producción. Trazabilidad de origen (`lote_origen_id`), peso húmedo/seco y disponibilidad.
- **`Almacen`**: Ubicaciones físicas. Distingue almacenes comerciales de plantas (`es_planta=True`).
- **`Inventario`**: Existencia actual. Clave compuesta única (`presentacion_id`, `almacen_id`, `lote_id`).
- **`Venta` & `VentaDetalle`**: Cabecera y detalle de venta. Control de `tipo_pago` (`contado`, `credito`), `estado_pago` (`pendiente`, `parcial`, `pagado`) y `estado` (`pedido`, `completado`).
- **`Pago`**: Transacciones financieras asociadas a una venta. Métodos: `efectivo`, `deposito`, `transferencia`, `tarjeta`, `yape_plin`, `otro`. Seguimiento de depósitos (`depositado=True`, `monto_depositado`, `fecha_deposito`).
- **`Movimiento`**: Kardex general de inventario. Tipo `entrada`/`salida`, trazabilidad de operación (`produccion`, `venta`, `ajuste`, `merma`, `transferencia`, `ensamblaje`, `compra`).
- **`Receta` & `ComponenteReceta`**: Lista de materiales (BOM). Relaciona una presentación final con los insumos/materia prima consumidos por cada unidad fabricada.
- **`Cliente` & `VistaClienteProyeccion`**: Clientes con cálculo de saldo pendiente, frecuencia de compra en días, estimación de próxima compra y días de retraso.
- **`Gasto`**: Gastos operativos categorizados (`logistica`, `personal`, `insumos`, `otros`).
- **`Merma`**: Registro de pérdidas o subproductos aprovechables para briquetas.
- **`ComandoVozLog`**: Registro de auditoría de comandos de voz procesados por IA.

---

## 5. Módulos y Endpoints de la API

Todos los endpoints requieren el header `Authorization: Bearer <JWT_TOKEN>` salvo `/auth` y `/health`.

### 5.1 Autenticación & Usuarios
- `POST /auth`: Autenticación con `username` y `password`. Retorna JWT.
- `GET /usuarios`: Listado de usuarios (Admin/Gerente).
- `POST /usuarios`: Crear nuevo usuario.
- `GET /usuarios/<id>` | `PUT /usuarios/<id>` | `DELETE /usuarios/<id>`: Gestión de usuario.

### 5.2 Catálogo & Almacenes
- `GET/POST /productos`, `GET/PUT/DELETE /productos/<id>`
- `GET/POST /presentaciones`, `GET/PUT/DELETE /presentaciones/<id>`
- `GET/POST /almacenes`, `GET/PUT/DELETE /almacenes/<id>`
- `GET/POST /proveedores`, `GET/PUT/DELETE /proveedores/<id>`
- `GET/POST /lotes`, `GET/PUT/DELETE /lotes/<id>`

### 5.3 Inventario & Movimientos
- `GET /inventarios`: Stock filtrado por almacén, producto o presentación.
- `GET /inventario/reporte-global`: Stock consolidado de todos los almacenes.
- `POST /inventario/transferir`: Transferencia atómica de stock entre dos almacenes.
- `GET /inventario/alertas-insumos`: Insumos por debajo de su stock mínimo.
- `GET/POST /movimientos`, `GET /movimientos/<id>`: Consulta y registro manual de kardex.

### 5.4 Producción & Recetas (BOM)
- `GET/POST /recetas`, `GET/PUT/DELETE /recetas/<id>`: Gestión de recetas de ensamble.
- `POST /produccion`: Registro de producción por operarios (descuenta materia prima/insumos y da entrada al producto final).
- `POST /produccion/ensamblaje`: Motor de ensamblaje automático interno.
- `GET /reportes/produccion-general` | `GET /reportes/produccion-briquetas`: Informes de rendimiento y turnos.
- `GET /reportes/produccion-entradas` (alias `/reportes/movimientos-entrada`): Reporte exhaustivo de entradas por producción (desglosado por lotes, fechas, presentaciones) y traslados recibidos entre almacenes.

### 5.5 Ventas & Pedidos
- `GET/POST /ventas`, `GET/PUT/DELETE /ventas/<id>`: Gestión principal de ventas.
- `GET /ventas/form-data`: Catálogos precargados para interfaces de venta rápida.
- `GET /ventas/filtros`: Opciones de filtrado dinámico.
- `GET /ventas/exportar`: Exportación de ventas a Excel/CSV.
- `GET/POST /pedidos`, `GET/PUT/DELETE /pedidos/<id>`: Preventa y pedidos programados.
- `POST /pedidos/<id>/convertir`: Conversión de un pedido en venta efectiva con descarga de inventario.
- `POST /transacciones/venta-completa`: Operación atómica combinada (Venta + Detalles + Pago inicial + Kardex).

### 5.6 Pagos & Tesorería
- `GET/POST /pagos`, `GET/DELETE /pagos/<id>`: Registro y anulación de pagos.
- `GET /pagos/venta/<venta_id>`: Historial de amortizaciones de una venta.
- `POST /pagos/batch`: Registro simultáneo de pagos para múltiples ventas.
- `POST /pagos/depositos`: Registro de depósito en cuenta bancaria de montos en efectivo recaudados.
- `GET /pagos/cierrecaja`: Arqueo y balance de caja por almacén y rango de fechas.
- `GET /pagos/exportar`: Exportación del libro de ingresos/cobros.

### 5.7 Clientes & Inteligencia de Recompra
- `GET/POST /clientes`, `GET/PUT/DELETE /clientes/<id>`
- `GET /clientes/proyecciones`: Vista analítica con semáforo de recompra (`al_dia`, `proximo`, `vencido`).
- `GET /clientes/exportar` | `GET /clientes/proyecciones/exportar`

### 5.8 Gastos & Compras de Insumos
- `GET/POST /gastos`, `GET/PUT/DELETE /gastos/<id>`
- `POST /compras/insumos`: Registro de adquisición de insumos con actualización de stock.
- `GET /gastos/exportar`

### 5.9 Dashboard & Reportes Financieros
- `GET /dashboard`: KPIs consolidados de ventas, saldo por cobrar, stock bajo y gráficos.
- `GET /reportes/resumen-financiero`: Ingresos vs. Gastos vs. Margen operativo.
- `GET /reportes/ventas-presentacion`: Agrupación de ventas por presentación y kilos.
- `GET /reportes/unificado`: Consolidación multi-métrica para gerencia.
- `GET /reportes/depositos-historial`: Trazabilidad de fondos depositados vs. retenidos.

### 5.10 Inteligencia Artificial, Voz y Telegram
- `POST /voice/command`: Recepción de audio o texto para extracción de operaciones mediante Gemini.
- `POST /chat`: Asistente conversacional con acceso a contexto de negocio.
- `POST /telegram/webhook/<webhook_token>`: Webhook receptor de actualizaciones de Telegram.
- `POST /telegram/vincular`: Generación/validación de código temporal (PIN) para vincular usuario de Telegram.

---

## 6. Convenciones y Reglas de Desarrollo

### 6.1 Manejo de Errores y Base de Datos
- Todas las rutas deben estar decoradas con `@handle_db_errors` para capturar `IntegrityError`, `ValidationError` y excepciones de base de datos de manera uniforme.
- Si una operación involucra múltiples pasos (ej. Venta + Detalles + Movimiento de Stock + Pago), se debe ejecutar dentro de una única transacción SQLAlchemy con rollback garantizado ante cualquier falla:
  ```python
  try:
      # operaciones
      db.session.commit()
  except Exception:
      db.session.rollback()
      raise
  ```

### 6.2 Control de Accesos y Roles
- `@rol_requerido('admin', 'gerente')`: Restringe endpoints sensibles a roles superiores.
- `@mismo_almacen_o_admin`: Impide que un usuario con rol `usuario` consulte o modifique datos de un almacén diferente al que tiene asignado.

### 6.3 Manejo de Zonas Horarias
- Las fechas en base de datos se almacenan con `timezone=True` (UTC).
- Para cálculos de negocio locales (Perú), utilizar las funciones auxiliares de `utils.date_utils`:
  - `to_peru_time(dt)`: Convierte UTC a hora de Perú (`America/Lima`).
  - `get_peru_now()`: Obtiene el `datetime` actual en hora peruana.

### 6.4 Seguridad en Telegram & Respuestas al Usuario
- Jamás exponer excepciones crudas, trazas SQL (`psycopg2.errors`, `IntegrityError`) o stacktraces al usuario en Telegram o en respuestas HTTP públicas.
- Usar `format_user_friendly_error(e)` en el bot de Telegram para devolver mensajes claros con códigos de error de referencia (`ERR-XXXX`).

---

## 7. Configuración y Entornos

El sistema utiliza archivos `.env` según el entorno de ejecución:

| Archivo | Entorno | Uso Principal |
|---|---|---|
| `.env.local` | `development` | Desarrollo local con SQLite o PostgreSQL local, CORS permisivo |
| `.env.production` | `production` | Despliegue en producción con PostgreSQL (Supabase/RDS), S3, CORS estricto |
| `.env.example` | Plantilla | Documentación de todas las variables requeridas |

---

## 8. Comandos de Desarrollo y Operación

### Entorno Virtual y Dependencias
```bash
# Crear y activar entorno virtual (Windows)
python -m venv venv
.\venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

### Ejecución del Servidor
```bash
# Desarrollo local
python app.py

# O especificando archivo de entorno
set ENV_FILE=.env.local
python app.py

# Producción con Gunicorn (Linux / Contenedor)
gunicorn --bind 0.0.0.0:8080 app:app --workers 4 --threads 2
```

### Migraciones de Base de Datos
```bash
# Generar nueva migración tras modificar models.py
flask db migrate -m "Descripcion del cambio"

# Aplicar migraciones a la base de datos
flask db upgrade

# Revertir última migración
flask db downgrade
```

### Pruebas Automatizadas
```bash
# Ejecutar toda la suite de tests
pytest

# Ejecutar tests con reporte detallado
pytest -v -s
```
