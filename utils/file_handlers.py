# utils/file_handlers.py
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from urllib.parse import urlparse
from PIL import Image # <--- Importar Pillow
import io # <--- Importar io para manejo en memoria

# Configurar logging
logger = logging.getLogger(__name__)

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    # Permitir formatos de imagen comunes Y PDF
    allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif', 'pdf'}) # <-- Añadido 'pdf'
    if not filename:
        return False
    # Obtener extensión de forma segura
    parts = filename.rsplit('.', 1)
    if len(parts) == 2:
        return parts[1].lower() in allowed_extensions
    return False # No tiene extensión

def safe_filename(filename, force_extension=None):
    """
    Genera un nombre de archivo seguro y único.
    Si force_extension se proporciona, usa esa extensión.
    Sino, intenta preservar la extensión original.
    """
    if not filename:
        return None
    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = 'file' # Fallback

    original_extension = None
    try:
        base, original_extension = safe_name.rsplit('.', 1)
        original_extension = original_extension.lower()
    except ValueError:
        base = safe_name # No había extensión original

    if not base:
        base = 'file' # Asegurar que la base no esté vacía

    # Determinar la extensión final
    final_extension = force_extension if force_extension else original_extension
    if not final_extension:
         final_extension = 'bin' # Fallback si no hay extensión original ni forzada

    unique_name = f"{base}_{uuid.uuid4().hex}.{final_extension}"
    return unique_name

def get_s3_client():
    """
    Crea y devuelve un cliente S3 de Boto3.
    Prioriza el Rol IAM asociado a la instancia EC2.
    """
    region = current_app.config.get('S3_REGION')
    if not region:
        logger.error("AWS_REGION (S3_REGION) no está configurado.")
        return None
    try:
        s3_client = boto3.client('s3', region_name=region)
        s3_client.list_buckets() # Verificar credenciales del rol/entorno
        logger.debug("Cliente S3 creado usando credenciales del entorno/rol IAM.")
        return s3_client
    except (NoCredentialsError, ClientError) as e:
        logger.error(f"No se pudieron obtener credenciales S3 (ni rol IAM ni explícitas): {e}")
        return None
    except Exception as e:
         logger.error(f"Error inesperado al crear cliente S3: {e}")
         return None


def save_file(file, subfolder, quality=80, max_width=1920):
    """
    Procesa y guarda un archivo en S3 de forma segura (privado).
    - Si es imagen (jpg, png, gif): Redimensiona, convierte a WebP y sube.
    - Si es PDF: Sube el original directamente.

    Args:
        file: Objeto FileStorage de Flask request.files
        subfolder: Prefijo de "carpeta" dentro del bucket S3
        quality (int): Calidad para la conversión a WebP (0-100).
        max_width (int): Ancho máximo al que redimensionar la imagen.

    Returns:
        str: Clave del objeto S3 (ej: 'pagos/nombre_unico.webp' o 'pagos/doc.pdf') si fue exitoso, o None si hay error.
    """
    if not file or not file.filename:
        logger.warning("Intento de guardar archivo vacío o sin nombre")
        return None

    # Verificar extensión original permitida
    if not allowed_file(file.filename):
        logger.warning(f"Intento de subir archivo con tipo original no permitido: {file.filename}")
        return None

    s3_client = get_s3_client()
    bucket_name = current_app.config.get('S3_BUCKET')
    if not s3_client or not bucket_name:
        logger.error("Configuración S3 incompleta (cliente o bucket). No se puede guardar archivo.")
        return None

    # Determinar tipo y procesar condicionalmente
    content_type = file.content_type
    file_to_upload = file.stream # Por defecto, subir el stream original
    target_extension = None # Para forzar extensión en safe_filename
    upload_content_type = content_type # ContentType para S3

    if content_type and content_type.startswith('image/') and not content_type.endswith('webp'):
        # --- Procesamiento de Imagen a WebP ---
        logger.info(f"Procesando imagen: {file.filename} ({content_type})")
        try:
            img = Image.open(file.stream)
            img_width, img_height = img.size
            if img_width > max_width:
                ratio = max_width / float(img_width)
                new_height = int(float(img_height) * float(ratio))
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                logger.info(f"Imagen redimensionada a {max_width}x{new_height}")

            webp_buffer = io.BytesIO()
            # Manejar RGBA (ej. PNG con transparencia)
            if img.mode == 'RGBA':
                 # WebP soporta transparencia, guardar como está
                 img.save(webp_buffer, format='WEBP', quality=quality, lossless=False) # Ajustar lossless si se prefiere
            else:
                 # Convertir a RGB si no lo es (ej. P, L) antes de guardar como WebP
                 img.convert('RGB').save(webp_buffer, format='WEBP', quality=quality)

            webp_buffer.seek(0)
            file_to_upload = webp_buffer # Cambiar el stream a subir
            target_extension = "webp" # Forzar extensión .webp
            upload_content_type = "image/webp" # ContentType para S3
            logger.info(f"Imagen convertida a WebP con calidad {quality}")

        except Exception as e:
            logger.error(f"Error procesando imagen con Pillow: {e}")
            return None
        # ------------------------------------------
    elif content_type == 'application/pdf':
        # --- Manejo de PDF ---
        logger.info(f"Subiendo PDF directamente: {file.filename}")
        # No hay procesamiento, se usa file.stream original
        # No se fuerza extensión, safe_filename usará .pdf
        # upload_content_type ya es 'application/pdf'
        pass # No se necesita hacer nada extra aquí
    else:
        # --- Otros tipos de archivo (si se quieren permitir) ---
        # Por ahora, si no es imagen procesable o PDF, lo rechazamos o lo subimos tal cual
        logger.warning(f"Tipo de archivo no procesado ({content_type}): {file.filename}. Subiendo original.")
        # Podrías descomentar la siguiente línea para rechazar tipos no esperados:
        # return None

    # Generar nombre seguro (forzando .webp si es imagen, sino usa original)
    unique_filename = safe_filename(file.filename, force_extension=target_extension)
    if not unique_filename:
        logger.error("No se pudo generar un nombre de archivo seguro.")
        return None

    # Limpiar subfolder
    clean_subfolder = subfolder.strip('/') if subfolder else ''
    # Construir la clave del objeto S3
    s3_object_key = f"{clean_subfolder}/{unique_filename}" if clean_subfolder else unique_filename

    try:
        # Subir el buffer correspondiente (original o webp) a S3
        file_to_upload.seek(0) # Asegurar que el stream esté al inicio
        s3_client.upload_fileobj(
            file_to_upload,
            bucket_name,
            s3_object_key,
            ExtraArgs={'ContentType': upload_content_type} # Usar el ContentType determinado
        )
        logger.info(f"Archivo subido exitosamente a S3 (privado). Clave: {s3_object_key}, Tipo: {upload_content_type}")
        # Devolver la CLAVE del objeto
        return s3_object_key
    except ClientError as e:
        logger.error(f"Error subiendo archivo a S3 (ClientError): {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado guardando archivo S3: {str(e)}")
        return None

# --- Funciones get_presigned_url y delete_file (sin cambios) ---

def get_presigned_url(s3_object_key, expiration=3600):
    """
    Genera una URL pre-firmada para acceder a un objeto S3 privado.
    """
    if not s3_object_key:
        logger.warning("Intento de generar URL pre-firmada para clave vacía.")
        return None

    s3_client = get_s3_client()
    bucket_name = current_app.config.get('S3_BUCKET')

    if not s3_client or not bucket_name:
        logger.error("Configuración S3 incompleta (cliente o bucket) para generar URL pre-firmada.")
        return None

    try:
        response = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_object_key},
            ExpiresIn=expiration
        )
        logger.info(f"URL pre-firmada generada para: {s3_object_key}")
        return response
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
             logger.error(f"No se encontró la clave S3 '{s3_object_key}' al generar URL pre-firmada.")
        else:
             logger.error(f"Error generando URL pre-firmada para {s3_object_key}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado generando URL pre-firmada: {str(e)}")
        return None


def delete_file(s3_object_key):
    """
    Elimina un archivo de S3 usando su clave de objeto.
    """
    if not s3_object_key:
        logger.warning("Intento de eliminar archivo con clave S3 vacía.")
        return False

    s3_client = get_s3_client()
    bucket_name = current_app.config.get('S3_BUCKET')

    if not s3_client or not bucket_name:
        logger.error("Configuración S3 incompleta (cliente o bucket) para eliminar archivo.")
        return False

    try:
        s3_client.delete_object(Bucket=bucket_name, Key=s3_object_key)
        logger.info(f"Solicitud de eliminación enviada a S3 para: {s3_object_key}")
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            logger.warning(f"Objeto S3 no encontrado para eliminar (NoSuchKey): {s3_object_key}")
            return True # Considerar éxito si ya no existe
        else:
            logger.error(f"Error eliminando archivo de S3: {e}")
            return False
    except Exception as e:
        logger.error(f"Error inesperado eliminando archivo S3: {str(e)}")
        return False
