# Archivo: resources/user_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request, current_app
from models import Users, Almacen
from schemas import user_schema, users_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido, validate_pagination_params, create_pagination_response
from werkzeug.security import generate_password_hash
import re
import logging

# Configurar logging
logger = logging.getLogger(__name__)

class UserResource(Resource):
    @jwt_required()
    @rol_requerido('admin')  # Solo admin puede listar usuarios
    @handle_db_errors
    def get(self, user_id=None):
        """
        Obtiene usuario(s)
        - Con ID: Detalles de un usuario específico
        - Sin ID: Lista paginada con filtros (rol, almacén)
        """
        try:
            # Si se solicita un usuario específico
            if user_id:
                usuario = Users.query.get_or_404(user_id)
                return user_schema.dump(usuario), 200
            
            # Construir query con filtros
            query = Users.query
            
            # Filtrar por rol si se especifica
            if rol := request.args.get('rol'):
                # Validar que sea un rol válido
                roles_validos = ['admin', 'gerente', 'usuario']
                if rol not in roles_validos:
                    return {"error": f"Rol inválido. Debe ser uno de: {', '.join(roles_validos)}"}, 400
                    
                query = query.filter(Users.rol == rol)
            
            # Filtrar por almacén si se especifica
            if almacen_id := request.args.get('almacen_id'):
                try:
                    query = query.filter(Users.almacen_id == int(almacen_id))
                except ValueError:
                    return {"error": "ID de almacén inválido"}, 400
            
            # Búsqueda por username
            if username := request.args.get('username'):
                query = query.filter(Users.username.ilike(f'%{username}%'))
            
            # Paginación con validación
            page, per_page = validate_pagination_params()
            usuarios = query.paginate(page=page, per_page=per_page)
            
            # Respuesta estandarizada
            return create_pagination_response(users_schema.dump(usuarios.items), usuarios), 200
            
        except Exception as e:
            logger.error(f"Error al obtener usuarios: {str(e)}")
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @rol_requerido('admin')  # Solo admin puede crear usuarios
    @handle_db_errors
    def post(self):
        """Crea un nuevo usuario con validación completa"""
        try:
            # Validar formato de entrada
            if not request.is_json:
                return {"error": "Se esperaba contenido JSON"}, 400
                
            data = request.get_json()
            if not data:
                return {"error": "Datos JSON vacíos o inválidos"}, 400
            
            # Validaciones preliminares
            if not data.get('username') or len(data.get('username', '')) < 3:
                return {"error": "El nombre de usuario debe tener al menos 3 caracteres"}, 400
                
            if not data.get('password') or len(data.get('password', '')) < 8:
                return {"error": "La contraseña debe tener al menos 8 caracteres"}, 400
            
            # Validar complejidad de contraseña
            password = data.get('password', '')
            if not (re.search(r'[A-Z]', password) and re.search(r'[0-9]', password)):
                return {"error": "La contraseña debe contener al menos una mayúscula y un número"}, 400
            
            # Verificar que el username no exista
            username = data.get('username').strip()
            if Users.query.filter_by(username=username).first():
                return {"error": "El nombre de usuario ya existe"}, 400
            
            # Validar rol
            rol = data.get('rol', 'usuario').strip()
            roles_validos = ['admin', 'gerente', 'usuario']
            if rol not in roles_validos:
                return {"error": f"Rol inválido. Debe ser uno de: {', '.join(roles_validos)}"}, 400
            
            # Validar almacén si se proporciona
            if almacen_id := data.get('almacen_id'):
                try:
                    almacen_id = int(almacen_id)
                    almacen = Almacen.query.get(almacen_id)
                    if not almacen:
                        return {"error": "El almacén especificado no existe"}, 400
                except (ValueError, TypeError):
                    return {"error": "ID de almacén inválido"}, 400
            
            # Hashear la contraseña de forma segura
            data['password'] = generate_password_hash(password, method='pbkdf2:sha256:150000')
            
            # Crear usuario
            nuevo_usuario = user_schema.load(data)
            db.session.add(nuevo_usuario)
            db.session.commit()
            
            logger.info(f"Usuario creado: {username}, rol: {rol}")
            
            # Devolver respuesta sin la contraseña
            result = user_schema.dump(nuevo_usuario)
            result.pop('password', None)  # Remover password del resultado
            
            return result, 201
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al crear usuario: {str(e)}")
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @rol_requerido('admin')  # Solo admin puede actualizar usuarios
    @handle_db_errors
    def put(self, user_id):
        """Actualiza un usuario existente con validaciones"""
        try:
            if not user_id:
                return {"error": "Se requiere ID de usuario"}, 400
                
            usuario = Users.query.get_or_404(user_id)
            
            # Validar formato de entrada
            if not request.is_json:
                return {"error": "Se esperaba contenido JSON"}, 400
                
            data = request.get_json()
            if not data:
                return {"error": "Datos JSON vacíos o inválidos"}, 400
            
            # Si se cambia el username, verificar que no exista
            if 'username' in data and data['username'] != usuario.username:
                username = data['username'].strip()
                if len(username) < 3:
                    return {"error": "El nombre de usuario debe tener al menos 3 caracteres"}, 400
                    
                if Users.query.filter_by(username=username).first():
                    return {"error": "El nombre de usuario ya existe"}, 400
            
            # Si se cambia la contraseña, verificar complejidad
            if 'password' in data:
                password = data['password']
                if len(password) < 8:
                    return {"error": "La contraseña debe tener al menos 8 caracteres"}, 400
                    
                if not (re.search(r'[A-Z]', password) and re.search(r'[0-9]', password)):
                    return {"error": "La contraseña debe contener al menos una mayúscula y un número"}, 400
                    
                # Hashear la contraseña
                data['password'] = generate_password_hash(password, method='pbkdf2:sha256:150000')
            
            # Validar rol si se proporciona
            if 'rol' in data:
                rol = data['rol'].strip()
                roles_validos = ['admin', 'gerente', 'usuario']
                if rol not in roles_validos:
                    return {"error": f"Rol inválido. Debe ser uno de: {', '.join(roles_validos)}"}, 400
            
            # Validar almacén si se proporciona
            if 'almacen_id' in data:
                try:
                    if data['almacen_id'] is not None:
                        almacen_id = int(data['almacen_id'])
                        almacen = Almacen.query.get(almacen_id)
                        if not almacen:
                            return {"error": "El almacén especificado no existe"}, 400
                except (ValueError, TypeError):
                    return {"error": "ID de almacén inválido"}, 400
            
            # Actualizar usuario
            updated_usuario = user_schema.load(data, instance=usuario, partial=True)
            db.session.commit()
            
            logger.info(f"Usuario actualizado: {usuario.id} - {usuario.username}")
            
            # Devolver respuesta sin la contraseña
            result = user_schema.dump(updated_usuario)
            result.pop('password', None)  # Remover password del resultado
            
            return result, 200
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al actualizar usuario: {str(e)}")
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @rol_requerido('admin')  # Solo admin puede eliminar usuarios
    @handle_db_errors
    def delete(self, user_id):
        """Elimina un usuario con validaciones de seguridad"""
        try:
            if not user_id:
                return {"error": "Se requiere ID de usuario"}, 400
                
            usuario = Users.query.get_or_404(user_id)
            
            # No permitir eliminar al usuario que hace la petición
            claims = get_jwt()
            if str(usuario.id) == claims.get('sub'):
                return {"error": "No puedes eliminar tu propio usuario"}, 400
            
            # No permitir eliminar el último usuario admin
            if usuario.rol == 'admin':
                admin_count = Users.query.filter_by(rol='admin').count()
                if admin_count <= 1:
                    return {"error": "No se puede eliminar el último usuario administrador"}, 400
            
            # Verificar si tiene movimientos u otras dependencias
            # Se puede implementar verificación de dependencias adicionales aquí
            
            username = usuario.username  # Guardar para el log
            db.session.delete(usuario)
            db.session.commit()
            
            logger.info(f"Usuario eliminado: {user_id} - {username}")
            return {"message": "Usuario eliminado correctamente"}, 200
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al eliminar usuario: {str(e)}")
            return {"error": "Error al procesar la solicitud"}, 500