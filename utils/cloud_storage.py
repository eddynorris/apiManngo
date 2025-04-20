# utils/cloud_storage.py
from google.cloud import storage
import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app

# Configurar logging
logger = logging.getLogger(__name__)

# Inicializar cliente de GCS
def get_storage_client():
    try:
        return storage.Client()
    except Exception as e:
        logger.error(f"Error al inicializar cliente de Cloud Storage: {str(e)}")
        return None

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config.get('ALLOWED_EXTENSIONS', {})

def upload_to_gcs(file, subfolder):
    """
    Sube un archivo a Google Cloud Storage
    
    Args:
        file: Objeto de archivo de Flask request.files
        subfolder: Carpeta donde guardar
        
    Returns:
        str: URL pública del archivo guardado o None si hay error
    """
    if not file or not file.filename:
        logger.warning("Intento de subir archivo vacío a GCS")
        return None
        
    # Verificar que el archivo es permitido
    if not allowed_file(file.filename):
        logger.warning(f"Intento de subir archivo con tipo no permitido: {file.filename}")
        return None
    
    # Generar nombre seguro único
    original_filename = secure_filename(file.filename)
    if not original_filename:  # Si secure_filename retorna vacío
        logger.warning(f"Nombre de archivo inválido: {file.filename}")
        extension = "bin"
    else:
        try:
            extension = original_filename.rsplit('.', 1)[1].lower()
        except IndexError:
            extension = "bin"
    
    unique_filename = f"{uuid.uuid4().hex}.{extension}"
    
    # Validar subfolder
    if '..' in subfolder or subfolder.startswith('/'):
        logger.error(f"Subfolder potencialmente peligroso: {subfolder}")
        return None
        
    # Normalizar path
    storage_path = f"{subfolder}/{unique_filename}"
        
    try:
        # Obtener configuración
        bucket_name = os.environ.get('GCS_BUCKET_NAME')
        if not bucket_name:
            logger.error("Variable GCS_BUCKET_NAME no configurada")
            return None
            
        # Obtener cliente de Storage
        storage_client = get_storage_client()
        if not storage_client:
            return None
            
        # Obtener bucket
        bucket = storage_client.bucket(bucket_name)
        
        # Crear blob y subir
        blob = bucket.blob(storage_path)
        
        # Leer contenido
        file_content = file.read()
        content_type = file.content_type
        
        # Subir contenido
        blob.upload_from_string(
            file_content,
            content_type=content_type
        )
        
        # Hacer público el archivo
        blob.make_public()
        
        # Registrar éxito
        logger.info(f"Archivo subido a GCS: {storage_path}")
        
        # Devolver URL pública
        return blob.public_url
    
    except Exception as e:
        logger.error(f"Error al subir archivo a GCS: {str(e)}")
        return None

def delete_from_gcs(file_url):
    """
    Elimina un archivo de Google Cloud Storage
    
    Args:
        file_url: URL pública o ruta del archivo
        
    Returns:
        bool: True si se eliminó correctamente, False si hubo error
    """
    if not file_url:
        return False
        
    try:
        # Obtener configuración
        bucket_name = os.environ.get('GCS_BUCKET_NAME')
        if not bucket_name:
            logger.error("Variable GCS_BUCKET_NAME no configurada")
            return False
            
        # Obtener cliente de Storage
        storage_client = get_storage_client()
        if not storage_client:
            return False
            
        # Obtener bucket
        bucket = storage_client.bucket(bucket_name)
        
        # Extraer ruta del archivo de la URL
        if bucket_name in file_url:
            # Es una URL pública
            parts = file_url.split(f"{bucket_name}/")
            if len(parts) < 2:
                logger.error(f"Formato de URL inválido: {file_url}")
                return False
            file_path = parts[1]
        else:
            # Es una ruta directa
            file_path = file_url
        
        # Eliminar archivo
        blob = bucket.blob(file_path)
        blob.delete()
        
        # Registrar éxito
        logger.info(f"Archivo eliminado de GCS: {file_path}")
        
        return True
    
    except Exception as e:
        logger.error(f"Error al eliminar archivo de GCS: {str(e)}")
        return False