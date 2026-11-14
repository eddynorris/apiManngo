# common.py
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import wraps
from typing import Any

import werkzeug.exceptions
from flask import Response, has_request_context, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from marshmallow import ValidationError
from werkzeug.datastructures import Headers

import config
from extensions import db
from models import Users

# Configuración de logging
logger = logging.getLogger(__name__)

# Re-exportar constante para compatibilidad
MAX_ITEMS_PER_PAGE = config.MAX_ITEMS_PER_PAGE

AUTH_VERSION_CLAIM = 'auth_version'


def obtener_usuario_actual() -> Users:
    """Resuelve el usuario vigente por el ID del subject del JWT.

    No busca por username ni confía en claims de rol/almacén. Debe invocarse
    después de ``verify_jwt_in_request``. Los IDs no numéricos, no positivos o
    de un usuario inexistente se rechazan como identidad no autorizada.
    """
    identity = get_jwt_identity()
    if isinstance(identity, bool) or identity is None:
        raise werkzeug.exceptions.Unauthorized("Identidad inválida")

    if isinstance(identity, int):
        usuario_id = identity
    elif isinstance(identity, str) and identity.isascii() and identity.isdigit():
        usuario_id = int(identity)
    else:
        raise werkzeug.exceptions.Unauthorized("Identidad inválida")

    if usuario_id <= 0:
        raise werkzeug.exceptions.Unauthorized("Identidad inválida")

    usuario = db.session.get(Users, usuario_id)
    if usuario is None:
        raise werkzeug.exceptions.Unauthorized("Usuario no autorizado")
    return usuario

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
            dt = dt.replace(tzinfo=UTC)

        return dt
    except ValueError as e:
        raise ValueError(f"Formato de fecha inválido: {date_string}. Error: {str(e)}") from e

def _status_5xx_de_respuesta(resultado: Any) -> int | None:
    """Devuelve el status si el handler produjo una respuesta 5xx, si no None."""
    status = None
    if isinstance(resultado, tuple) and len(resultado) >= 2:
        status = resultado[1]
    elif isinstance(resultado, Response):
        status = resultado.status_code

    if isinstance(status, str):
        primer_token = status.strip().split(' ', 1)[0]
        if primer_token.isdigit():
            status = int(primer_token)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    if 500 <= status <= 599:
        return status
    return None


def _headers_sin_cuerpo(headers: Any) -> Any:
    """Conserva headers necesarios y descarta los que describen el cuerpo viejo."""
    omitidos = {'content-type', 'content-length'}
    if isinstance(headers, (dict, Headers)):
        filtrados = {
            clave: valor for clave, valor in headers.items()
            if str(clave).lower() not in omitidos
        }
        return filtrados or None
    if isinstance(headers, (list, tuple)):
        filtrados = [
            (clave, valor) for clave, valor in headers
            if str(clave).lower() not in omitidos
        ]
        return filtrados or None
    return headers


def _sanitizar_respuesta_5xx(resultado: Any, status: int, error_id: str) -> Any:
    """Reemplaza cualquier cuerpo 5xx por un mensaje público correlacionable."""
    cuerpo = {"message": "Error interno del servidor", "error_id": error_id}

    if isinstance(resultado, tuple):
        headers = _headers_sin_cuerpo(resultado[2]) if len(resultado) >= 3 else None
        if headers:
            return cuerpo, status, headers
        return cuerpo, status

    respuesta = jsonify(cuerpo)
    respuesta.status_code = status
    for clave, valor in resultado.headers.items():
        if clave.lower() not in {'content-type', 'content-length'}:
            respuesta.headers[clave] = valor
    return respuesta


def _log_respuesta_5xx(func: Callable, status: int, error_id: str) -> None:
    """Registra solo metadatos de la respuesta; nunca el cuerpo ni la excepción."""
    if has_request_context():
        logger.error(
            "Respuesta 5xx sanitizada en %s [%s]: %s %s status=%s",
            getattr(func, '__name__', 'handler'),
            error_id,
            request.method,
            request.path,
            status,
        )
    else:
        logger.error(
            "Respuesta 5xx sanitizada en %s [%s]: status=%s",
            getattr(func, '__name__', 'handler'),
            error_id,
            status,
        )


def handle_db_errors(func: Callable) -> Callable:
    """
    Decorator para manejo centralizado de errores de base de datos y validación.

    Args:
        func (Callable): La función a decorar.

    Returns:
        Callable: La función decorada con manejo de errores.
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], int] | Any:
        try:
            # Capturar y sanitizar parámetros de ID para prevenir inyección
            for key, value in kwargs.items():
                if key.endswith('_id'):
                    try:
                        kwargs[key] = int(value)
                    except (ValueError, TypeError):
                        return {"message": f"ID inválido: {key}"}, 400

            resultado = func(*args, **kwargs)
            status_5xx = _status_5xx_de_respuesta(resultado)
            if status_5xx is not None:
                error_id = uuid.uuid4().hex[:8]
                try:
                    db.session.rollback()
                except Exception:
                    logger.error(
                        "No se pudo revertir la sesión al sanitizar [%s]",
                        error_id,
                    )
                _log_respuesta_5xx(func, status_5xx, error_id)
                return _sanitizar_respuesta_5xx(resultado, status_5xx, error_id)
            return resultado
        except ValidationError as e:
            logger.warning(f"Error de validación: {e.messages}")
            return {"message": "Datos inválidos", "errors": e.messages}, 400
        except werkzeug.exceptions.HTTPException as e:
            # Permitir que las excepciones HTTP se propaguen sin modificar
            raise e
        except Exception as e:
            db.session.rollback()
            error_id = uuid.uuid4().hex[:8]
            logger.error(f"Error en {func.__name__} [{error_id}]: {str(e)}", exc_info=True)

            # Si es un error de integridad de SQLAlchemy, podemos traducirlo a un 409 o 400
            from sqlalchemy.exc import IntegrityError
            if isinstance(e, IntegrityError):
                err_msg = str(e.orig).lower() if hasattr(e, 'orig') else ""
                if "unique" in err_msg or "duplicado" in err_msg:
                    return {"message": "El registro ya existe o entra en conflicto con un registro único.", "error_id": error_id}, 409
                if "foreign key" in err_msg or "violates foreign key constraint" in err_msg:
                    return {"message": "El registro hace referencia a una entidad no existente.", "error_id": error_id}, 400
                return {"message": "Violación de integridad de datos en el servidor.", "error_id": error_id}, 400

            return {"message": "Error interno del servidor", "error_id": error_id}, 500
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
        def wrapper(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], int] | Any:
            verify_jwt_in_request()

            usuario = obtener_usuario_actual()
            rol_usuario = usuario.rol

            if rol_usuario not in roles_permitidos:
                logger.warning(f"Acceso denegado: Usuario con rol '{rol_usuario}' intentó acceder a ruta restringida")
                return {
                    "error": "Acceso denegado",
                    "mensaje": "No tiene permisos suficientes para esta acción",
                    "required_roles": list(roles_permitidos),
                    "current_role": rol_usuario
                }, 403

            return fn(*args, **kwargs)
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
    def wrapper(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], int] | Any:
        verify_jwt_in_request()

        usuario = obtener_usuario_actual()

        if usuario.rol == 'admin':
            return fn(*args, **kwargs)

        # Verificar si está intentando acceder a datos de otro almacén
        almacen_id_request = kwargs.get('almacen_id')
        if almacen_id_request is not None:
            try:
                almacen_id_request = int(almacen_id_request)
            except (ValueError, TypeError):
                return ({
                    'message': 'ID de almacén inválido',
                    'error': 'parametro_invalido'
                }), 400

            usuario_almacen_id = usuario.almacen_id
            if usuario_almacen_id is None:
                return ({
                    'message': 'Usuario sin almacén asignado',
                    'error': 'almacen_no_asignado'
                }), 403

            if almacen_id_request != int(usuario_almacen_id):
                logger.warning(f"Intento de acceso a almacén no autorizado: Usuario {usuario.username} intentó acceder a almacén {almacen_id_request}")
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
                    if usuario.almacen_id is None or almacen_id_json != int(usuario.almacen_id):
                        logger.warning(f"Intento de modificación de almacén no autorizado: Usuario {usuario.username}")
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
    return wrapper

def obtener_payload_normalizado() -> tuple[dict[str, Any], Any]:
    """Extrae JSON o multipart/form-data y normaliza IDs numéricos.

    Devuelve ``(payload, archivo_comprobante)``. El comprobante solo existe en
    solicitudes multipart. Los IDs se convierten a int para que la autorización
    y los servicios trabajen con el mismo tipo sin importar el formato.
    """
    import json

    from services.authorization import normalizar_ids

    if request.content_type and 'multipart/form-data' in request.content_type:
        data = request.form.to_dict()
        archivo = request.files.get('comprobante')
        if isinstance(data.get('detalles'), str):
            try:
                data['detalles'] = json.loads(data['detalles'])
            except json.JSONDecodeError:
                raise werkzeug.exceptions.BadRequest("Formato de detalles inválido") from None
        return normalizar_ids(data), archivo

    data = request.get_json(silent=True)
    if data is None:
        data = {}
    return normalizar_ids(data), None


def validate_pagination_params() -> tuple[int, int]:
    """
    Extrae y valida parámetros de paginación de la request.

    Returns:
        Tuple[int, int]: Una tupla con (page, per_page).
    """
    from flask import has_request_context
    if not has_request_context():
        return 1, config.DEFAULT_ITEMS_PER_PAGE

    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        per_page = min(int(request.args.get('per_page', config.DEFAULT_ITEMS_PER_PAGE)), config.MAX_ITEMS_PER_PAGE)
    except (ValueError, TypeError):
        per_page = config.DEFAULT_ITEMS_PER_PAGE

    return page, per_page

def create_pagination_response(items: list[Any], pagination: Any) -> dict[str, Any]:
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

def paginar(query, schema=None):
    """
    Pagina una consulta SQLAlchemy y opcionalmente serializa con un schema Marshmallow.
    """
    page, per_page = validate_pagination_params()
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    if schema:
        items = schema.dump(pagination.items, many=True)
    else:
        items = pagination.items

    return create_pagination_response(items, pagination)

def obtener_saldos_pendientes_clientes(cliente_ids: list[int] | None = None) -> dict[int, Decimal]:
    """
    Calcula el saldo pendiente total por cliente utilizando 1 sola consulta SQL agregada.
    Evita el problema N+1 de la propiedad Python.
    """
    from decimal import Decimal

    from sqlalchemy import func, select

    from models import Pago, Venta

    sub_pagos = (
        select(Pago.venta_id, func.coalesce(func.sum(Pago.monto), 0).label('total_pagado'))
        .group_by(Pago.venta_id)
        .subquery()
    )

    query = (
        db.session.query(
            Venta.cliente_id,
            func.coalesce(func.sum(Venta.total - func.coalesce(sub_pagos.c.total_pagado, 0)), 0).label('saldo_total')
        )
        .outerjoin(sub_pagos, sub_pagos.c.venta_id == Venta.id)
    )

    if cliente_ids:
        query = query.filter(Venta.cliente_id.in_(cliente_ids))

    query = query.group_by(Venta.cliente_id)
    return {row[0]: Decimal(str(row[1])) for row in query.all()}

def validate_password(password: str) -> tuple[bool, str | None]:
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
