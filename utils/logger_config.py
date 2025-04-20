# utils/logger_config.py
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from flask import has_request_context, request

class RequestFormatter(logging.Formatter):
    """Formateador personalizado que incluye información de solicitud HTTP"""
    
    def format(self, record):
        if has_request_context():
            record.url = request.url
            record.remote_addr = request.remote_addr
            record.method = request.method
            # Añadir user_id de JWT si está disponible
            user_id = "anónimo"
            try:
                from flask_jwt_extended import get_jwt_identity
                jwt_identity = get_jwt_identity()
                if jwt_identity:
                    user_id = jwt_identity
            except:
                pass
            record.user_id = user_id
        else:
            record.url = None
            record.remote_addr = None
            record.method = None
            record.user_id = None
            
        return super().format(record)

def setup_logging(app):
    """Configura el sistema de logging para la aplicación"""
    
    # Determinar nivel de log según entorno
    log_level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    
    # Remover handlers existentes
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    # Configurar handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # Configurar formatos según entorno
    if app.config['ENV'] == 'production':
        # Formato JSON para producción (más fácil para herramientas de análisis)
        console_format = '{"time":"%(asctime)s", "level":"%(levelname)s", "module":"%(name)s", "message":"%(message)s"'
        console_format += ', "method":"%(method)s", "url":"%(url)s", "ip":"%(remote_addr)s", "user":"%(user_id)s"}'
    else:
        # Formato más legible para desarrollo
        console_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        console_format += ' [%(method)s %(url)s IP:%(remote_addr)s User:%(user_id)s]'
    
    console_formatter = RequestFormatter(console_format)
    console_handler.setFormatter(console_formatter)
    
    # Configurar handler para archivo si estamos en producción
    if app.config['ENV'] == 'production':
        log_dir = os.environ.get('LOG_DIR', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            f"{log_dir}/app.log", 
            maxBytes=10*1024*1024,  # 10MB
            backupCount=10
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(console_formatter)
        root_logger.addHandler(file_handler)
    
    # Añadir handler de consola al logger raíz
    root_logger.addHandler(console_handler)
    root_logger.setLevel(log_level)
    
    # Ajustar nivel de logs para bibliotecas muy verbosas
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    
    # Log inicial
    app.logger.info(f"Logging configurado. Nivel: {log_level_name}")
    
    return root_logger