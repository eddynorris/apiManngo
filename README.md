# API Manngo

API REST construida con Flask que da soporte al sistema Manngo: gestión de ventas, inventario, producción, compras, pagos, clientes y proveedores de un negocio. Incluye autenticación JWT, subida de archivos a AWS S3, webhook de Telegram, chat asistido con Gemini, integración con SUNAT y sincronización con Supabase.

## Características principales

- Autenticación y registro de usuarios con JWT (`/auth`, `/registrar`).
- CRUD de productos, presentaciones, clientes, proveedores, almacenes, lotes y mermas.
- Ventas con detalle, movimientos de inventario y pedidos (incluida su conversión).
- Gestión de gastos y pagos (incluidos depósitos bancarios y comprobantes).
- Módulo de producción: recetas, tipos y reportes de producción.
- Reportes financieros y dashboard.
- Webhook de Telegram (ruta protegida por token) y bot de ventas por voz.
- Chat asistido con IA (Google Gemini).
- Integración con SUNAT.
- Sincronización con Supabase y almacenamiento en AWS S3.
- Rate limiting (Redis opcional), CORS restringido por entorno, cabeceras de seguridad (Talisman), compresión gzip y validación de configuración al arranque.
- Documentación Swagger/OpenAPI integrada.
- Ci en GitHub Actions y Dockerfile de producción con gunicorn.

## Tecnologías usadas

- Python 3.11
- Flask 2.3 y ecosistema: Flask-RESTful, Flask-SQLAlchemy, Flask-JWT-Extended, Flask-Migrate, Flask-Limiter, Flask-Talisman, Flask-CORS, Flask-Compress
- PostgreSQL (psycopg2) / SQLite en desarrollo, con Alembic
- Supabase, AWS S3 (boto3), Google Generative AI
- Redis (respaldo del rate limiting)
- gunicorn (producción) + Docker
- pytest (tests), ruff (lint)

## Requisitos previos

- Python 3.11+
- PostgreSQL (o SQLite para desarrollo local)
- Credenciales de Supabase y/o AWS S3 (según módulos usados)
- Tokens de Telegram y Google API (para webhook y chat)

## Cómo ejecutar

1. Crear un entorno virtual e instalar las dependencias:

   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copiar `.env.example` a `.env` y completar los valores (`DATABASE_URL`, `JWT_SECRET_KEY`, `ALLOWED_ORIGINS`, etc.).

3. Aplicar migraciones y levantar el servidor de desarrollo:

   ```bash
   flask db upgrade
   flask run
   ```

   O directamente:

   ```bash
   python app.py
   ```

4. Ejecutar los tests:

   ```bash
   pytest
   ```

Con Docker (producción):

   ```bash
   docker build -t api-manngo .
   docker run -p 8080:8080 api-manngo
   ```

## Estructura del proyecto

- `app.py` — punto de entrada y configuración de la aplicación.
- `config.py` / `extensions.py` — configuración y extensiones de Flask.
- `models.py` / `schemas.py` — modelos SQLAlchemy y esquemas Marshmallow.
- `resources/` — endpoints (recursos Flask-RESTful) organizados por dominio.
- `services/` — lógica de negocio (ventas, stock, producción, pagos, Telegram, Gemini, SUNAT).
- `telegram/` — router y resolvers del bot de Telegram.
- `scripts/` — comandos CLI (por ejemplo, `sync_supabase`).
- `migrations/` — migraciones de base de datos (Alembic).
- `database/` — scripts SQL de inicialización y datos.
- `docs/` — guías adicionales (Railway, migración S3/Supabase, depósitos bancarios, etc.).

La documentación de la API detallada se encuentra en `API_Endpoints_Documentation.md` y `API_OVERVIEW.md`.