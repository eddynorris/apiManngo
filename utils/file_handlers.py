# utils/file_handlers.py
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app
from extensions import supabase
from PIL import Image
import io

# Configurar logging
logger = logging.getLogger(__name__)

# Buckets válidos en Supabase
VALID_BUCKETS = {'presentaciones', 'comprobantes', 'pagos'}

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif', 'pdf'})
    if not filename:
        return False
    parts = filename.rsplit('.', 1)
    if len(parts) == 2:
        return parts[1].lower() in allowed_extensions
    return False

def safe_filename(filename, force_extension=None):
    """
    Genera un nombre de archivo seguro y único.
    """
    if not filename:
        return None
    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = 'file'

    original_extension = None
    try:
        base, original_extension = safe_name.rsplit('.', 1)
        original_extension = original_extension.lower()
    except ValueError:
        base = safe_name

    if not base:
        base = 'file'

    final_extension = force_extension if force_extension else original_extension
    if not final_extension:
         final_extension = 'bin'

    unique_name = f"{base}_{uuid.uuid4().hex}.{final_extension}"
    return unique_name

def determine_bucket_and_path(storage_key):
    """
    Dada una clave de almacenamiento (S3 o Supabase), determina el bucket
    y la ruta del archivo correspondiente en Supabase Storage.
    """
    if not storage_key:
        return None, None
        
    parts = storage_key.split('/', 1)
    if len(parts) == 2 and parts[0] in VALID_BUCKETS:
        bucket_name = parts[0]
        file_path = parts[1]
    else:
        # Si no comienza con un bucket válido (ej. rutas antiguas de S3 o 'comprobantes_depositos')
        if storage_key.startswith('presentaciones/'):
            bucket_name = 'presentaciones'
            file_path = storage_key[len('presentaciones/'):]
        elif storage_key.startswith('pagos/'):
            bucket_name = 'pagos'
            file_path = storage_key[len('pagos/'):]
        else:
            bucket_name = 'comprobantes'
            file_path = storage_key  # Se mantiene la ruta completa en el bucket fallback
            
    return bucket_name, file_path

def save_file(file, subfolder, quality=80, max_width=1920):
    """
    Procesa y guarda un archivo en Supabase Storage.
    - Si es imagen: Redimensiona, convierte a WebP y sube.
    - Si es PDF: Sube el original directamente.

    Returns:
        str: Clave de almacenamiento (ej: 'comprobantes/pagos/nombre_unico.webp') o None si hay error.
    """
    if not file or not file.filename:
        logger.warning("Intento de guardar archivo vacío o sin nombre")
        return None

    if not allowed_file(file.filename):
        logger.warning(f"Intento de subir archivo con tipo original no permitido: {file.filename}")
        return None

    if not supabase:
        logger.error("Cliente de Supabase no configurado. No se puede guardar archivo.")
        return None

    # Determinar bucket destino según subfolder
    clean_subfolder = subfolder.strip('/') if subfolder else ''
    if 'presentacion' in clean_subfolder:
        bucket_name = 'presentaciones'
    elif 'pago' in clean_subfolder:
        bucket_name = 'pagos'
    else:
        bucket_name = 'comprobantes'

    # Procesar archivo
    content_type = file.content_type
    file_to_upload = file.stream if hasattr(file, 'stream') else file
    target_extension = None
    upload_content_type = content_type

    if content_type and content_type.startswith('image/') and not content_type.endswith('webp'):
        logger.info(f"Procesando imagen: {file.filename} ({content_type})")
        try:
            img = Image.open(file_to_upload)
            img_width, img_height = img.size
            if img_width > max_width:
                ratio = max_width / float(img_width)
                new_height = int(float(img_height) * float(ratio))
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                logger.info(f"Imagen redimensionada a {max_width}x{new_height}")

            webp_buffer = io.BytesIO()
            if img.mode == 'RGBA':
                 img.save(webp_buffer, format='WEBP', quality=quality, lossless=False)
            else:
                 img.convert('RGB').save(webp_buffer, format='WEBP', quality=quality)

            webp_buffer.seek(0)
            file_to_upload = webp_buffer
            target_extension = "webp"
            upload_content_type = "image/webp"
            logger.info(f"Imagen convertida a WebP con calidad {quality}")

        except Exception as e:
            logger.error(f"Error procesando imagen con Pillow: {e}")
            return None
    elif content_type == 'application/pdf':
        logger.info(f"Subiendo PDF directamente: {file.filename}")
    else:
        logger.warning(f"Tipo de archivo no procesado ({content_type}): {file.filename}. Subiendo original.")

    # Generar nombre único
    unique_filename = safe_filename(file.filename, force_extension=target_extension)
    if not unique_filename:
        logger.error("No se pudo generar un nombre de archivo seguro.")
        return None

    # Ruta final del archivo dentro del bucket
    file_path = f"{clean_subfolder}/{unique_filename}" if clean_subfolder else unique_filename

    try:
        if hasattr(file_to_upload, 'seek'):
            file_to_upload.seek(0)
            
        file_bytes = file_to_upload.read() if hasattr(file_to_upload, 'read') else file_to_upload

        supabase.storage.from_(bucket_name).upload(
            path=file_path,
            file=file_bytes,
            file_options={"content-type": upload_content_type}
        )
        
        # Devolver la clave en formato 'bucket/path' para mantener compatibilidad
        storage_key = f"{bucket_name}/{file_path}"
        logger.info(f"Archivo subido exitosamente a Supabase Storage. Clave: {storage_key}, Tipo: {upload_content_type}")
        return storage_key
    except Exception as e:
        logger.error(f"Error guardando archivo en Supabase Storage: {str(e)}")
        return None

def get_presigned_url(storage_key, expiration=3600):
    """
    Genera una URL pre-firmada para acceder a un objeto de Supabase Storage.
    """
    if not storage_key:
        logger.warning("Intento de generar URL pre-firmada para clave vacía.")
        return None

    if not supabase:
        logger.error("Cliente de Supabase no configurado.")
        return None

    try:
        bucket_name, file_path = determine_bucket_and_path(storage_key)
        if not bucket_name or not file_path:
            logger.error(f"No se pudo determinar el bucket o ruta para la clave: {storage_key}")
            return None

        # Supabase API espera create_signed_url
        response = supabase.storage.from_(bucket_name).create_signed_url(
            path=file_path,
            expires_in=expiration
        )
        
        # El response puede venir como dict o como objeto directo dependiendo de la versión
        url = response.get('signedURL') if isinstance(response, dict) else getattr(response, 'signed_url', None) or getattr(response, 'signedURL', None)
        if not url and isinstance(response, dict) and 'signedUrl' in response:
            url = response['signedUrl']
            
        # Si sigue sin encontrarse, a veces response es un string o tiene una estructura diferente
        if not url:
            # Reintentar obtener de forma cruda si es necesario
            if hasattr(response, 'get'):
                url = response.get('signedUrl') or response.get('signedURL')
                
        if url:
            logger.info(f"URL pre-firmada generada para: {storage_key}")
            return url
        else:
            logger.error(f"No se pudo extraer la URL firmada de la respuesta: {response}")
            return None
    except Exception as e:
        logger.error(f"Error inesperado generando URL pre-firmada para Supabase: {str(e)}")
        return None

def delete_file(storage_key):
    """
    Elimina un archivo de Supabase Storage.
    """
    if not storage_key:
        logger.warning("Intento de eliminar archivo con clave vacía.")
        return False

    if not supabase:
        logger.error("Cliente de Supabase no configurado.")
        return False

    try:
        bucket_name, file_path = determine_bucket_and_path(storage_key)
        if not bucket_name or not file_path:
            logger.error(f"No se pudo determinar el bucket o ruta para eliminar la clave: {storage_key}")
            return False

        supabase.storage.from_(bucket_name).remove([file_path])
        logger.info(f"Solicitud de eliminación exitosa en Supabase Storage para: {storage_key}")
        return True
    except Exception as e:
        logger.error(f"Error inesperado eliminando archivo de Supabase Storage: {str(e)}")
        return False
