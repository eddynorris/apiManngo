# ARCHIVO: cliente_proyeccion_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from flask import request
from models import Cliente, Pedido, Venta, PedidoDetalle, VentaDetalle
from schemas import cliente_schema, clientes_schema, pedido_schema, pedidos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, validate_pagination_params, create_pagination_response
from datetime import datetime, timezone, timedelta, date
from sqlalchemy import func, desc, asc, cast, Date
import logging
import calendar

# Configurar logging
logger = logging.getLogger(__name__)

class ClienteProyeccionResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, cliente_id=None):
        """
        Obtiene proyecciones de clientes con pedidos y fechas estimadas de próxima compra
        - Con cliente_id: Detalle específico de un cliente
        - Sin cliente_id: Lista paginada de todos los clientes con proyecciones
        """
        try:
            if cliente_id:
                # Obtener cliente específico
                cliente = Cliente.query.get_or_404(cliente_id)
                return self._get_cliente_projection(cliente), 200
            
            # Construir query base
            query = Cliente.query

            # Filtrar clientes con frecuencia_compra_dias definida
            query = query.filter(Cliente.frecuencia_compra_dias.isnot(None))
            
            # Filtro por fecha de proyeccion
            filtro_fecha = request.args.get('filtro_fecha')
            if filtro_fecha:
                # Expresión para calcular la próxima fecha de compra en la base de datos
                # Nota: make_interval es específico de PostgreSQL, que es el motor de Supabase.
                # Se usan argumentos posicionales para make_interval: years, months, weeks, days, hours, mins, secs
                proxima_compra_expr = Cliente.ultima_fecha_compra + func.make_interval(0, 0, 0, Cliente.frecuencia_compra_dias)
                
                today = datetime.now(timezone.utc).date()

                if filtro_fecha == 'manana':
                    tomorrow = today + timedelta(days=1)
                    # Comparamos solo la parte de la fecha
                    query = query.filter(cast(proxima_compra_expr, Date) == tomorrow)
                
                elif filtro_fecha == 'semana':
                    # Desde hoy hasta los próximos 7 días
                    end_of_week = today + timedelta(days=7)
                    query = query.filter(cast(proxima_compra_expr, Date).between(today, end_of_week))

                elif filtro_fecha == 'mes':
                    # Desde hoy hasta el final del mes
                    num_days = calendar.monthrange(today.year, today.month)[1]
                    end_of_month = date(today.year, today.month, num_days)
                    query = query.filter(cast(proxima_compra_expr, Date).between(today, end_of_month))

            # Aplicar filtros
            if search_term := request.args.get('nombre') or request.args.get('search'):
                query = query.filter(Cliente.nombre.ilike(f'%{search_term}%'))
            
            if ciudad := request.args.get('ciudad'):
                query = query.filter(Cliente.ciudad.ilike(f'%{ciudad}%'))
            
            # Ordenación
            sort_by = request.args.get('sort_by', 'nombre')
            sort_order = request.args.get('sort_order', 'asc').lower()
            
            sortable_columns = {
                'nombre': Cliente.nombre,
                'ciudad': Cliente.ciudad,
                'ultima_fecha_compra': Cliente.ultima_fecha_compra,
                'frecuencia_compra_dias': Cliente.frecuencia_compra_dias
            }
            
            column_to_sort = sortable_columns.get(sort_by, Cliente.nombre)
            order_func = desc if sort_order == 'desc' else asc
            query = query.order_by(order_func(column_to_sort))
            
            # Paginación
            page, per_page = validate_pagination_params()
            resultado = query.paginate(page=page, per_page=per_page, error_out=False)
            
            # Generar proyecciones para cada cliente
            clientes_con_proyeccion = []
            for cliente in resultado.items:
                clientes_con_proyeccion.append(self._get_cliente_projection(cliente))
            
            return create_pagination_response(clientes_con_proyeccion, resultado), 200
            
        except Exception as e:
            logger.error(f"Error al obtener proyecciones de clientes: {str(e)}")
            db.session.rollback()
            return {"error": "Error al procesar la solicitud"}, 500
    
    def _get_cliente_projection(self, cliente):
        """
        Genera la proyección completa para un cliente específico
        """
        # Datos base del cliente
        cliente_data = cliente_schema.dump(cliente)
        
        # Obtener pedidos del cliente
        pedidos = Pedido.query.filter_by(cliente_id=cliente.id).order_by(desc(Pedido.fecha_creacion)).all()
        cliente_data['pedidos'] = pedidos_schema.dump(pedidos)
        
        # Obtener ventas para estadísticas
        ventas = Venta.query.filter_by(cliente_id=cliente.id).order_by(desc(Venta.fecha)).all()
        
        # Proyección de próxima compra (simplificado)
        cliente_data['proxima_compra_estimada'] = self._calcular_proyeccion_compra(cliente)
        
        # Estadísticas adicionales
        cliente_data['estadisticas'] = self._calcular_estadisticas_cliente(cliente, ventas, pedidos)
        
        return cliente_data
    
    def _calcular_proyeccion_compra(self, cliente):
        """
        Calcula la fecha estimada de la próxima compra basándose únicamente
        en los datos pre-calculados y mantenidos por la base de datos.
        """
        # Usar los campos 'ultima_fecha_compra' y 'frecuencia_compra_dias'
        # que son actualizados automáticamente por el trigger en Supabase.
        if cliente.ultima_fecha_compra and cliente.frecuencia_compra_dias and cliente.frecuencia_compra_dias > 0:
            fecha_estimada = cliente.ultima_fecha_compra + timedelta(days=cliente.frecuencia_compra_dias)
            return fecha_estimada.isoformat()
        
        # Si no hay datos suficientes en la DB, no se puede proyectar.
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