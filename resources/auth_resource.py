from typing import Dict, Tuple, Union, Any
from flask_restful import Resource, reqparse
from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash
from models import Users, Almacen
from flask import request
from common import validate_password
from datetime import timedelta
import logging
import config

# Configurar logging
logger = logging.getLogger(__name__)

class AuthResource(Resource):
    def post(self) -> Tuple[Dict[str, Any], int]:
        try:
            parser = reqparse.RequestParser()
            parser.add_argument('username', type=str, required=True, help='El nombre de usuario es requerido')
            parser.add_argument('password', type=str, required=True, help='La contraseña es requerida')
            
            data = parser.parse_args()
            
            # Sanitizar entradas
            username = data['username'].strip()
            password = data['password']
            
            # Validaciones básicas para prevenir ataques simples
            if not username or len(username) < config.MIN_USERNAME_LENGTH:
                return {'message': f'El nombre de usuario debe tener al menos {config.MIN_USERNAME_LENGTH} caracteres'}, 400
                
            # Validaciones de contraseña
            is_valid, error_msg = validate_password(password)
            if not is_valid:
                return {'message': error_msg}, 400
            
            # Find user by username (case insensitive)
            usuario = Users.query.filter(Users.username.ilike(username)).first()
            
            # Verificación real de credenciales
            if not usuario or not check_password_hash(usuario.password, password):
                # Log de intento fallido (sin exponer qué campo falló)
                logger.warning(f"Intento de login fallido para el usuario: {username}")
                return {'message': 'Credenciales inválidas'}, 401
            
            # Determinar expiración del token basado en el rol
            if usuario.rol == 'admin':
                expires = timedelta(hours=config.JWT_EXPIRES_HOURS_ADMIN)
            else:
                expires = timedelta(hours=config.JWT_EXPIRES_HOURS_USER)
                
            # Crear token con datos mínimos necesarios
            access_token = create_access_token(
                identity=str(usuario.id),
                additional_claims={
                    'username': usuario.username,
                    'rol': usuario.rol,
                    'almacen_id': usuario.almacen_id
                },
                expires_delta=expires
            )
            
            # Obtener nombre del almacén si existe
            nombre_almacen = None
            if usuario.almacen_id:
                almacen = Almacen.query.get(usuario.almacen_id)
                if almacen:
                    nombre_almacen = almacen.nombre
            
            # Log de login exitoso
            logger.info(f"Login exitoso para usuario: {username}")
            
            return {
                'access_token': access_token,
                'token_type': 'Bearer',
                'expires_in': int(expires.total_seconds()),
                'user': {
                    'id': usuario.id,
                    'username': usuario.username,
                    'rol': usuario.rol,
                    'almacen_id': usuario.almacen_id,
                    'almacen_nombre': nombre_almacen
                }
            }, 200
            
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Error en login: {str(e)}", exc_info=True)
            return {'message': 'Error en el servidor'}, 500
