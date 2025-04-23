# utils/file_handlers.py
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app
import boto3
from botocore.exceptions import ClientError
from urllib.parse import urljoin, urlparse

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
    Guarda un archivo en S3 de forma segura. (O localmente si S3 no está configurado)

    Args:
        file: Objeto FileStorage de Flask request.files
        subfolder: Prefijo de "carpeta" dentro del bucket S3 o ruta local

    Returns:
        str: URL completa del archivo en S3 o ruta relativa local, o None si hay error
    """
    if not file:
        logger.warning("Intento de guardar archivo vacío")
        return None

    if not allowed_file(file.filename):
        logger.warning(f"Intento de subir archivo con tipo no permitido: {file.filename}")
        return None
    
    # ---- Lógica de decisión S3 vs Local ----
    use_s3 = all([
        current_app.config.get('S3_BUCKET'),
        current_app.config.get('S3_REGION'),
        current_app.config.get('S3_LOCATION')
    ])

    unique_filename = safe_filename(file.filename)
    if not unique_filename:
        logger.error("No se pudo generar un nombre de archivo seguro.")
        return None

    # Limpiar subfolder para evitar problemas de ruta
    clean_subfolder = subfolder.strip('/')

    if use_s3:
        # --- Lógica S3 ---
        s3_client = get_s3_client()
        bucket_name = current_app.config.get('S3_BUCKET')
        base_location = current_app.config.get('S3_LOCATION')

        if not s3_client:
            logger.error("Configuración S3 incompleta (cliente).")
            return None

        s3_object_key = f"{clean_subfolder}/{unique_filename}" if clean_subfolder else unique_filename
        # Usar urljoin para evitar doble slash y manejar base_location con/sin slash final
        file_url = urljoin(base_location + ('/' if not base_location.endswith('/') else ''), s3_object_key)


        try:
            s3_client.upload_fileobj(
                file,
                bucket_name,
                s3_object_key,
                ExtraArgs={'ContentType': file.content_type}
            )
            logger.info(f"Archivo subido exitosamente a S3: {file_url}")
            return file_url
        except ClientError as e:
            logger.error(f"Error subiendo archivo a S3: {e}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado guardando archivo S3: {str(e)}")
            return None

    else:
        # --- Lógica Local ---
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads') # Usar config o default
        # Asegurar que la carpeta base exista (usando la ruta absoluta de app.config)
        if not os.path.exists(upload_folder):
             os.makedirs(upload_folder, exist_ok=True)
             logger.info(f"Carpeta de subida local creada: {upload_folder}")

        # Crear subcarpeta si no existe
        target_folder = os.path.join(upload_folder, clean_subfolder) if clean_subfolder else upload_folder
        os.makedirs(target_folder, exist_ok=True)

        local_file_path = os.path.join(target_folder, unique_filename)
        # Construir URL relativa correctamente asegurando un solo '/' al inicio
        relative_url_path = os.path.join(clean_subfolder, unique_filename).replace('\\', '/') 
        
        try:
            file.save(local_file_path)
            logger.info(f"Archivo guardado localmente en: {local_file_path}")
            # Devolver la URL relativa para ser usada por el endpoint de servir archivos
            # Asumimos que la URL base es /uploads/
            final_relative_url = f"/uploads/{relative_url_path}"
            logger.debug(f"Devolviendo URL relativa local: {final_relative_url}")
            return final_relative_url
        except Exception as e:
            logger.error(f"Error guardando archivo localmente: {str(e)}")
            return None


def delete_file(file_identifier):
    """
    Elimina un archivo de S3 o localmente.

    Args:
        file_identifier (str): URL completa de S3 o ruta relativa local (ej: /uploads/...)
    """
    if not file_identifier:
        logger.warning("Intento de eliminar archivo con identificador vacío.")
        return False

    # ---- Lógica de decisión S3 vs Local ----
    use_s3 = all([
        current_app.config.get('S3_BUCKET'),
        current_app.config.get('S3_REGION'),
        current_app.config.get('S3_LOCATION')
    ])
    
    is_s3_url = use_s3 and file_identifier.startswith(current_app.config.get('S3_LOCATION', 'https://impossible-prefix/'))
    is_local_path = file_identifier.startswith('/uploads/')

    if use_s3 and is_s3_url:
        # --- Lógica de eliminación S3 ---
        s3_client = get_s3_client()
        bucket_name = current_app.config.get('S3_BUCKET')
        base_location = current_app.config.get('S3_LOCATION')

        if not s3_client: # No necesitamos comprobar bucket/location aquí de nuevo
            logger.error("Configuración S3 incompleta (cliente) para eliminar archivo.")
            return False

        try:
            # Extraer la clave del objeto de la URL S3
            # Usamos urlparse para ser más robustos
            parsed_url = urlparse(file_identifier)
            object_key = parsed_url.path.lstrip('/')
            
            if not object_key:
                logger.error(f"No se pudo extraer la clave del objeto de la URL S3: {file_identifier}")
                return False

            s3_client.delete_object(Bucket=bucket_name, Key=object_key)
            logger.info(f"Solicitud de eliminación enviada a S3 para: {object_key}")
            return True
        except ClientError as e:
            # Distinguir 'NoSuchKey' (no encontrado) de otros errores
            if e.response['Error']['Code'] == 'NoSuchKey':
                 logger.warning(f"Objeto S3 no encontrado para eliminar (NoSuchKey): {object_key}")
                 return True # Considerar éxito si no existe
            else:
                logger.error(f"Error eliminando archivo de S3: {e}")
                return False
        except Exception as e:
            logger.error(f"Error inesperado eliminando archivo S3: {str(e)}")
            return False
            
    elif is_local_path:
         # --- Lógica de eliminación Local ---
        return delete_local_file(file_identifier)
        
    else:
        # El identificador no coincide ni con S3 esperado ni con ruta local
        logger.warning(f"Formato de identificador de archivo no reconocido para eliminación: {file_identifier}. Use_s3={use_s3}")
        # Intentar eliminar localmente como último recurso si no se esperaba S3
        if not use_s3 and file_identifier: 
            return delete_local_file('/uploads/' + file_identifier.lstrip('/')) # Asumir que falta /uploads/
        return False


def delete_local_file(relative_url_path):
    """Función auxiliar para eliminar archivos locales."""
    # Validar que la ruta empieza con /uploads/
    if not relative_url_path or not relative_url_path.startswith('/uploads/'):
        logger.warning(f"Intento de eliminar archivo local con ruta inválida: {relative_url_path}")
        return False

    upload_folder = current_app.config.get('UPLOAD_FOLDER') # Obtener ruta absoluta
    if not upload_folder:
        logger.error("UPLOAD_FOLDER no está configurado en la app para eliminación local.")
        return False
        
    # Construir la ruta completa del archivo local
    # Quitar el /uploads/ inicial para unir correctamente con la carpeta base
    relative_file_part = relative_url_path[len('/uploads/'):].lstrip('/')
    file_system_path = os.path.join(upload_folder, relative_file_part)
    # Normalizar la ruta por seguridad y consistencia
    file_system_path = os.path.normpath(file_system_path)

    # Doble chequeo de seguridad: asegurar que la ruta final sigue dentro de UPLOAD_FOLDER
    if not file_system_path.startswith(os.path.normpath(upload_folder)):
         logger.error(f"Intento de eliminación fuera de UPLOAD_FOLDER detectado: {file_system_path}")
         return False

    try:
        if os.path.exists(file_system_path) and os.path.isfile(file_system_path):
            os.remove(file_system_path)
            logger.info(f"Archivo local eliminado: {file_system_path}")
            return True
        else:
            logger.warning(f"Archivo local no encontrado o no es un archivo para eliminar: {file_system_path}")
            return True # Considerar éxito si no existe o no es un archivo
    except Exception as e:
        logger.error(f"Error eliminando archivo local {file_system_path}: {str(e)}")
        return False

# get_file_url ya no es necesaria o simplemente devuelve la entrada
# def get_file_url(file_path):
#    ...