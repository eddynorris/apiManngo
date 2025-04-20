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
                
            if not password or len(password) < 6:
                return {'message': 'La contraseña debe tener al menos 6 caracteres'}, 400
            
            # Find user by username
            usuario = Users.query.filter_by(username=username).first()
            
            # Verificación real de credenciales
            if not usuario or not check_password_hash(usuario.password, password):
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


class RegisterResource(Resource):
    def post(self):
        try:
            parser = reqparse.RequestParser()
            parser.add_argument('username', type=str, required=True, help='El nombre de usuario es requerido')
            parser.add_argument('password', type=str, required=True, help='La contraseña es requerida')
            parser.add_argument('rol', type=str, required=False, default='usuario', help='El rol del usuario')
            parser.add_argument('almacen_id', type=int, required=False, help='ID del almacén asignado')
            
            data = parser.parse_args()
            
            # Sanitizar y validar datos
            username = data['username'].strip()
            password = data['password']
            rol = data['rol'].strip()
            
            # Validaciones de seguridad
            if not username or len(username) < 3:
                return {'message': 'El nombre de usuario debe tener al menos 3 caracteres'}, 400
                
            if not password or len(password) < 8:
                return {'message': 'La contraseña debe tener al menos 8 caracteres'}, 400
                
            # Verificar complejidad de contraseña
            if not re.search(r'[A-Z]', password) or not re.search(r'[0-9]', password):
                return {'message': 'La contraseña debe contener al menos una mayúscula y un número'}, 400
            
            # Validar unicidad del usuario
            if Users.query.filter_by(username=username).first():
                return {'message': 'El nombre de usuario ya está en uso'}, 400
            
            # Validar el rol
            roles_validos = ['admin', 'gerente', 'usuario']
            if rol not in roles_validos:
                return {'message': f'Rol inválido. Debe ser uno de: {", ".join(roles_validos)}'}, 400
            
            # Validar el almacén si se proporciona
            if data['almacen_id']:
                almacen = Almacen.query.get(data['almacen_id'])
                if not almacen:
                    return {'message': 'El almacén especificado no existe'}, 400
            
            # Crear hash seguro de la contraseña
            password_hash = generate_password_hash(password, method='pbkdf2:sha256:150000')
            
            # Crear nuevo usuario
            nuevo_usuario = Users(
                username=username,
                password=password_hash,
                rol=rol,
                almacen_id=data['almacen_id']
            )
            
            try:
                db.session.add(nuevo_usuario)
                db.session.commit()
                
                # Log de creación exitosa
                logger.info(f"Usuario creado: {username} con rol {rol}")
                
                return {
                    'message': 'Usuario registrado exitosamente', 
                    'id': nuevo_usuario.id,
                    'username': nuevo_usuario.username,
                    'rol': nuevo_usuario.rol
                }, 201
                
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error al crear usuario: {str(e)}")
                return {'message': 'Error al registrar el usuario', 'error': str(e)}, 500
                
        except Exception as e:
            logger.error(f"Error en registro: {str(e)}")
            return {'message': 'Error en el servidor'}, 500