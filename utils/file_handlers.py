# utils/file_handlers.py
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app
import boto3
from botocore.exceptions import ClientError
from urllib.parse import urlparse

# Configurar logging
logger = logging.getLogger(__name__)

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', set())
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def safe_filename(filename):
    """Genera un nombre de archivo seguro y único"""
    if not filename:
        return None
    safe_name = secure_filename(filename)
    if not safe_name:
        safe_name = 'file'
    try:
        base, extension = safe_name.rsplit('.', 1)
        extension = extension.lower()
    except ValueError:
        base = safe_name
        extension = 'bin' # Fallback si no hay extensión
    # Asegurar que la base no esté vacía
    if not base:
        base = 'file'
    unique_name = f"{base}_{uuid.uuid4().hex}.{extension}"
    return unique_name

def get_s3_client():
    """Crea y devuelve un cliente S3 de Boto3."""
    region = current_app.config.get('S3_REGION')
    aws_access_key_id = current_app.config.get('S3_KEY')
    aws_secret_access_key = current_app.config.get('S3_SECRET')

    if not region:
        logger.error("AWS_REGION (S3_REGION) no está configurado.")
        return None

    # Usar credenciales explícitas si se proporcionan, sino confiar en el entorno/roles IAM
    if aws_access_key_id and aws_secret_access_key:
        s3_client = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key
        )
        logger.debug("Cliente S3 creado usando credenciales explícitas.")
    else:
        s3_client = boto3.client('s3', region_name=region)
        logger.debug("Cliente S3 creado usando credenciales del entorno/rol IAM.")

    return s3_client

def save_file(file, subfolder):
    """
    Guarda un archivo en S3 de forma segura.

    Args:
        file: Objeto FileStorage de Flask request.files
        subfolder: Prefijo de "carpeta" dentro del bucket S3

    Returns:
        str: URL completa del archivo en S3 o None si hay error
    """
    if not file:
        logger.warning("Intento de guardar archivo vacío")
        return None

    if not allowed_file(file.filename):
        logger.warning(f"Intento de subir archivo con tipo no permitido: {file.filename}")
        return None

    unique_filename = safe_filename(file.filename)
    if not unique_filename:
        logger.error("No se pudo generar un nombre de archivo seguro.")
        return None

    s3_client = get_s3_client()
    bucket_name = current_app.config.get('S3_BUCKET')
    base_location = current_app.config.get('S3_LOCATION')

    if not s3_client or not bucket_name or not base_location:
        logger.error("Configuración S3 incompleta (cliente, bucket o location).")
        return None

    # Limpiar subfolder para evitar problemas de ruta
    clean_subfolder = subfolder.strip('/')
    s3_object_key = f"{clean_subfolder}/{unique_filename}" if clean_subfolder else unique_filename
    # Construir URL final esperada
    file_url = f"{base_location.strip('/')}/{s3_object_key}"

    try:
        s3_client.upload_fileobj(
            file,  # El objeto FileStorage se puede pasar directamente
            bucket_name,
            s3_object_key,
            ExtraArgs={
                # Determinar ContentType basado en extensión (mejora el acceso)
                'ContentType': file.content_type
                # Podrías añadir ACL aquí si no usas políticas de bucket, ej. 'ACL': 'public-read'
            }
        )
        logger.info(f"Archivo subido exitosamente a S3: {file_url}")
        return file_url # Retornar la URL completa de S3
    except ClientError as e:
        logger.error(f"Error subiendo archivo a S3: {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado guardando archivo: {str(e)}")
        return None

def delete_file(file_url):
    """Elimina un archivo de S3 si existe, usando su URL completa."""
    if not file_url:
        return False

    s3_client = get_s3_client()
    bucket_name = current_app.config.get('S3_BUCKET')
    base_location = current_app.config.get('S3_LOCATION')

    if not s3_client or not bucket_name or not base_location:
        logger.error("Configuración S3 incompleta para eliminar archivo.")
        return False

    try:
        # Asegurarse de que la URL base coincida para evitar borrar de otros buckets/ubicaciones
        if not file_url.startswith(base_location):
            logger.error(f"URL del archivo no coincide con S3_LOCATION: {file_url}")
            return False

        # Extraer la clave del objeto de la URL
        object_key = file_url[len(base_location):].lstrip('/')
        if not object_key:
            logger.error(f"No se pudo extraer la clave del objeto de la URL: {file_url}")
            return False

        s3_client.delete_object(Bucket=bucket_name, Key=object_key)
        logger.info(f"Solicitud de eliminación enviada a S3 para: {object_key}")
        # Nota: delete_object no lanza error si el objeto no existe
        return True
    except ClientError as e:
        logger.error(f"Error eliminando archivo de S3: {e}")
        return False
    except Exception as e:
        logger.error(f"Error inesperado eliminando archivo: {str(e)}")
        return False

# get_file_url ya no es necesaria o simplemente devuelve la entrada
# def get_file_url(file_path):
#    ...