from flask_restful import Resource, reqparse
from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash, generate_password_hash
from models import Users, Almacen
from extensions import db
from flask import request, jsonify, abort, current_app
from datetime import datetime, timezone, timedelta
import re
import logging

# Configurar logging
logger = logging.getLogger(__name__)

class AuthResource(Resource):
    def post(self):
        try:
            parser = reqparse.RequestParser()
            parser.add_argument('username', type=str, required=True, help='El nombre de usuario es requerido')
            parser.add_argument('password', type=str, required=True, help='La contraseña es requerida')
            
            data = parser.parse_args()
            
            # Sanitizar entradas
            username = data['username'].strip()
            password = data['password']
            
            # Validaciones básicas para prevenir ataques simples
            if not username or len(username) < 3:
                return {'message': 'El nombre de usuario debe tener al menos 3 caracteres'}, 400
                
            if not password or len(password) < 8:
                return {'message': 'La contraseña debe tener al menos 8 caracteres'}, 400
            
            # Find user by username (case insensitive)
            usuario = Users.query.filter(Users.username.ilike(username)).first()
            
            # Verificación real de credenciales (insensible a mayúsculas/minúsculas)
            if not usuario or not check_password_hash(usuario.password, password.lower()):
                # Log de intento fallido (sin exponer qué campo falló)
                logger.warning(f"Intento de login fallido para el usuario: {username}")
                return {'message': 'Credenciales inválidas'}, 401
            
            # Determinar expiración del token basado en el rol
            if usuario.rol == 'admin':
                expires = timedelta(hours=12)  # Los admins tienen más tiempo
            else:
                expires = timedelta(hours=8)   # Usuarios normales, menos tiempo
                
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
            logger.error(f"Error en login: {str(e)}")
            return {'message': 'Error en el servidor'}, 500
