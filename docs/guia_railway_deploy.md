# Guía de Despliegue en Railway

> **Proyecto:** apiFlaskManngo  
> **Fecha:** Julio 2026  
> **Objetivo:** Desplegar la API de Flask en Railway usando Docker, configurando Redis para el rate limiting y conectando la base de datos de Supabase de manera óptima.

---

## Tabla de Contenidos

1. [Estructura del Entorno en Railway](#1-estructura-del-entorno-en-railway)
2. [Paso 1: Configurar Redis en Railway](#2-paso-1-configurar-redis-en-railway)
3. [Paso 2: Conectar la Base de Datos (Supabase)](#3-paso-2-conectar-la-base-de-datos-supabase)
4. [Paso 3: Variables de Entorno en Railway](#4-paso-3-variables-de-entorno-en-railway)
5. [Paso 4: Optimización de Docker y Gunicorn](#5-paso-4-optimización-de-docker-y-gunicorn)
6. [Paso 5: Configurar Rate Limiter con Redis en el Código](#6-paso-5-configurar-rate-limiter-con-redis-en-el-código)
7. [Paso 6: Lanzar el Deploy en Railway](#7-paso-6-lanzar-el-deploy-en-railway)
8. [Paso 7: Configuración de Dominios y SSL](#8-paso-7-configuración-de-dominios-y-ssl)
9. [Paso 8: Health Check y Auto-Restart](#9-paso-8-health-check-y-auto-restart)

---

## 1. Estructura del Entorno en Railway

Railway te permite agrupar múltiples servicios en un mismo proyecto. Tu arquitectura en Railway constará de:

```
[ Proyecto Manngo API ]
  ├── 1. Flask Web Service (Construido vía Dockerfile)
  └── 2. Redis Private Service (Para Rate Limiter y Caché)
```

---

## 2. Paso 1: Configurar Redis en Railway

Para el rate limiter (`Flask-Limiter`) en producción, no se debe usar la memoria local (`memory://`) debido a que Gunicorn ejecuta múltiples subprocesos (workers) y las peticiones se distribuyen de forma asíncrona.

1. Ve a tu dashboard de Railway.
2. Haz clic en **+ New** → **Database** → **Add Redis**.
3. Railway creará un servicio de Redis en tu proyecto.
4. Ve al servicio de Redis → pestaña **Variables** y copia la URL privada:
   * `REDIS_URL` o `REDIS_PRIVATE_URL` (formato: `redis://default:contraseña@host:puerto`).

---

## 3. Paso 2: Conectar la Base de Datos (Supabase)

Como ya tienes tu base de datos configurada en Supabase (PostgreSQL), la API de Flask se conectará directamente a ella mediante el Connection Pooler provisto por Supabase.

> 💡 **Recomendación:** Usa la URL del pooler transaccional (puerto `6543`) en lugar de la conexión directa (puerto `5432`). Esto reduce el consumo de recursos de conexión.

Connection string a usar en Railway:
`postgresql://postgres.ytmgbcmcvelzmbehinfe:contraseña@aws-0-us-east-2.pooler.supabase.com:6543/postgres`

---

## 4. Paso 3: Variables de Entorno en Railway

Configura las siguientes variables en la pestaña **Variables** de tu servicio web en Railway:

| Variable | Valor Recomendado | Descripción |
|---|---|---|
| `FLASK_APP` | `app.py` | Entrada de la app Flask |
| `FLASK_ENV` | `production` | Modo de ejecución de la app |
| `PORT` | `8080` (Railway la asigna automáticamente) | Puerto expuesto |
| `DATABASE_URL` | `postgresql://...` | Connection String de Supabase Pooler |
| `JWT_SECRET_KEY` | `una-clave-muy-segura-y-aleatoria` | Clave para cifrar tokens JWT |
| `JWT_EXPIRES_SECONDS` | `43200` (12 horas) | Expiración de tokens estándar |
| `ALLOWED_ORIGINS` | `https://manngo.com,https://www.manngo.com` | Dominios permitidos por CORS |
| `SUPABASE_URL` | `https://ytmgbcmcvelzmbehinfe.supabase.co` | URL de tu proyecto Supabase |
| `SUPABASE_KEY` | `tu-service-role-key-de-supabase` | Clave secreta (Service Role) |
| `GOOGLE_API_KEY` | `tu-api-key-de-gemini` | Clave para Gemini AI (chat/voz) |
| `LIMITER_STORAGE_URI` | `${{Redis.REDIS_URL}}` | Vincula automáticamente la URL de tu Redis de Railway |
| `DEFAULT_RATE_LIMIT` | `200 per day;50 per hour` | Límites para peticiones de la API |
| `LOG_LEVEL` | `INFO` | Nivel de logs para producción |

---

## 5. Paso 4: Optimización de Docker y Gunicorn

### 5.1 Dockerfile
Railway detecta automáticamente tu `Dockerfile` en la raíz del proyecto. Este ya se encuentra optimizado en dos fases de construcción (Builder y Final) para reducir el peso de la imagen a menos de ~150MB.

### 5.2 Configuración de Workers en Gunicorn
En el Dockerfile actual, el comando de inicio es:
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "3", "--threads", "2", "--timeout", "120", "app:app"]
```

#### Optimización en Railway:
Puedes sobreescribir los workers basándote en la cantidad de CPUs que Railway asigne a tu contenedor en producción:
* Número de workers recomendado: `(2 * Cores de CPU) + 1`.
* Para un plan básico de 512MB RAM/0.5vCPU, 2-3 workers son idóneos.
* Si necesitas cambiarlo dinámicamente, puedes definir la variable `GUNICORN_CMD_ARGS` en Railway con el valor:
  `--workers 3 --threads 2 --timeout 120`

---

## 6. Paso 5: Configurar Rate Limiter con Redis en el Código

En el archivo `app.py`, el limiter ya está preparado para leer `LIMITER_STORAGE_URI` desde las variables de entorno:

```python
# app.py
limiter = Limiter(
    key_func=get_key_func,
    default_limits=os.environ.get('DEFAULT_RATE_LIMIT', '200 per day;50 per hour').split(';'),
    storage_uri=os.environ.get('LIMITER_STORAGE_URI', 'memory://'),
    strategy=os.config.get('RATELIMIT_STRATEGY', 'fixed-window')
)
```

Al pasarle `${{Redis.REDIS_URL}}` en las variables de entorno de Railway, Flask-Limiter se conectará automáticamente a Redis de forma persistente y compartida entre todos los workers de Gunicorn.

---

## 7. Paso 6: Lanzar el Deploy en Railway

1. Instala el CLI de Railway (opcional) o conecta tu repositorio de GitHub directamente:
   * **Dashboard de Railway** → **New Project** → **Deploy from GitHub repository**.
2. Elige tu repositorio `apiFlaskManngo`.
3. Selecciona la rama principal (`main` o `master`).
4. Haz clic en **Deploy Now**.
5. Railway detectará el `Dockerfile`, compilará la imagen de Docker e iniciará el servicio automáticamente.

---

## 8. Paso 7: Configuración de Dominios y SSL

Por defecto, Railway te genera un dominio gratuito del tipo `xxx.up.railway.app`.

### Para agregar tu dominio personalizado (`manngo.com`):
1. Ve a tu servicio web en Railway → pestaña **Settings**.
2. En la sección **Domains**, haz clic en **Custom Domain**.
3. Ingresa tu dominio (ej: `api.manngo.com`).
4. Railway te proporcionará un registro **CNAME** DNS.
5. Ve al proveedor donde compraste tu dominio (ej. GoDaddy, Namecheap, Cloudflare) y añade el registro CNAME que te dio Railway.
6. Railway generará y renovará automáticamente tu certificado **SSL (HTTPS)** de Let's Encrypt de forma gratuita.

---

## 9. Paso 8: Health Check y Auto-Restart

Para garantizar alta disponibilidad y evitar deploys caídos:
1. Ve a tu servicio en Railway → pestaña **Settings** → sección **Deployments**.
2. Configura el **Healthcheck Path**:
   * Cambia el endpoint de validación a: `/health`.
3. Railway enviará peticiones a `/health` durante el deploy. Si la respuesta es exitosa (`HTTP 200` y la base de datos responde exitosamente), el despliegue se considera completado y se rutea el tráfico de usuarios.
4. Si por alguna razón el servicio se cae o la conexión a la base de datos colapsa permanentemente, el Health Check fallará y Railway reiniciará el contenedor automáticamente.
