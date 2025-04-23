# --- Build Stage ---
FROM python:3.9-slim as builder

WORKDIR /app

# Instalar dependencias de construcción y cliente postgres
RUN apt-get update && apt-get install -y --no-install-recommends gcc postgresql-client libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Crear wheels para mejor manejo de cache
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# --- Final Stage ---
FROM python:3.9-slim

# Crear grupo y usuario no root
RUN groupadd -r appgroup && useradd --no-log-init -r -g appgroup appuser

WORKDIR /app

# Instalar solo cliente postgres si es necesario para comandos psql en el contenedor final
# Si no se necesita, se puede omitir esta capa para reducir tamaño
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*

# Copiar dependencias pre-compiladas desde la etapa builder
COPY --from=builder /wheels /wheels
COPY --from=builder /app/requirements.txt .
# Instalar dependencias desde wheels locales
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt

# Copiar el código de la aplicación
# Asegurarse que .dockerignore está bien configurado para no copiar archivos innecesarios
COPY . .

# Eliminar directorio de uploads si existe (no se usará en el contenedor)
RUN rm -rf uploads

# Establecer propietario del directorio de trabajo
# Esto debe hacerse DESPUÉS de copiar el código
RUN chown -R appuser:appgroup /app

# Configurar variables de entorno por defecto (pueden ser sobrescritas)
ENV FLASK_APP=app.py
ENV FLASK_ENV=development
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV GUNICORN_WORKERS=3
ENV GUNICORN_TIMEOUT=120

# Cambiar al usuario no root
USER appuser

# Exponer el puerto
EXPOSE ${PORT}

# Ejecutar con gunicorn usando variables de entorno
CMD exec gunicorn --bind :${PORT} --workers ${GUNICORN_WORKERS} --threads 2 --timeout ${GUNICORN_TIMEOUT} app:app