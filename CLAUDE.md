# Project Context & AI Guidelines: apiFlaskManngo (CLAUDE.md)

This document provides complete, accurate, and up-to-date guidance for Claude Code (claude.ai/code) and AI agents interacting with the **apiFlaskManngo** repository.

---

## 1. Project Overview

**apiFlaskManngo** is a production-grade RESTful API built in Python using Flask and PostgreSQL. It functions as the comprehensive ERP and operational backend for **Manngo**, a business enterprise specializing in charcoal, eco-friendly briquettes, packaging, inventory, and multi-channel sales distribution.

### Core Business Pillars
1. **Multi-Warehouse Inventory & Lot Tracing**: Tracking raw materials, wet/dry weights, lot conversions, commercial presentations, inter-warehouse transfers, and insumos (bags, strings, labels).
2. **Production & Recipe (BOM) Assembly**: Automated consumption of raw material and supplies to assemble finished products according to defined formulas (Bill of Materials), recording waste (merma) and efficiency.
3. **Sales, Orders & Pre-sales**: Full lifecycle from scheduled pre-sales (`pedidos`) to finalized sales (`ventas`), supporting cash (`contado`) and credit (`credito`) with partial payments (`parcial`) and balance calculations.
4. **Treasury & Bank Deposit Reconciliation**: Distinct tracking of cash in manager hands vs. funds deposited into corporate bank accounts, complete with cash register closing (`cierrecaja`).
5. **Customer Recurrence & Repurchase Forecasting**: Intelligent repurchase projection engine calculating estimated next purchase dates, days of delay, and health status indicators (`al_dia`, `proximo`, `vencido`).
6. **Gemini AI & Voice Integration**: Natural language parsing for audio/text commands (`/voice/command`), smart assistant operations, and structured JSON parsing.
7. **Telegram Conversational Bot**: Webhook-based interactive assistant with Finite State Machine (FSM) for field staff to record sales, collections, stock inquiries, production, and transfers.
8. **SUNAT Electronic Dispatch Guides (GRE)**: REST JSON client integration with Peru's tax authority (SUNAT) using OAuth2 tokens and status polling.

---

## 2. Technology Stack & Key Dependencies

- **Framework**: Flask 3.x, Flask-RESTful
- **Database / ORM**: PostgreSQL, SQLAlchemy 2.x, Flask-SQLAlchemy
- **Database Migrations**: Alembic, Flask-Migrate
- **Serialization / Validation**: Marshmallow, Flask-Marshmallow, marshmallow-sqlalchemy
- **Authentication / Authorization**: Flask-JWT-Extended (RBAC: `admin`, `gerente`, `usuario`)
- **Security & Optimization**: Flask-Talisman, Flask-Limiter, Flask-Compress (gzip)
- **AI & NLP**: Google Generative AI (`google-genai` / Gemini 2.5 Flash)
- **Cloud Storage**: AWS S3 & Supabase Storage (payment vouchers, product photos)
- **Tax Integration**: Requests, OAuth2 token caching for SUNAT GRE
- **Testing**: Pytest

---

## 3. Directory Layout & Architecture

```text
apiFlaskManngo/
├── app.py                      # Main entry point: configurations, middlewares, error handlers, resource init
├── common.py                   # Decorators (@handle_db_errors, @rol_requerido, @mismo_almacen_o_admin), date tools
├── config.py                   # Global constants (MAX_ITEMS_PER_PAGE, etc.)
├── extensions.py               # Shared Flask extensions (db, jwt, swagger, migrate)
├── models.py                   # SQLAlchemy ORM models & business constraints
├── schemas.py                  # Marshmallow schemas for serialization/deserialization
├── resources/                  # Flask-RESTful resource controllers
│   ├── __init__.py             # Resource registry & rate limiting
│   ├── auth_resource.py        # /auth
│   ├── user_resource.py        # /usuarios
│   ├── producto_resource.py    # /productos
│   ├── presentacion_resource.py# /presentaciones
│   ├── almacen_resource.py     # /almacenes
│   ├── inventario_resource.py  # /inventarios, /inventario/reporte-global, /inventario/alertas-insumos
│   ├── transferencia_resource.py # /inventario/transferir
│   ├── lote_resource.py        # /lotes
│   ├── produccion_resource.py  # /produccion, /produccion/ensamblaje
│   ├── receta_resource.py      # /recetas
│   ├── merma_resource.py       # /mermas
│   ├── cliente_resource.py     # /clientes, /clientes/proyecciones, /clientes/exportar
│   ├── proveedor_resource.py   # /proveedores
│   ├── pedido_resource.py      # /pedidos, /pedidos/<id>/convertir, /pedidos/form-data
│   ├── venta_resource.py       # /ventas, /ventas/form-data, /ventas/exportar, /ventas/filtros
│   ├── ventadetalle_resource.py# /ventas/<id>/detalles
│   ├── pago_resource.py        # /pagos, /pagos/venta/<id>, /pagos/batch, /pagos/depositos, /pagos/cierrecaja
│   ├── gasto_resource.py       # /gastos, /gastos/exportar
│   ├── compra_insumo_resource.py# /compras/insumos
│   ├── dashboard_resource.py   # /dashboard
│   ├── reporte_financiero_resource.py # /reportes/ventas-presentacion, /reportes/resumen-financiero, /reportes/unificado, /reportes/depositos-historial
│   ├── reporte_produccion_resource.py # /reportes/produccion-briquetas, /reportes/produccion-general
│   ├── voice_resource.py       # /voice/command
│   ├── chat_resource.py        # /chat
│   ├── telegram_webhook_resource.py # /telegram/webhook/<token>, /telegram/vincular
│   └── transaccion_resource.py # /transacciones/venta-completa
├── services/                   # Business logic & 3rd-party integrations
│   ├── gemini_service.py       # Gemini API client, structured prompts & audio transcription
│   ├── pago_service.py         # Payment processing and auto-status resolution
│   ├── produccion_service.py   # Assembly logic, BOM deduction, lot tracking
│   ├── stock_service.py        # Atomic stock verification and inventory updates
│   ├── sunat_service.py        # SUNAT OAuth2 token caching & Dispatch Guide transmission
│   ├── telegram_service.py     # Telegram Bot API messaging & interactive buttons
│   └── venta_service.py        # Sale execution and stock decrement orchestration
├── telegram/                   # Telegram Bot Engine
│   ├── context.py              # User context and conversation history management
│   ├── handlers/               # Intent handlers (venta, pago, produccion, guia_sunat, consulta, transferencia)
│   ├── resolvers.py            # Fuzzy matching for entities (products, clients, warehouses)
│   ├── router.py               # Main dispatch & secure user-friendly error formatting
│   ├── state_machine.py        # Multi-step conversational FSM
│   └── ui.py                   # Telegram Keyboards & UI builder
├── utils/                      # Utilities
│   ├── date_parser.py          # Flexible string-to-date parsing
│   ├── date_utils.py           # Timezone utilities (Peru America/Lima vs UTC)
│   ├── file_handlers.py        # S3 / local file storage management
│   └── logger_config.py        # Structured logging setup
├── scripts/                    # Management scripts (e.g. sync_supabase.py)
└── tests/                      # Pytest suite
```

---

## 4. Key Endpoints Summary

| Method | Endpoint | Description | Role / Auth |
|---|---|---|---|
| `POST` | `/auth` | Authenticate user with username/password, returns JWT | Public (Rate-limited: 10/min) |
| `GET` | `/health` | Service health and database connectivity check | Public |
| `GET` / `POST` | `/usuarios` | List or create users | `admin`, `gerente` |
| `GET` / `POST` | `/productos` | Product catalog CRUD | JWT required |
| `GET` / `POST` | `/presentaciones` | Commercial presentation formats & prices | JWT required |
| `GET` / `POST` | `/almacenes` | Warehouse management (`es_planta=True/False`) | JWT required |
| `GET` | `/inventarios` | List inventory per warehouse/presentation | JWT required |
| `GET` | `/inventario/reporte-global` | Consolidated stock across all warehouses | JWT required |
| `POST` | `/inventario/transferir` | Atomic stock transfer between warehouses | JWT required |
| `GET` | `/inventario/alertas-insumos`| Low stock supplies alerts | JWT required |
| `GET` / `POST` | `/recetas` | Bill of Materials (recipes) management | `admin`, `gerente` |
| `POST` | `/produccion` | Operator production recording (consumes BOM, adds product) | JWT required |
| `POST` | `/produccion/ensamblaje`| Internal recipe assembly engine | JWT required |
| `GET` | `/reportes/produccion-entradas`| Comprehensive production report by batch/lot, dates & inter-warehouse transfers (alias: `/reportes/movimientos-entrada`) | JWT required |
| `GET` / `POST` | `/ventas` | Sales management and filters | JWT required |
| `POST` | `/transacciones/venta-completa`| Atomic sale + details + initial payment + stock update | JWT required |
| `GET` / `POST` | `/pedidos` | Scheduled pre-orders | JWT required |
| `POST` | `/pedidos/<id>/convertir` | Convert pre-order into actual sale | JWT required |
| `GET` / `POST` | `/pagos` | Record/manage payments | JWT required |
| `POST` | `/pagos/batch` | Record payments for multiple sales in one request | JWT required |
| `POST` | `/pagos/depositos` | Log bank deposits for collected cash | `admin`, `gerente` |
| `GET` | `/pagos/cierrecaja` | Cash register balance & audit per warehouse | `admin`, `gerente` |
| `GET` / `POST` | `/clientes` | Customer directory | JWT required |
| `GET` | `/clientes/proyecciones` | Customer repurchase projection matrix | JWT required |
| `GET` / `POST` | `/gastos` | Operational expense logging | JWT required |
| `POST` | `/compras/insumos` | Raw supplies purchase & inventory increment | JWT required |
| `GET` | `/dashboard` | Executive KPI overview | JWT required |
| `GET` | `/reportes/resumen-financiero`| Income vs. Expenses financial summary | `admin`, `gerente` |
| `POST` | `/voice/command` | Process audio or text command via Gemini | JWT (Rate-limited: 20/min) |
| `POST` | `/chat` | Assistant chat with business context | JWT required |
| `POST` | `/telegram/webhook/<token>` | Telegram bot webhook receiver | Verified by header |
| `POST` | `/telegram/vincular` | Link Telegram account with 6-digit PIN | JWT required |

---

## 5. Development Conventions & Rules

### 5.1 Plan & Review Workflow
- When implementing new features or major refactors, adopt a structured planning approach.
- Break down tasks into clear steps with MVP focus.
- Ensure automated tests cover database transactions, edge cases, and role validations.

### 5.2 Transactional Integrity & Error Handling
- Always wrap resource logic using the `@handle_db_errors` decorator from `common.py`.
- Multi-entity modifications (e.g. Sale + Details + Payment + Kardex) **must** run in a single transaction with explicit `db.session.rollback()` on exception.
- Never expose internal database errors or SQL stacktraces to clients or Telegram. Use `format_user_friendly_error(e)` in Telegram handlers.

### 5.3 Access Control & Scoping
- Enforce roles with `@rol_requerido('admin', 'gerente')`.
- For warehouse isolation, use `@mismo_almacen_o_admin` to prevent standard users from modifying records outside their assigned warehouse.

### 5.4 Timezone Management
- Database datetime columns use `timezone=True` (UTC).
- Local business dates (Peru timezone `America/Lima`) must use `utils.date_utils`:
  - `get_peru_now()`
  - `to_peru_time(dt)`

---

## 6. Environment & Configuration

Configurations are loaded conditionally based on `FLASK_ENV`:
- `.env.local`: Local development (SQLite or local PostgreSQL, permissive CORS).
- `.env.production`: Production environment (PostgreSQL/Supabase, strict CORS, AWS S3, security headers).
- `.env.example`: Reference documentation for required environment variables.

### Common CLI & Development Commands

```bash
# Activate virtual environment (Windows)
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run development server
python app.py

# Run tests
pytest

# Run tests with detailed output
pytest -v -s

# Database migrations
flask db migrate -m "Describe migration"
flask db upgrade

# Sync Supabase CLI helper
flask sync-supabase

# Production run with Gunicorn
gunicorn --bind 0.0.0.0:8080 app:app --workers 4 --threads 2
```