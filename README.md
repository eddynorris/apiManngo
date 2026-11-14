# API Manngo

API REST en Flask para Manngo, empresa de producción y venta de carbón vegetal y briquetas. Cubre
inventario multialmacén con lotes, producción por receta, pedidos y ventas, pagos y depósitos, gastos y
compras de insumos, reportes, proyección de recompra de clientes, un bot de Telegram con IA (Gemini) y la
preparación de guías de remisión electrónicas de SUNAT.

La referencia técnica para desarrolladores y agentes es **[AGENTS.md](AGENTS.md)** (stack, estructura,
comandos, convenciones y zonas sensibles). Los endpoints están documentados en `API_OVERVIEW.md` y
`API_Endpoints_Documentation.md` (puede haber diferencias con el código: manda `resources/__init__.py`).

## Tecnologías
- Python 3.12, Flask 3.1, Flask-RESTful, SQLAlchemy 2, Flask-Migrate (Alembic), Marshmallow
- PostgreSQL en Supabase; Supabase Storage para comprobantes y fotos
- JWT, Flask-Limiter con Redis (obligatorio en producción), Talisman, CORS, Compress, Swagger (Flasgger)
- Google Gemini (`google-generativeai`), Telegram Bot API, SUNAT GRE (lxml/signxml)
- gunicorn + Docker; pytest y ruff

## Puesta en marcha
```bash
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.lock
pip install pytest
cp .env.example .env                                  # completar DATABASE_URL y JWT_SECRET_KEY (>= 32 caracteres)
python app.py                                         # http://localhost:5000, /health
```

Tests (sin red ni credenciales reales):
```bash
python scripts/run_tests_offline.py -q
```

Docker (producción):
```bash
docker build -t api-manngo .
docker run -p 8080:8080 --env-file .env.production api-manngo
```
En producción hace falta un Redis (`LIMITER_STORAGE_URI=redis://...`; en Railway `${{Redis.REDIS_URL}}`).
Las migraciones (`flask db upgrade`) son un paso manual del operador, tras un respaldo; ver
`docs/PREPARACION_DESPLIEGUE.md`.

## Estructura
- `app.py`: arranque y configuración.
- `extensions.py`, `config.py`, `common.py`: extensiones, constantes y decoradores.
- `models.py`, `schemas.py`: modelos SQLAlchemy y schemas Marshmallow.
- `resources/`: endpoints por dominio. `services/`: lógica de negocio e integraciones.
- `telegram/`: bot conversacional. `utils/`: fechas, almacenamiento y logging.
- `migrations/`: Alembic. `scripts/`: runner de tests offline, preflight de despliegue, carga histórica (dry-run) y sync con Supabase.
- `tests/`: suite pytest. `docs/`: guías y notas de la auditoría.
