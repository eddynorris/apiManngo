# common.py
from flask import jsonify, request
from marshmallow import ValidationError
from extensions import db
from functools import wraps
from flask_jwt_extended import verify_jwt_in_request, get_jwt
import logging
import re
import werkzeug.exceptions
from datetime import datetime, timezone

# Configuración de logging
logger = logging.getLogger(__name__)

# Límite de paginación para evitar consultas pesadas
MAX_ITEMS_PER_PAGE = 100

def parse_iso_datetime(date_string, add_timezone=True):
    """
    Parsea una fecha ISO 8601 de manera robusta, manejando diferentes formatos.
    
    Args:
        date_string (str): Fecha en formato ISO 8601
        add_timezone (bool): Si True, agrega timezone UTC si no está presente
        
    Returns:
        datetime: Objeto datetime parseado
        
    Raises:
        ValueError: Si el formato de fecha es inválido
    """
    if not date_string:
        raise ValueError("La fecha no puede estar vacía")
    
    # Normalizar la cadena de fecha
    date_string = date_string.strip()
    
    # Manejar diferentes formatos de timezone
    if date_string.endswith('Z'):
        # Formato con Z (Zulu time)
        date_string = date_string.replace('Z', '+00:00')
    elif '+' not in date_string and '-' not in date_string[-6:]:
        # No tiene timezone, agregar UTC si se solicita
        if add_timezone:
            if 'T' in date_string:
                date_string += '+00:00'
            else:
                # Solo fecha, agregar hora y timezone
                date_string += 'T00:00:00+00:00'
    
    try:
        # Intentar parsear con fromisoformat
        dt = datetime.fromisoformat(date_string)
        
        # Si add_timezone es True y no tiene timezone, agregar UTC
        if add_timezone and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        return dt
    except ValueError as e:
        # Si falla, intentar otros formatos comunes
        formats_to_try = [
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%Y-%m-%dT%H:%M:%SZ', 
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d'
        ]
        
        for fmt in formats_to_try:
            try:
                dt = datetime.strptime(date_string.replace('Z', ''), fmt)
                if add_timezone:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
                
        # Si ningún formato funciona, lanzar error descriptivo
        raise ValueError(f"Formato de fecha inválido: '{date_string}'. Use formato ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)")

def sanitize_input(value, allowed_pattern=None):
    """Sanitiza entrada para prevenir inyección SQL y otros ataques"""
    if value is None:
        return None
        
    # Convertir a string si no lo es
    if not isinstance(value, str):
        value = str(value)
        
    # Eliminar caracteres potencialmente peligrosos
    value = value.strip()
    
    # Si hay un patrón permitido, validar contra él
    if allowed_pattern and not re.match(allowed_pattern, value):
        return None
        
    return value

def handle_db_errors(func):
    """Decorator para manejo centralizado de errores"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # Capturar y sanitizar parámetros de ID para prevenir inyección
            for key, value in kwargs.items():
                if key.endswith('_id'):
                    try:
                        kwargs[key] = int(value)
                    except (ValueError, TypeError):
                        return {"message": f"ID inválido: {key}"}, 400
            
            return func(*args, **kwargs)
        except ValidationError as e:
            logger.warning(f"Error de validación: {e.messages}")
            return {"message": "Datos inválidos", "errors": e.messages}, 400
        except werkzeug.exceptions.HTTPException as e:
            # Permitir que las excepciones HTTP se propaguen sin modificar
            raise e
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en {func.__name__}: {str(e)}")
            return {"message": "Error interno del servidor"}, 500
    return wrapper

def rol_requerido(*roles_permitidos):
    """
    Decorador para restringir acceso basado en roles
    Ejemplo: @rol_requerido('admin', 'gerente')
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                # Verificar token JWT
                verify_jwt_in_request()
                
                # Obtener claims
                claims = get_jwt()
                rol_usuario = claims.get('rol')
                
                # Verificar si el rol está permitido
                if rol_usuario not in roles_permitidos:
                    logger.warning(f"Acceso denegado: Usuario con rol '{rol_usuario}' intentó acceder a ruta restringida")
                    return {
                        "error": "Acceso denegado",
                        "mensaje": "No tiene permisos suficientes para esta acción",
                        "required_roles": list(roles_permitidos),
                        "current_role": rol_usuario
                    }, 403
                
                # Si el rol es válido, continuar
                return fn(*args, **kwargs)
                
            except Exception as e:
                logger.error(f"Error en verificación de rol: {str(e)}")
                return {"error": "Error en verificación de acceso"}, 401
        return wrapper
    return decorator

def mismo_almacen_o_admin(fn):
    """
    Decorador para verificar si el usuario tiene acceso al almacén solicitado
    - Si es admin, tiene acceso a todos los almacenes
    - Si no es admin, solo tiene acceso a su propio almacén
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            # Verificar token JWT
            verify_jwt_in_request()
            
            # Obtener claims
            claims = get_jwt()
            
            # Si es admin, permitir acceso
            if claims.get('rol') == 'admin':
                return fn(*args, **kwargs)
            
            # Verificar si está intentando acceder a datos de otro almacén
            almacen_id_request = kwargs.get('almacen_id')
            if almacen_id_request is not None:
                # Validar que almacen_id sea un entero válido
                try:
                    almacen_id_request = int(almacen_id_request)
                except (ValueError, TypeError):
                    return ({
                        'message': 'ID de almacén inválido',
                        'error': 'parametro_invalido'
                    }), 400
                    
                # Verificar si el almacén coincide con el del usuario
                usuario_almacen_id = claims.get('almacen_id')
                if usuario_almacen_id is None:
                    return ({
                        'message': 'Usuario sin almacén asignado',
                        'error': 'almacen_no_asignado'
                    }), 403
                    
                if int(almacen_id_request) != int(usuario_almacen_id):
                    logger.warning(f"Intento de acceso a almacén no autorizado: Usuario {claims.get('username')} intentó acceder a almacén {almacen_id_request}")
                    return ({
                        'message': 'No tiene permiso para acceder a este almacén',
                        'error': 'acceso_denegado'
                    }), 403
                    
            # Verificar almacén en datos JSON para métodos POST/PUT
            if request.is_json and request.method in ['POST', 'PUT']:
                data = request.get_json()
                if data and 'almacen_id' in data:
                    try:
                        almacen_id_json = int(data['almacen_id'])
                        if almacen_id_json != int(claims.get('almacen_id', 0)):
                            logger.warning(f"Intento de modificación de almacén no autorizado: Usuario {claims.get('username')}")
                            return ({
                                'message': 'No tiene permiso para modificar este almacén',
                                'error': 'acceso_denegado'
                            }), 403
                    except (ValueError, TypeError):
                        return ({
                            'message': 'ID de almacén inválido en datos',
                            'error': 'parametro_invalido'
                        }), 400
            
            return fn(*args, **kwargs)
            
        except werkzeug.exceptions.HTTPException as e:
            # Permitir que las excepciones HTTP se propaguen sin modificar
            raise e
        except Exception as e:
            logger.error(f"Error en verificación de almacén: {str(e)}")
            return {"error": "Error en verificación de acceso"}, 401
    return wrapper

def validate_pagination_params():
    """Extrae y valida parámetros de paginación"""
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
        
    try:
        per_page = min(int(request.args.get('per_page', 10)), MAX_ITEMS_PER_PAGE)
    except (ValueError, TypeError):
        per_page = 10
        
    return page, per_page

def create_pagination_response(items, pagination):
    """Crea respuesta estandarizada con paginación"""
    return {
        "data": items,
        "pagination": {
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages
        }
    }