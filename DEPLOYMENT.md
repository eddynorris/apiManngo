# Deployment Guide - API Manngo

## 🏠 Desarrollo Local

### Primer Setup
1. **Clonar el repositorio:**
   ```bash
   git clone <repository-url>
   cd apiFlaskManngo
   ```

2. **Crear entorno virtual:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar entorno de desarrollo:**
   - El archivo `.env.local` ya está configurado para desarrollo
   - Usa SQLite por defecto (no requiere PostgreSQL local)
   - Archivos se guardan localmente en `uploads/`

5. **Iniciar en desarrollo:**
   ```bash
   dev.bat
   # o manualmente:
   set ENV_FILE=.env.local
   python app.py
   ```

### URLs de Desarrollo
- **API:** http://localhost:5000
- **Health Check:** http://localhost:5000/health
- **Configuración:** http://localhost:5000/config (solo en desarrollo)

---

## 🚀 Deployment en AWS EC2

### Configuración en el Servidor

1. **Configurar variables de entorno:**
   ```bash
   # En el servidor EC2, configurar:
   export ENV_FILE=.env.production
   export FLASK_ENV=production
   ```

2. **Archivo de configuración de producción:**
   - Usar `.env.production` con configuraciones de producción
   - Incluir credenciales de Supabase/PostgreSQL
   - Configurar S3 para archivos
   - Activar CloudWatch logging

3. **Iniciar con Gunicorn:**
   ```bash
   gunicorn --bind 0.0.0.0:5000 --workers 3 --threads 2 --timeout 120 app:app
   ```

### Variables de Entorno Críticas para Producción
```bash
# Base de datos
DATABASE_URL=postgresql://usuario:password@host:puerto/database

# JWT
JWT_SECRET_KEY=clave-super-segura-aqui

# AWS
S3_BUCKET=nombre-del-bucket
AWS_REGION=us-east-2
CLOUDWATCH_LOG_GROUP=manngo-api-logs

# CORS
ALLOWED_ORIGINS=https://manngo.lat

# Flask
FLASK_ENV=production
```

---

## 🔄 Workflow de Desarrollo → Producción

### 1. Desarrollo Local
```bash
# Trabajar en desarrollo
dev.bat

# Hacer cambios al código
# Probar localmente con SQLite

# Verificar configuración
curl http://localhost:5000/config
```

### 2. Testing
```bash
# Ejecutar tests
pytest

# Probar con configuración de producción localmente
set ENV_FILE=.env.production
python app.py
```

### 3. Deployment
```bash
# Commit y push a GitHub
git add .
git commit -m "Descripción de cambios"
git push origin main
```

### 4. En el Servidor EC2
```bash
# Pull de los cambios
git pull origin main

# Reiniciar el servicio (método depende de tu setup)
sudo systemctl restart manngo-api
# o si usas PM2:
pm2 restart manngo-api
```

---

## 🧪 Testing en Diferentes Entornos

### Test con SQLite (rápido)
```bash
ENV_FILE=.env.local pytest
```

### Test con PostgreSQL (más realista)
```bash
# Configurar .env.test con PostgreSQL de test
ENV_FILE=.env.test pytest
```

---

## 🔧 Troubleshooting

### Problema: Base de datos no conecta
- **Local:** Verificar que SQLite se esté creando correctamente
- **Producción:** Verificar credenciales de PostgreSQL/Supabase

### Problema: Archivos no se suben
- **Local:** Verificar que el directorio `uploads/` se creó
- **Producción:** Verificar credenciales de AWS S3

### Problema: CORS errors
- **Local:** Verificar `ALLOWED_ORIGINS` en `.env.local`
- **Producción:** Verificar `ALLOWED_ORIGINS` en `.env.production`

### Debug del entorno actual
```bash
# En desarrollo
curl http://localhost:5000/config

# En producción (endpoint no disponible)
# Revisar logs de la aplicación
```

---

## 📝 Notas Importantes

1. **Nunca** commitear archivos `.env*` con credenciales reales
2. **Siempre** usar `.env.local` para desarrollo local
3. **Verificar** que el entorno correcto esté cargado antes de hacer cambios
4. **Probar** localmente antes de hacer push a producción
5. **Monitorear** los logs después del deployment

---

## 🎯 Checklist Pre-Deployment

- [ ] Tests pasan en local
- [ ] Configuración de producción actualizada
- [ ] Variables de entorno configuradas en servidor
- [ ] S3 y CloudWatch configurados
- [ ] Backup de base de datos (si hay cambios de schema)
- [ ] Endpoint /health responde correctamente