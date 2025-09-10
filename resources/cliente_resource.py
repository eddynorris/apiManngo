# ARCHIVO: cliente_resource.py
from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required
from flask import request, send_file
from models import Cliente, Pedido, Venta
from schemas import cliente_schema, clientes_schema, ClienteSchema, pedidos_schema
from extensions import db
from common import handle_db_errors, validate_pagination_params, create_pagination_response, rol_requerido
import pandas as pd
import re
import io
import logging
import calendar
from sqlalchemy import func, desc, asc, cast, Date, case
from sqlalchemy.orm import aliased
from sqlalchemy import orm
from datetime import datetime, timezone, timedelta, date

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

class ClienteProyeccionResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, cliente_id=None):
        if cliente_id:
            return self._get_detalle_cliente(cliente_id)
        else:
            return self._get_lista_proyecciones()

    def _get_detalle_cliente(self, cliente_id):
        """
        OPTIMIZADO PARA DETALLE: Obtiene un solo cliente y carga su historial
        completo de ventas y pedidos de forma eficiente.
        """
        try:
            # --- MEJORA PARA DETALLE: Carga ansiosa de las colecciones ---
            cliente = Cliente.query.options(
                orm.selectinload(Cliente.ventas),
                orm.selectinload(Cliente.pedidos)
            ).get_or_404(cliente_id)

            # Construir la respuesta completa
            cliente_data = cliente_schema.dump(cliente)
            
            # Los datos ya están cargados, no hay nuevas consultas
            ventas = cliente.ventas
            pedidos = cliente.pedidos
            
            cliente_data['pedidos'] = pedidos_schema.dump(pedidos)
            cliente_data['ventas'] = [{
                'id': v.id, 'fecha': v.fecha.isoformat(), 'total': float(v.total), 'estado_pago': v.estado_pago
            } for v in sorted(ventas, key=lambda x: x.fecha, reverse=True)] # Ordenar en python
            
            cliente_data['proxima_compra_estimada'] = self._calcular_proyeccion_compra(cliente)
            cliente_data['estadisticas'] = self._calcular_estadisticas_cliente(cliente, ventas, pedidos)

            return cliente_data, 200

        except Exception as e:
            logger.error(f"Error al obtener detalle del cliente {cliente_id}: {str(e)}")
            return {"error": "Error al procesar la solicitud de detalle"}, 500

    def _get_lista_proyecciones(self):
        """
        OPTIMIZADO PARA LISTA: Usa subconsultas para obtener solo las estadísticas
        agregadas, sin traer el historial de ventas/pedidos.
        """
        try:
            # --- MEJORA PARA LISTA: Subconsulta para agregar estadísticas de ventas ---
            venta_stats = db.session.query(
                Venta.cliente_id.label('cliente_id'),
                func.count(Venta.id).label('total_ventas'),
                func.sum(Venta.total).label('monto_total_comprado')
            ).group_by(Venta.cliente_id).subquery()
            
            # --- Subconsulta para agregar estadísticas de pedidos ---
            pedido_stats = db.session.query(
                Pedido.cliente_id.label('cliente_id'),
                func.count(Pedido.id).label('total_pedidos')
            ).group_by(Pedido.cliente_id).subquery()

            # --- Construir la consulta principal ---
            query = db.session.query(
                Cliente,
                # Usar coalesce para mostrar 0 si no hay ventas/pedidos
                func.coalesce(venta_stats.c.total_ventas, 0).label('total_ventas'),
                func.coalesce(venta_stats.c.monto_total_comprado, 0).label('monto_total_comprado'),
                func.coalesce(pedido_stats.c.total_pedidos, 0).label('total_pedidos')
            ).outerjoin(
                venta_stats, Cliente.id == venta_stats.c.cliente_id
            ).outerjoin(
                pedido_stats, Cliente.id == pedido_stats.c.cliente_id
            )
            
            # (El resto de tu lógica de filtrado se aplica a esta consulta principal)
            query = query.filter(Cliente.frecuencia_compra_dias.isnot(None))
            # ... otros filtros

            # Paginación
            page, per_page = validate_pagination_params()
            paginated_results = query.paginate(page=page, per_page=per_page, error_out=False)

            # --- Construir la respuesta final ---
            clientes_con_proyeccion = []
            for result in paginated_results.items:
                cliente = result.Cliente
                cliente_data = cliente_schema.dump(cliente)
                
                # Calcular la proyección (esto es rápido, no necesita consulta)
                cliente_data['proxima_compra_estimada'] = self._calcular_proyeccion_compra(cliente)

                # Agregar las estadísticas ya calculadas por la base de datos
                monto_total = float(result.monto_total_comprado)
                total_ventas = result.total_ventas
                
                cliente_data['estadisticas'] = {
                    'total_ventas': total_ventas,
                    'monto_total_comprado': monto_total,
                    'total_pedidos': result.total_pedidos,
                    'saldo_pendiente': float(cliente.saldo_pendiente),
                    'promedio_compra': monto_total / total_ventas if total_ventas > 0 else 0,
                    'ultima_actividad': cliente.ultima_fecha_compra.isoformat() if cliente.ultima_fecha_compra else None
                }
                clientes_con_proyeccion.append(cliente_data)

            return {
                "data": clientes_con_proyeccion,
                "pagination": {
                    "total": paginated_results.total, "page": paginated_results.page,
                    "per_page": paginated_results.per_page, "pages": paginated_results.pages
                }
            }, 200

        except Exception as e:
            logger.error(f"Error al obtener lista de proyecciones: {str(e)}")
            return {"error": "Error al procesar la lista de proyecciones"}, 500

    # Estos helpers ya no necesitan hacer consultas, solo cálculos simples
    def _calcular_proyeccion_compra(self, cliente):
        # ... (se mantiene igual)
        if cliente.ultima_fecha_compra and cliente.frecuencia_compra_dias and cliente.frecuencia_compra_dias > 0:
            return (cliente.ultima_fecha_compra + timedelta(days=cliente.frecuencia_compra_dias)).isoformat()
        return None
    
    def _calcular_estadisticas_cliente(self, cliente, ventas, pedidos):
        """
        Calcula estadísticas adicionales del cliente
        """
        estadisticas = {
            'total_ventas': len(ventas),
            'total_pedidos': len(pedidos),
            'monto_total_comprado': 0,
            'saldo_pendiente': float(cliente.saldo_pendiente),
            'promedio_compra': 0,
            'pedidos_por_estado': {},
            'ultima_actividad': None
        }
        
        # Calcular montos
        if ventas:
            estadisticas['monto_total_comprado'] = float(sum(venta.total for venta in ventas))
            estadisticas['promedio_compra'] = estadisticas['monto_total_comprado'] / len(ventas)
            estadisticas['ultima_actividad'] = ventas[0].fecha.isoformat()
        
        # Contar pedidos por estado
        for pedido in pedidos:
            estado = pedido.estado
            estadisticas['pedidos_por_estado'][estado] = estadisticas['pedidos_por_estado'].get(estado, 0) + 1
        
        # Si no hay ventas pero hay pedidos, usar fecha del último pedido
        if not estadisticas['ultima_actividad'] and pedidos:
            estadisticas['ultima_actividad'] = pedidos[0].fecha_creacion.isoformat()
        
        return estadisticas

class ClienteProyeccionExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Exporta clientes con proyecciones a un archivo Excel de forma optimizada.
        """
        parser = reqparse.RequestParser()
        parser.add_argument('ciudad', type=str, location='args')
        parser.add_argument('saldo_minimo', type=float, location='args')
        parser.add_argument('frecuencia_minima', type=int, location='args')
        args = parser.parse_args()

        try:
            # --- Subconsulta para agregar estadísticas de ventas ---
            venta_stats = db.session.query(
                Venta.cliente_id.label('cliente_id'),
                func.count(Venta.id).label('total_ventas'),
                func.sum(Venta.total).label('monto_total_comprado')
            ).group_by(Venta.cliente_id).subquery()
            
            # --- Subconsulta para agregar estadísticas de pedidos ---
            pedido_stats = db.session.query(
                Pedido.cliente_id.label('cliente_id'),
                func.count(Pedido.id).label('total_pedidos')
            ).group_by(Pedido.cliente_id).subquery()

            # --- Construir la consulta principal ---
            query = db.session.query(
                Cliente,
                func.coalesce(venta_stats.c.total_ventas, 0).label('total_ventas'),
                func.coalesce(venta_stats.c.monto_total_comprado, 0).label('monto_total_comprado'),
                func.coalesce(pedido_stats.c.total_pedidos, 0).label('total_pedidos')
            ).outerjoin(
                venta_stats, Cliente.id == venta_stats.c.cliente_id
            ).outerjoin(
                pedido_stats, Cliente.id == pedido_stats.c.cliente_id
            )
            
            # Aplicar filtros
            if args['ciudad']:
                query = query.filter(Cliente.ciudad.ilike(f"%{args['ciudad']}%"))
            if args['saldo_minimo']:
                query = query.filter(Cliente.saldo_pendiente >= args['saldo_minimo'])
            if args['frecuencia_minima']:
                query = query.filter(Cliente.frecuencia_compra_dias >= args['frecuencia_minima'])
            
            # Solo clientes con frecuencia de compra calculada
            query = query.filter(Cliente.frecuencia_compra_dias.isnot(None))
            
            resultados = query.order_by(desc(Cliente.ultima_fecha_compra)).all()
            
            if not resultados:
                return {"message": "No hay clientes con proyecciones para exportar con los filtros seleccionados"}, 404

            # --- Construir los datos para el Excel ---
            data_para_excel = []
            for result in resultados:
                cliente = result.Cliente
                monto_total = float(result.monto_total_comprado)
                total_ventas = result.total_ventas
                
                # Calcular proyección de próxima compra
                proxima_compra = None
                if cliente.ultima_fecha_compra and cliente.frecuencia_compra_dias and cliente.frecuencia_compra_dias > 0:
                    proxima_compra = (cliente.ultima_fecha_compra + timedelta(days=cliente.frecuencia_compra_dias)).strftime('%Y-%m-%d')
                
                data_para_excel.append({
                    'ID': cliente.id,
                    'Nombre': cliente.nombre,
                    'Teléfono': cliente.telefono or 'N/A',
                    'Dirección': cliente.direccion or 'N/A',
                    'Ciudad': cliente.ciudad or 'N/A',
                    'Saldo Pendiente': float(cliente.saldo_pendiente),
                    'Última Compra': cliente.ultima_fecha_compra.strftime('%Y-%m-%d') if cliente.ultima_fecha_compra else 'N/A',
                    'Frecuencia Compra (días)': cliente.frecuencia_compra_dias or 0,
                    'Próxima Compra Estimada': proxima_compra or 'N/A',
                    'Total Ventas': total_ventas,
                    'Monto Total Comprado': monto_total,
                    'Promedio por Compra': monto_total / total_ventas if total_ventas > 0 else 0,
                    'Total Pedidos': result.total_pedidos
                })

            df = pd.DataFrame(data_para_excel)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Clientes Proyecciones')
            
            output.seek(0)

            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'clientes_proyecciones_{datetime.now().strftime("%Y%m%d")}.xlsx'
            )

        except Exception as e:
            logger.error(f"Error al exportar clientes con proyecciones: {str(e)}")
            return {"error": "Error interno al generar el archivo Excel"}, 500