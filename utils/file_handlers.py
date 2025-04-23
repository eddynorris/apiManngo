# utils/file_handlers.py
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from urllib.parse import urlparse # Necesario para delete_file

# Configurar logging
logger = logging.getLogger(__name__)

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', set())
    # Asegurarse que filename no sea None o vacío
    if not filename:
        return False
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def safe_filename(filename):
    """Genera un nombre de archivo seguro y único"""
    if not filename:
        return None
    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = 'file' # Fallback si secure_filename devuelve vacío
    try:
        base, extension = safe_name.rsplit('.', 1)
        extension = extension.lower()
    except ValueError:
        # Si no hay punto (extensión), usa un nombre base y una extensión por defecto
        base = safe_name
        extension = 'bin' # O elige una extensión por defecto apropiada
    # Asegurar que la base no esté vacía después de secure_filename
    if not base:
        base = 'file'
    unique_name = f"{base}_{uuid.uuid4().hex}.{extension}"
    return unique_name

def get_s3_client():
    """
    Crea y devuelve un cliente S3 de Boto3.
    Prioriza el Rol IAM asociado a la instancia EC2.
    Usa credenciales explícitas (S3_KEY, S3_SECRET) solo como fallback,
    lo cual NO se recomienda en EC2.
    """
    region = current_app.config.get('S3_REGION')
    aws_access_key_id = current_app.config.get('S3_KEY')
    aws_secret_access_key = current_app.config.get('S3_SECRET')

    if not region:
        logger.error("AWS_REGION (S3_REGION) no está configurado.")
        return None

    try:
        # Intenta crear cliente sin credenciales explícitas primero (usará Rol IAM/variables de entorno)
        s3_client = boto3.client('s3', region_name=region)
        # Verifica si las credenciales se cargaron (opcional, pero útil para debug)
        s3_client.list_buckets() # Una llamada simple para forzar la carga de credenciales
        logger.debug("Cliente S3 creado usando credenciales del entorno/rol IAM.")
        return s3_client
    except (NoCredentialsError, ClientError) as e:
        logger.warning(f"No se pudieron obtener credenciales del entorno/rol IAM: {e}. Intentando con credenciales explícitas (NO RECOMENDADO EN EC2)...")
        # Fallback a credenciales explícitas (si se proporcionan)
        if aws_access_key_id and aws_secret_access_key:
            try:
                s3_client = boto3.client(
                    's3',
                    region_name=region,
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key
                )
                s3_client.list_buckets() # Verificar credenciales explícitas
                logger.warning("Cliente S3 creado usando credenciales explícitas. ¡Considere usar Roles IAM en EC2!")
                return s3_client
            except (NoCredentialsError, ClientError) as explicit_e:
                logger.error(f"Error al crear cliente S3 con credenciales explícitas: {explicit_e}")
                return None
        else:
            logger.error("No se encontraron credenciales S3 (ni rol IAM ni explícitas).")
            return None
    except Exception as e:
         logger.error(f"Error inesperado al crear cliente S3: {e}")
         return None


def save_file(file, subfolder):
    """
    Guarda un archivo en S3 de forma segura (privado por defecto).

    Args:
        file: Objeto FileStorage de Flask request.files
        subfolder: Prefijo de "carpeta" dentro del bucket S3

    Returns:
        str: Clave del objeto S3 (ej: 'pagos/nombre_unico.jpg') si fue exitoso, o None si hay error.
    """
    if not file or not file.filename: # Añadido chequeo de file.filename
        logger.warning("Intento de guardar archivo vacío o sin nombre")
        return None

    if not allowed_file(file.filename):
        logger.warning(f"Intento de subir archivo con tipo no permitido: {file.filename}")
        return None

    # ---- Solo lógica S3 (asumiendo que es el objetivo principal en AWS) ----
    s3_client = get_s3_client()
    bucket_name = current_app.config.get('S3_BUCKET')

    if not s3_client or not bucket_name:
        logger.error("Configuración S3 incompleta (cliente o bucket). No se puede guardar archivo.")
        # Podrías añadir un fallback a lógica local aquí si es necesario
        return None

    unique_filename = safe_filename(file.filename)
    if not unique_filename:
        logger.error("No se pudo generar un nombre de archivo seguro.")
        return None

    # Limpiar subfolder
    clean_subfolder = subfolder.strip('/') if subfolder else ''
    # Construir la clave del objeto S3
    s3_object_key = f"{clean_subfolder}/{unique_filename}" if clean_subfolder else unique_filename

    try:
        # Subir el archivo a S3. Por defecto es privado.
        s3_client.upload_fileobj(
            file,
            bucket_name,
            s3_object_key,
            ExtraArgs={'ContentType': file.content_type} # Ayuda al navegador al descargar/mostrar
        )
        logger.info(f"Archivo subido exitosamente a S3 (privado). Clave: {s3_object_key}")
        # --- Devolver la CLAVE del objeto, NO la URL ---
        return s3_object_key
    except ClientError as e:
        logger.error(f"Error subiendo archivo a S3 (ClientError): {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado guardando archivo S3: {str(e)}")
        return None

def get_presigned_url(s3_object_key, expiration=3600):
    """
    Genera una URL pre-firmada para acceder a un objeto S3 privado.

    Args:
        s3_object_key (str): La clave del objeto en S3 (devuelta por save_file).
        expiration (int): Tiempo en segundos durante el cual la URL será válida. Default: 1 hora.

    Returns:
        str: La URL pre-firmada, o None si hay error.
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
        # Verificar si el error es porque la clave no existe
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

    Args:
        s3_object_key (str): Clave del objeto S3 a eliminar.
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

# --- La lógica local se puede mantener como fallback o eliminar si S3 es mandatorio ---
# def delete_local_file(relative_url_path): ...
# (El código de delete_local_file y la lógica local en save_file/delete_file
#  se pueden eliminar si decides usar solo S3 en producción)
