# Dependencias: versiones consultadas y propuesta pendiente de validar

Todas las versiones siguientes se consultaron en metadatos públicos PyPI; son candidatas, NO una garantía de compatibilidad ni ausencia de vulnerabilidades. El padre instalará entorno aislado y ejecutará suite antes de aprobar. Mantener Marshmallow 3.26.2 para no introducir ruptura mayor 4.x; actualizar Flask/Flask-SQLAlchemy y revisar usos de ENV/app.env retirados. No tocar .env ni actualizar global.

## Pins propuestos para L6

| Paquete | Versión | Fuente |
|---|---|---|
| flask | 3.1.3 | https://pypi.org/project/Flask/3.1.3/ |
| flask-cors | 6.0.5 | https://pypi.org/project/Flask-Cors/6.0.5/ |
| flask-jwt-extended | 4.7.4 | https://pypi.org/project/Flask-JWT-Extended/4.7.4/ |
| flask-limiter | 4.1.1 | https://pypi.org/project/Flask-Limiter/4.1.1/ |
| flask-marshmallow | 1.3.0 (conservada; 1.5.0 exige Marshmallow >=4 y el resolver rechazó esa combinación con 3.26.2) | https://pypi.org/project/Flask-Marshmallow/1.3.0/ |
| flask-sqlalchemy | 3.1.1 | https://pypi.org/project/Flask-SQLAlchemy/3.1.1/ |
| werkzeug | 3.1.9 | https://pypi.org/project/Werkzeug/3.1.9/ |
| pillow | 13.0.0 | https://pypi.org/project/Pillow/13.0.0/ |
| gunicorn | 26.2.0 | https://pypi.org/project/gunicorn/26.2.0/ |
| cryptography | 50.0.2 | https://pypi.org/project/cryptography/50.0.2/ |
| pyjwt | 2.15.1 | https://pypi.org/project/PyJWT/2.15.1/ |
| python-dotenv | 1.2.4 | https://pypi.org/project/python-dotenv/1.2.4/ |
| requests | 2.34.2 | https://pypi.org/project/requests/2.34.2/ |
| jinja2 | 3.1.6 | https://pypi.org/project/Jinja2/3.1.6/ |
| setuptools | 84.2.1 | https://pypi.org/project/setuptools/84.2.1/ |
| marshmallow | 3.26.2 | https://pypi.org/project/marshmallow/3.26.2/ |
| pyyaml | 6.0.3 | https://pypi.org/project/PyYAML/6.0.3/ |
| openpyxl | 3.1.5 | https://pypi.org/project/openpyxl/3.1.5/ |

Para GRE se proponen lxml 6.1.3 y signxml 5.2.0 con cryptography 50.0.2. Esquema estructural OASIS UBL 2.1 local en `tests/fixtures/ubl21_xsd/maindoc/UBL-DespatchAdvice-2.1.xsd`, con manifiesto de procedencia y hashes. No son validaciones tributarias SUNAT ni evidencia de emisión real.

El resto de dependencias puede conservarse si no hay aviso aplicable; pandas/redis deben fijarse en lock a versiones realmente resueltas. No copiar contadores brutos del scanner como vulnerabilidades explotables. Generar lock con versiones y hashes, `pip check` y suite; guardar límites y avisos restantes. La migración de Flask debe incluir startup/import de todos los recursos y el logger.

## Verificación local del candidato

- Resolver uv completado: 129 paquetes instalados y `uv pip check` sin conflictos.
- Suite L0 congelada en copia sin dotenv y contenedor --network none: **139 passed**, incluidas 107 pruebas originales. No es todavía suite del árbol final.
- pip-audit del candidato terminó con código0 y **sin avisos conocidos** en su salida JSON. Esto no demuestra ausencia de vulnerabilidades lógicas ni del despliegue.
- Lock con hashes generado realmente para Python3.12. Falta incorporar pins/lock a producto por L6 y repetir suite/build final. Flask-Marshmallow1.5.0 fue rechazado por conflicto con Marshmallow3 y no se usa.
