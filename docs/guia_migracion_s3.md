# Guía de Migración: AWS S3 → Supabase Storage

> **Proyecto:** apiFlaskManngo  
> **Fecha:** Julio 2026  
> **Objetivo:** Migrar todos los archivos almacenados en AWS S3 (bucket `appmanngoimagenes`) a Supabase Storage, eliminando la dependencia de AWS.

---

## Tabla de Contenidos

1. [Pre-requisitos](#1-pre-requisitos)
2. [Configurar Supabase Storage](#2-configurar-supabase-storage)
3. [Script de Migración Automatizada](#3-script-de-migración-automatizada)
4. [Actualizar el Código del Backend](#4-actualizar-el-código-del-backend)
5. [Actualizar URLs en la Base de Datos](#5-actualizar-urls-en-la-base-de-datos)
6. [Verificación y Rollback](#6-verificación-y-rollback)
7. [Limpieza Post-Migración](#7-limpieza-post-migración)

---

## 1. Pre-requisitos

- [ ] Tener acceso admin al proyecto Supabase (`ytmgbcmcvelzmbehinfe`)
- [ ] Tener las credenciales de AWS S3 configuradas localmente (`aws configure`)
- [ ] Python 3.9+ con `boto3`, `supabase` y `requests` instalados
- [ ] Backup de la base de datos antes de comenzar

```bash
# Instalar dependencias necesarias
pip install boto3 supabase python-dotenv
```

---

## 2. Configurar Supabase Storage

### 2.1 Crear Buckets en Supabase

Accede al **Dashboard de Supabase** → **Storage** → **New Bucket**.

Crea los siguientes buckets que corresponden a tus subcarpetas en S3:

| Bucket Name | Público | Descripción |
|---|---|---|
| `presentaciones` | ❌ Privado | Fotos de productos/presentaciones |
| `comprobantes` | ❌ Privado | Comprobantes de pago (imágenes y PDF) |

> **Nota:** Mantener los buckets privados y usar URLs firmadas, igual que con S3.

### 2.2 Configurar Políticas RLS (Row Level Security)

En el Dashboard de Supabase → **Storage** → Selecciona el bucket → **Policies**:

```sql
-- Política: Los usuarios autenticados pueden leer archivos
CREATE POLICY "Usuarios autenticados pueden leer" ON storage.objects
  FOR SELECT
  USING (auth.role() = 'authenticated' OR auth.role() = 'service_role');

-- Política: Solo el service_role puede insertar/eliminar
CREATE POLICY "Service role puede escribir" ON storage.objects
  FOR INSERT
  WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service role puede eliminar" ON storage.objects
  FOR DELETE
  USING (auth.role() = 'service_role');
```

> **Importante:** Tu API usa la `SUPABASE_KEY` con rol `service_role`, así que tendrá permisos completos.

---

## 3. Script de Migración Automatizada

Crea el archivo `scripts/migrate_s3_to_supabase.py`:

```python
"""
Script de migración: AWS S3 → Supabase Storage
Ejecutar: python scripts/migrate_s3_to_supabase.py
"""
import os
import sys
import boto3
import tempfile
import logging
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuración
AWS_BUCKET = os.getenv('S3_BUCKET', 'appmanngoimagenes')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-2')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Mapeo de carpetas S3 → buckets Supabase
FOLDER_TO_BUCKET = {
    'presentaciones/': 'presentaciones',
    'pagos/': 'comprobantes',
    'comprobantes/': 'comprobantes',
}

def get_supabase_bucket(s3_key):
    """Determina el bucket de Supabase basándose en la ruta del archivo S3."""
    for prefix, bucket in FOLDER_TO_BUCKET.items():
        if s3_key.startswith(prefix):
            return bucket, s3_key[len(prefix):]  # bucket, filename
    return 'comprobantes', s3_key  # fallback

def migrate():
    # Inicializar clientes
    s3 = boto3.client('s3', region_name=AWS_REGION)
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Listar todos los objetos en S3
    paginator = s3.get_paginator('list_objects_v2')
    total_migrated = 0
    total_errors = 0
    
    for page in paginator.paginate(Bucket=AWS_BUCKET):
        if 'Contents' not in page:
            logger.warning("No se encontraron archivos en el bucket S3.")
            continue
            
        for obj in page['Contents']:
            s3_key = obj['Key']
            
            # Ignorar "carpetas" vacías
            if s3_key.endswith('/'):
                continue
            
            bucket_name, file_path = get_supabase_bucket(s3_key)
            
            try:
                # Descargar archivo de S3 a un archivo temporal
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    s3.download_file(AWS_BUCKET, s3_key, tmp.name)
                    tmp_path = tmp.name
                
                # Detectar content type
                head = s3.head_object(Bucket=AWS_BUCKET, Key=s3_key)
                content_type = head.get('ContentType', 'application/octet-stream')
                
                # Subir a Supabase Storage
                with open(tmp_path, 'rb') as f:
                    supabase.storage.from_(bucket_name).upload(
                        path=file_path,
                        file=f,
                        file_options={"content-type": content_type}
                    )
                
                total_migrated += 1
                logger.info(f"✅ Migrado: s3://{s3_key} → supabase:{bucket_name}/{file_path}")
                
                # Limpiar archivo temporal
                os.unlink(tmp_path)
                
            except Exception as e:
                total_errors += 1
                logger.error(f"❌ Error migrando {s3_key}: {e}")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
    
    logger.info(f"\n{'='*50}")
    logger.info(f"Migración completada: {total_migrated} archivos migrados, {total_errors} errores")
    logger.info(f"{'='*50}")

if __name__ == '__main__':
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL y SUPABASE_KEY son requeridos. Verifica tu .env")
        sys.exit(1)
    
    confirm = input(f"¿Migrar todos los archivos de s3://{AWS_BUCKET} a Supabase Storage? (s/n): ")
    if confirm.lower() == 's':
        migrate()
    else:
        print("Migración cancelada.")
```

### Ejecutar la migración:

```bash
python scripts/migrate_s3_to_supabase.py
```

---

## 4. Actualizar el Código del Backend

### 4.1 Modificar `utils/file_handlers.py`

Reemplazar las funciones de S3 con Supabase Storage:

```python
# utils/file_handlers.py (versión Supabase)
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app
from extensions import supabase
from PIL import Image
import io

logger = logging.getLogger(__name__)

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida."""
    allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif', 'pdf'})
    if not filename:
        return False
    parts = filename.rsplit('.', 1)
    return len(parts) == 2 and parts[1].lower() in allowed_extensions

def safe_filename(filename, force_extension=None):
    """Genera un nombre de archivo seguro y único."""
    if not filename:
        return None
    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = 'file'

    try:
        base, original_extension = safe_name.rsplit('.', 1)
        original_extension = original_extension.lower()
    except ValueError:
        base = safe_name
        original_extension = None

    if not base:
        base = 'file'

    final_extension = force_extension or original_extension or 'bin'
    return f"{base}_{uuid.uuid4().hex}.{final_extension}"

def save_file(file, subfolder, quality=80, max_width=1920):
    """
    Procesa y guarda un archivo en Supabase Storage.
    - Imágenes: Redimensiona, convierte a WebP y sube.
    - PDFs: Sube directamente.
    
    Returns:
        str: Ruta del archivo en Supabase Storage, o None si hay error.
    """
    if not file or not file.filename:
        logger.warning("Intento de guardar archivo vacío o sin nombre")
        return None

    if not allowed_file(file.filename):
        logger.warning(f"Tipo de archivo no permitido: {file.filename}")
        return None

    if not supabase:
        logger.error("Cliente de Supabase no configurado.")
        return None

    # Determinar bucket según subfolder
    bucket_name = 'comprobantes' if 'pago' in subfolder or 'comprobante' in subfolder else 'presentaciones'
    
    content_type = file.content_type
    file_data = file.stream
    target_extension = None
    upload_content_type = content_type

    # Procesamiento de imagen a WebP
    if content_type and content_type.startswith('image/') and not content_type.endswith('webp'):
        try:
            img = Image.open(file.stream)
            if img.size[0] > max_width:
                ratio = max_width / float(img.size[0])
                new_height = int(float(img.size[1]) * ratio)
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

            webp_buffer = io.BytesIO()
            if img.mode == 'RGBA':
                img.save(webp_buffer, format='WEBP', quality=quality, lossless=False)
            else:
                img.convert('RGB').save(webp_buffer, format='WEBP', quality=quality)

            webp_buffer.seek(0)
            file_data = webp_buffer
            target_extension = "webp"
            upload_content_type = "image/webp"
        except Exception as e:
            logger.error(f"Error procesando imagen: {e}")
            return None

    # Generar nombre seguro
    unique_filename = safe_filename(file.filename, force_extension=target_extension)
    if not unique_filename:
        return None

    # Construir ruta en el bucket
    clean_subfolder = subfolder.strip('/') if subfolder else ''
    file_path = f"{clean_subfolder}/{unique_filename}" if clean_subfolder else unique_filename

    try:
        file_data.seek(0)
        supabase.storage.from_(bucket_name).upload(
            path=file_path,
            file=file_data.read(),
            file_options={"content-type": upload_content_type}
        )
        # Retornar la referencia completa: bucket/path
        storage_key = f"{bucket_name}/{file_path}"
        logger.info(f"Archivo subido a Supabase Storage: {storage_key}")
        return storage_key
    except Exception as e:
        logger.error(f"Error subiendo a Supabase Storage: {e}")
        return None

def get_presigned_url(storage_key, expiration=3600):
    """Genera una URL firmada para acceder a un archivo en Supabase Storage."""
    if not storage_key or not supabase:
        return None

    try:
        # Separar bucket de la ruta
        parts = storage_key.split('/', 1)
        if len(parts) != 2:
            logger.error(f"Formato de storage_key inválido: {storage_key}")
            return None
        
        bucket_name, file_path = parts
        result = supabase.storage.from_(bucket_name).create_signed_url(
            path=file_path,
            expires_in=expiration
        )
        return result.get('signedURL') or result.get('signedUrl')
    except Exception as e:
        logger.error(f"Error generando URL firmada: {e}")
        return None

def delete_file(storage_key):
    """Elimina un archivo de Supabase Storage."""
    if not storage_key or not supabase:
        return False

    try:
        parts = storage_key.split('/', 1)
        if len(parts) != 2:
            return False
        
        bucket_name, file_path = parts
        supabase.storage.from_(bucket_name).remove([file_path])
        logger.info(f"Archivo eliminado de Supabase Storage: {storage_key}")
        return True
    except Exception as e:
        logger.error(f"Error eliminando de Supabase Storage: {e}")
        return False
```

### 4.2 Actualizar Variables de Entorno

En tu `.env` y `.env.production`, reemplaza las variables de S3:

```env
# ❌ ELIMINAR (después de verificar la migración)
# S3_BUCKET=appmanngoimagenes
# AWS_REGION=us-east-2

# ✅ MANTENER (ya configuradas)
SUPABASE_URL="https://ytmgbcmcvelzmbehinfe.supabase.co"
SUPABASE_KEY="tu-service-role-key"
```

### 4.3 Actualizar `requirements.txt`

```diff
- # AWS and S3
- boto3==1.37.37
- botocore==1.37.37
```

> **Solo eliminar boto3 y botocore DESPUÉS de completar la migración y verificar que todo funciona.**

---

## 5. Actualizar URLs en la Base de Datos

Si tus registros almacenan claves S3 (como `pagos/archivo.webp`), necesitas actualizarlas al nuevo formato (`comprobantes/pagos/archivo.webp`):

```sql
-- Actualizar URLs de comprobantes de pago
UPDATE pagos 
SET url_comprobante = 'comprobantes/' || url_comprobante 
WHERE url_comprobante IS NOT NULL 
  AND url_comprobante NOT LIKE 'comprobantes/%';

-- Actualizar URLs de fotos de presentaciones
UPDATE presentaciones_producto 
SET url_foto = 'presentaciones/' || url_foto 
WHERE url_foto IS NOT NULL 
  AND url_foto NOT LIKE 'presentaciones/%';
```

> ⚠️ **Ejecutar estas queries DESPUÉS de la migración y DESPUÉS de actualizar el código del backend.**

---

## 6. Verificación y Rollback

### Verificación

```python
# scripts/verify_migration.py
from extensions import supabase

buckets = ['presentaciones', 'comprobantes']
for bucket in buckets:
    files = supabase.storage.from_(bucket).list()
    print(f"Bucket '{bucket}': {len(files)} archivos")
    for f in files[:3]:
        print(f"  - {f['name']} ({f.get('metadata', {}).get('size', 'N/A')} bytes)")
```

### Plan de Rollback

Si algo falla, puedes revertir:

1. **Código:** Git revert al commit anterior
2. **Base de datos:** Restaurar backup
3. **Archivos:** Los archivos en S3 no se eliminan hasta que confirmes manualmente

---

## 7. Limpieza Post-Migración

Una vez verificado que todo funciona correctamente (mínimo 1 semana en producción):

1. Eliminar `boto3` y `botocore` de `requirements.txt`
2. Eliminar configuración S3 de `app.py` (`S3_BUCKET`, `S3_REGION`)
3. Eliminar variables de entorno `S3_BUCKET` y `AWS_REGION`
4. Vaciar y eliminar el bucket S3 en AWS
5. Cerrar la cuenta AWS si ya no la necesitas

---

## Resumen del Flujo

```
┌──────────────────────────────────────────────────────────┐
│ 1. Crear buckets en Supabase Dashboard                   │
│ 2. Ejecutar script de migración (S3 → Supabase)          │
│ 3. Actualizar file_handlers.py (boto3 → supabase SDK)    │
│ 4. Actualizar URLs en la base de datos                   │
│ 5. Deployar nuevo código                                 │
│ 6. Verificar durante 1 semana                            │
│ 7. Eliminar S3 y dependencias AWS                        │
└──────────────────────────────────────────────────────────┘
```
