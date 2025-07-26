# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Plan & Review

### Before starting work
- Always in plan mode to make a plan
- After get the plan, make sure you Write the plan to .claude/tasks/TASK_NAME.md
- The plan should be a detailed implementation plan and the reasoning behind them, as well as tasks broken down.
- If the task require external knowledge or certain package, also research to get latest knowledge (Use task tool for research)
- Don't over plan it, always think MVP.
- Once you write the plan, firstly ask me to review it. Do not continue until I approve the plan.

### While implementing
- You should update the plan as you work.
- After you complete tasks in the plan, you should update and append detailed descriptions of the changes you made, so following tasks can be easily hand over to other engineers.

## Development Commands

### Local Development
**Quick Start (Windows):**
```bash
dev.bat
```

**Manual Start:**
```bash
# Configurar entorno de desarrollo
set ENV_FILE=.env.local
python app.py
```

**Production Mode (Windows):**
```bash
prod.bat
```

**Run Tests:**
```bash
pytest
```

**Environment-specific Commands:**
```bash
# Desarrollo con SQLite
ENV_FILE=.env.local python app.py

# Producción local con PostgreSQL
ENV_FILE=.env.production python app.py

# Usar configuración personalizada
ENV_FILE=.env.custom python app.py
```

### Docker Commands
**Docker Build:**
```bash
docker build -t api-manngo .
```

**Docker Run (Development):**
```bash
docker run -p 5000:8080 --env-file .env.local api-manngo
```

**Docker Run (Production):**
```bash
docker run -p 5000:8080 --env-file .env.production api-manngo
```

### Diagnostic Endpoints (Development Only)
- **Health Check:** `GET /health`
- **Configuration Info:** `GET /config` (shows current environment settings)

## Architecture Overview

This is a Flask REST API for an inventory and sales management system (Manngo) with the following architecture:

### Core Structure
- **Entry Point:** `app.py` - Initializes Flask app, configures extensions (SQLAlchemy, JWT, CORS, rate limiting), and registers API resources
- **Database Models:** `models.py` - SQLAlchemy models for all entities (Users, Products, Sales, Inventory, etc.)
- **API Schemas:** `schemas.py` - Marshmallow schemas for serialization/validation
- **API Resources:** `resources/` directory - Flask-RESTful Resource classes for each endpoint
- **Extensions:** `extensions.py` - Shared Flask extension instances (db, jwt)
- **Common Utilities:** `common.py` - Decorators for error handling, role validation, and warehouse access control

### Key Features
- **Authentication:** JWT-based with role-based access control (admin, gerente, usuario)
- **Authorization:** Warehouse-scoped access for non-admin users via `@mismo_almacen_o_admin` decorator
- **Security:** Rate limiting, CORS, Talisman security headers, input sanitization
- **File Handling:** AWS S3 integration for file uploads (comprobantes, photos)
- **Logging:** CloudWatch integration for production environments
- **Database:** PostgreSQL with SQLAlchemy ORM

### Business Domain
The API manages:
- **Products & Presentations:** Product catalog with different packaging/presentation options
- **Inventory Management:** Multi-warehouse stock tracking with batch/lot management
- **Sales & Orders:** Complete sales workflow from orders (pedidos) to sales (ventas) with payment tracking
- **Financial Tracking:** Payments, expenses (gastos), bank deposits with receipt management
- **User Management:** Role-based access with warehouse assignments

### Important Patterns
- All API resources require JWT authentication except `/auth` and `/health`
- Database errors are handled via `@handle_db_errors` decorator
- Role validation uses `@rol_requerido('admin', 'gerente')` decorator
- Warehouse access control via `@mismo_almacen_o_admin` for non-admin users
- Pagination is implemented on list endpoints with `page`, `per_page`, `sort_by`, `sort_order` parameters
- File uploads support both JSON and multipart/form-data content types

### Environment Configuration

The app supports multiple environment configurations through different `.env` files:

**Configuration Files:**
- `.env.local` - Development with SQLite, permissive CORS, local file storage
- `.env.production` - Production with PostgreSQL, restricted CORS, S3 storage
- `.env` - Fallback configuration file

**Key Environment Variables:**
- `ENV_FILE` - Specifies which .env file to load (default: `.env.local`)
- `FLASK_ENV` - Environment mode (development/production)
- `DATABASE_URL` - Database connection string
- `JWT_SECRET_KEY` - JWT signing key (auto-generated for development)
- `S3_BUCKET` & `AWS_REGION` - S3 configuration (optional in development)
- `ALLOWED_ORIGINS` - CORS origins configuration
- `CLOUDWATCH_LOG_GROUP` - CloudWatch logging (production only)

**Local Development Features:**
- Automatic SQLite database creation
- Local file storage in `uploads/` directory
- Permissive CORS for frontend development
- Debug logging enabled
- Higher rate limits for testing

### Testing
- Test configuration in `tests/conftest.py`
- Uses SQLite in-memory database for tests
- Includes test data setup utilities
- Run tests with `pytest`

### Docker
- Multi-stage Dockerfile for production deployment
- Runs with gunicorn WSGI server
- Non-root user for security
- Environment variables for configuration