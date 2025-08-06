# ARCHIVO: cliente_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Cliente, Venta
from schemas import cliente_schema, clientes_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, validate_pagination_params, create_pagination_response, rol_requerido
import re
import logging

# Configurar logging
logger = logging.getLogger(__name__)

class ClienteResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, cliente_id=None):
        """
        Obtiene cliente(s)
        - Con ID: Detalle completo con saldo pendiente
        - Sin ID: Lista paginada con filtros (nombre, teléfono)
        """
        try:
            # Si se solicita un cliente específico
            if cliente_id:
                cliente = Cliente.query.get_or_404(cliente_id)
                return cliente_schema.dump(cliente), 200
            
            # Construir query con filtros
            query = Cliente.query
            
            # Aplicar filtros para búsqueda por nombre o término de búsqueda genérico
            search_term = request.args.get('nombre') or request.args.get('search')
            if search_term:
                # Usar ilike para búsqueda case-insensitive. SQLAlchemy previene inyección SQL.
                query = query.filter(Cliente.nombre.ilike(f'%{search_term}%'))
                
            if telefono := request.args.get('telefono'):
                # Validar formato básico de teléfono
                if not re.match(r'^[\d\+\-\s()]+$', telefono):
                    return {"error": "Formato de teléfono inválido"}, 400
                query = query.filter(Cliente.telefono == telefono)

            # Nuevo filtro por ciudad
            if ciudad := request.args.get('ciudad'):
                # Sanitizar input
                ciudad = re.sub(r'[^\w\s\-áéíóúÁÉÍÓÚñÑ]', '', ciudad)
                query = query.filter(Cliente.ciudad.ilike(f'%{ciudad}%'))
    
            # Paginación con validación
            page, per_page = validate_pagination_params()
            resultado = query.paginate(page=page, per_page=per_page, error_out=False)
            
            # Respuesta estandarizada
            return create_pagination_response(clientes_schema.dump(resultado.items), resultado), 200
            
        except Exception as e:
            logger.error(f"Error al obtener clientes: {str(e)}")
            db.session.rollback()
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @rol_requerido('admin', 'gerente', 'usuario')
    @handle_db_errors
    def post(self):
        """Crea nuevo cliente con validación de datos"""
        try:
            # Validar que sea JSON
            if not request.is_json:
                return {"error": "Se esperaba contenido JSON"}, 400
                
            data = request.get_json()
            if not data:
                return {"error": "Datos JSON vacíos o inválidos"}, 400
            
            # Validar campos requeridos
            if not data.get('nombre'):
                return {"error": "El nombre del cliente es obligatorio"}, 400
            
            # Validar teléfono si está presente
            if telefono := data.get('telefono'):
                if not re.match(r'^[\d\+\-\s()]{3,20}$', telefono):
                    return {"error": "Formato de teléfono inválido"}, 400
            
            # Crear y guardar cliente
            nuevo_cliente = cliente_schema.load(data)
            db.session.add(nuevo_cliente)
            db.session.commit()
            
            logger.info(f"Cliente creado: {nuevo_cliente.nombre}")
            return cliente_schema.dump(nuevo_cliente), 201
            
        except Exception as e:
            logger.error(f"Error al crear cliente: {str(e)}")
            db.session.rollback()
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @rol_requerido('admin', 'gerente', 'usuario')
    @handle_db_errors
    def put(self, cliente_id):
        """Actualiza cliente existente con validación parcial"""
        try:
            if not cliente_id:
                return {"error": "Se requiere ID de cliente"}, 400
                
            cliente = Cliente.query.get_or_404(cliente_id)
            
            # Validar que sea JSON
            if not request.is_json:
                return {"error": "Se esperaba contenido JSON"}, 400
                
            data = request.get_json()
            if not data:
                return {"error": "Datos JSON vacíos o inválidos"}, 400
            
            # Validar teléfono si está presente
            if telefono := data.get('telefono'):
                if not re.match(r'^[\d\+\-\s()]{3,20}$', telefono):
                    return {"error": "Formato de teléfono inválido"}, 400
            
            # Actualizar cliente
            cliente_actualizado = cliente_schema.load(
                data,
                instance=cliente,
                partial=True
            )
            
            db.session.commit()
            logger.info(f"Cliente actualizado: {cliente.id} - {cliente.nombre}")
            return cliente_schema.dump(cliente_actualizado), 200
            
        except Exception as e:
            logger.error(f"Error al actualizar cliente: {str(e)}")
            db.session.rollback()
            return {"error": "Error al procesar la solicitud"}, 500

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def delete(self, cliente_id):
        """Elimina cliente solo si no tiene ventas asociadas"""
        try:
            if not cliente_id:
                return {"error": "Se requiere ID de cliente"}, 400
                
            cliente = Cliente.query.get_or_404(cliente_id)
            
            # Verificar si tiene ventas asociadas
            ventas = Venta.query.filter_by(cliente_id=cliente_id).count()
            if ventas > 0:
                return {
                    "error": "No se puede eliminar cliente con historial de ventas",
                    "ventas_asociadas": ventas
                }, 400
                
            # Eliminar cliente
            nombre_cliente = cliente.nombre  # Guardar para el log
            db.session.delete(cliente)
            db.session.commit()
            
            logger.info(f"Cliente eliminado: {cliente_id} - {nombre_cliente}")
            return {"message": "Cliente eliminado exitosamente"}, 200
            
        except Exception as e:
            logger.error(f"Error al eliminar cliente: {str(e)}")
            db.session.rollback()
            return {"error": "Error al procesar la solicitud"}, 500