# common.py
import logging
import re
import werkzeug.exceptions
from functools import wraps
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from flask import jsonify, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt
from marshmallow import ValidationError

from extensions import db
from utils.date_utils import to_peru_time, get_peru_now
import config

# Configuración de logging
logger = logging.getLogger(__name__)

# Re-exportar constante para compatibilidad
MAX_ITEMS_PER_PAGE = config.MAX_ITEMS_PER_PAGE

def parse_iso_datetime(date_string: str, add_timezone: bool = True) -> datetime:
    """
    Parsea una fecha ISO 8601 de manera robusta, manejando diferentes formatos.
    
    Args:
        date_string (str): Fecha en formato ISO 8601.
        add_timezone (bool): Si True, agrega timezone UTC si no está presente.
        
    Returns:
        datetime: Objeto datetime parseado.
        
    Raises:
        ValueError: Si el formato de fecha es inválido.
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
            date_string += '+00:00'
    
    try:
        # Intentar parsear con timezone
        dt = datetime.fromisoformat(date_string)
        
        # Si no tiene timezone y no se solicitó agregar, retornar sin timezone
        if not add_timezone and dt.tzinfo is None:
            return dt
            
        # Si no tiene timezone pero se solicitó agregar, agregar UTC
        if dt.tzinfo is None and add_timezone:
            dt = dt.replace(tzinfo=timezone.utc)
            
        return dt
    except ValueError as e:
        raise ValueError(f"Formato de fecha inválido: {date_string}. Error: {str(e)}")

def handle_db_errors(func: Callable) -> Callable:
    """
    Decorator para manejo centralizado de errores de base de datos y validación.
    
    Args:
        func (Callable): La función a decorar.
        
    Returns:
        Callable: La función decorada con manejo de errores.
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Union[Tuple[Dict[str, Any], int], Any]:
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
            logger.error(f"Error en {func.__name__}: {str(e)}", exc_info=True)
            # Retornar el mensaje de error real para debugging
            return {"message": f"Error interno del servidor: {str(e)}"}, 500
    return wrapper

def rol_requerido(*roles_permitidos: str) -> Callable:
    """
    Decorador para restringir acceso basado en roles.
    
    Args:
        *roles_permitidos (str): Roles permitidos para acceder al recurso.
        
    Returns:
        Callable: El decorador.
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Union[Tuple[Dict[str, Any], int], Any]:
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
                logger.error(f"Error en verificación de rol: {str(e)}", exc_info=True)
                return {"error": "Error en verificación de acceso"}, 401
        return wrapper
    return decorator

def mismo_almacen_o_admin(fn: Callable) -> Callable:
    """
    Decorador para verificar si el usuario tiene acceso al almacén solicitado.
    - Si es admin, tiene acceso a todos los almacenes.
    - Si no es admin, solo tiene acceso a su propio almacén.
    
    Args:
        fn (Callable): La función a decorar.
        
    Returns:
        Callable: La función decorada.
    """
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Union[Tuple[Dict[str, Any], int], Any]:
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
            logger.error(f"Error en verificación de almacén: {str(e)}", exc_info=True)
            return {"error": "Error en verificación de acceso"}, 401
    return wrapper

def validate_pagination_params() -> Tuple[int, int]:
    """
    Extrae y valida parámetros de paginación de la request.
    
    Returns:
        Tuple[int, int]: Una tupla con (page, per_page).
    """
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
        
    try:
        per_page = min(int(request.args.get('per_page', config.DEFAULT_ITEMS_PER_PAGE)), config.MAX_ITEMS_PER_PAGE)
    except (ValueError, TypeError):
        per_page = config.DEFAULT_ITEMS_PER_PAGE
        
    return page, per_page

def create_pagination_response(items: List[Any], pagination: Any) -> Dict[str, Any]:
    """
    Crea respuesta estandarizada con paginación.
    
    Args:
        items (List[Any]): Lista de items de la página actual.
        pagination (Any): Objeto de paginación de SQLAlchemy.
        
    Returns:
        Dict[str, Any]: Diccionario con datos y metadatos de paginación.
    """
    return {
        "data": items,
        "pagination": {
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages
        }
    }

def validate_password(password: str) -> Tuple[bool, Optional[str]]:
    """
    Valida que la contraseña cumpla con los requisitos de seguridad:
    - Mínimo 8 caracteres
    - Al menos una letra
    - Al menos un número
    
    Args:
        password (str): La contraseña a validar.
        
    Returns:
        Tuple[bool, Optional[str]]: (True, None) si es válida, (False, mensaje_error) si no.
    """
    if not password or len(password) < config.MIN_PASSWORD_LENGTH:
        return False, f"La contraseña debe tener al menos {config.MIN_PASSWORD_LENGTH} caracteres"
    
    # Convertir a minúsculas para la validación
    lower_password = password.lower()
    if not (re.search(r'[a-z]', lower_password) and re.search(r'[0-9]', lower_password)):
        return False, "La contraseña debe contener al menos una letra y un número"
        
    lower_password = password.lower()
    if not (re.search(r'[a-z]', lower_password) and re.search(r'[0-9]', lower_password)):
        return False, "La contraseña debe contener al menos una letra y un número"
        
    return True, None

def make_json_serializable(data: Any) -> Any:
    """
    Recursively converts data to JSON-serializable format.
    Handles Decimal -> float, etc.
    """
    import decimal
    
    if isinstance(data, dict):
        return {k: make_json_serializable(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [make_json_serializable(v) for v in data]
    elif isinstance(data, decimal.Decimal):
        return float(data)
    elif isinstance(data, (datetime, date)):
        return data.isoformat()
    return data