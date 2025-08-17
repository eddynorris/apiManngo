# ARCHIVO: cliente_resource.py
from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required
from flask import request, send_file
from models import Cliente, Venta
from schemas import cliente_schema, clientes_schema, ClienteSchema
from extensions import db
from common import handle_db_errors, validate_pagination_params, create_pagination_response, rol_requerido
import pandas as pd
import re
import io
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


class ClienteExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Exporta todos los clientes a un archivo Excel, opcionalmente filtrado por ciudad.
        """
        parser = reqparse.RequestParser()
        parser.add_argument('ciudad', type=str, location='args', help='Filtra clientes por ciudad')
        args = parser.parse_args()
        ciudad = args.get('ciudad')

        try:
            # 1. Obtener clientes, aplicando filtro si se proporciona
            if ciudad:
                clientes = Cliente.query.filter_by(ciudad=ciudad).all()
            else:
                clientes = Cliente.query.all()
            if not clientes:
                return {"message": "No hay clientes para exportar"}, 404

            # 2. Serializar los datos con el esquema
            cliente_schema = ClienteSchema(many=True)
            data = cliente_schema.dump(clientes)

            # 3. Crear un DataFrame de pandas
            df = pd.DataFrame(data)

            # 4. Optimizar el DataFrame para el reporte
            columnas_deseadas = {
                'id': 'ID',
                'nombre': 'Nombre',
                'telefono': 'Teléfono',
                'direccion': 'Dirección',
                'ciudad': 'Ciudad',
                'saldo_pendiente': 'Saldo Pendiente',
                'ultima_fecha_compra': 'Última Compra',
                'frecuencia_compra_dias': 'Frecuencia de Compra'
            }
            
            # Filtrar y renombrar columnas
            df_optimizado = df[list(columnas_deseadas.keys())]
            df_optimizado = df_optimizado.rename(columns=columnas_deseadas)


            # 5. Crear un archivo Excel en memoria
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_optimizado.to_excel(writer, index=False, sheet_name='Clientes')
            
            output.seek(0)

            # 5. Enviar el archivo como respuesta
            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name='clientes.xlsx'
            )

        except Exception as e:
            logger.error(f"Error al exportar clientes: {str(e)}")
            return {"error": "Error interno al generar el archivo Excel"}, 500