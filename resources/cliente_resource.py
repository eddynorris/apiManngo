# ARCHIVO: cliente_resource.py
from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required
from flask import request, send_file
from models import Cliente, Pedido, Venta, VistaClienteProyeccion
from schemas import cliente_schema, clientes_schema, ClienteSchema, pedidos_schema
from extensions import db
from common import handle_db_errors, validate_pagination_params, create_pagination_response, rol_requerido
import pandas as pd
import re
import io
import logging
import calendar
from sqlalchemy import func, desc, asc, cast, Date, case, text
from sqlalchemy.orm import aliased
from sqlalchemy import orm
from datetime import datetime, timezone, timedelta, date
from types import SimpleNamespace

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
    """Recursos de proyección para clientes.

    Cambios y mejoras:
    - Filtros de fecha robustos (fecha, fecha_desde, fecha_hasta) con validación y múltiples formatos.
    - Refactor de condicionales mediante estrategias de filtros y ordenamiento.
    - Optimización de consulta principal incluyendo última venta (subconsulta) y sanitización de entradas.
    """

    @jwt_required()
    @handle_db_errors
    def get(self, cliente_id=None):
        """Obtiene detalle o lista de proyecciones según presencia de `cliente_id`."""
        if cliente_id:
            return self._get_detalle_cliente(cliente_id)
        else:
            return self._get_lista_proyecciones()

    def _get_detalle_cliente(self, cliente_id):
        try:
            codigo = request.args.get('codigo')
            if codigo:
                try:
                    cliente_id = int(codigo)
                except ValueError:
                    return {"error": "codigo inválido"}, 400
            cliente = Cliente.query.options(
                orm.selectinload(Cliente.ventas).selectinload(Venta.detalles)
            ).get_or_404(cliente_id)
            ventas = sorted(cliente.ventas, key=lambda x: x.fecha, reverse=True)
            historial = []
            for v in ventas:
                detalles = [{
                    'presentacion': d.presentacion.nombre if d.presentacion else None,
                    'cantidad': int(d.cantidad),
                    'precio_unitario': float(d.precio_unitario)
                } for d in (v.detalles or [])]
                historial.append({
                    'id': v.id,
                    'fecha': v.fecha.isoformat() if v.fecha else None,
                    'total': float(v.total),
                    'estado_pago': v.estado_pago,
                    'detalles': detalles
                })
            productos_counter = {}
            for v in ventas:
                for d in (v.detalles or []):
                    nombre = d.presentacion.nombre if d.presentacion else 'N/A'
                    productos_counter[nombre] = productos_counter.get(nombre, 0) + int(d.cantidad)
            productos_mas = sorted(productos_counter.items(), key=lambda x: x[1], reverse=True)[:5]
            monto_total_comprado = float(sum(v.total for v in ventas)) if ventas else 0.0
            promedio_compra = round(monto_total_comprado / len(ventas), 2) if ventas else 0.0
            estadisticas = {
                'total_ventas': len(ventas),
                'monto_total_comprado': monto_total_comprado,
                'promedio_compra': promedio_compra,
                'frecuencia_compra_dias': cliente.frecuencia_compra_dias or 0,
                'productos_mas_comprados': [{'nombre': n, 'cantidad': c} for n, c in productos_mas]
            }
            vp = db.session.query(VistaClienteProyeccion).filter(VistaClienteProyeccion.id == cliente.id).first()
            proyeccion = {
                'fecha_estimada': vp.proxima_compra_estimada.isoformat() if vp and vp.proxima_compra_estimada else None,
                'productos_probables': [{'nombre': n, 'cantidad': c} for n, c in productos_mas],
                'valor_estimado': float(vp.promedio_compra or 0) if vp and vp.promedio_compra is not None else promedio_compra
            }
            return {
                'codigo': str(cliente.id),
                'historial_ventas': historial,
                'estadisticas': estadisticas,
                'proyeccion_detallada': proyeccion
            }, 200
        except Exception as e:
            logger.error(f"Error al obtener detalle del cliente {cliente_id}: {str(e)}")
            return {"error": "Error al procesar la solicitud de detalle"}, 500

    def _get_lista_proyecciones(self):
        """Lista de clientes con proyecciones usando la vista materializada en la base de datos."""
        try:
            args = request.args
            query = db.session.query(VistaClienteProyeccion)
            search_term = args.get('search') or args.get('nombre')
            if search_term:
                query = query.filter(VistaClienteProyeccion.nombre.ilike(f'%{search_term}%'))
            if args.get('ciudad'):
                ciudad = self._sanitize_text(args.get('ciudad'))
                if ciudad:
                    query = query.filter(VistaClienteProyeccion.ciudad.ilike(f'%{ciudad}%'))
            query = query.filter(
                VistaClienteProyeccion.frecuencia_compra_dias.isnot(None),
                VistaClienteProyeccion.frecuencia_compra_dias > 0,
                VistaClienteProyeccion.proxima_compra_estimada.isnot(None)
            )
            single_date, start_date, end_date = self._parse_date_args(args)
            if single_date or start_date or end_date:
                date_col = cast(VistaClienteProyeccion.proxima_compra_estimada, Date)
                if single_date:
                    query = query.filter(date_col == single_date)
                else:
                    if start_date:
                        query = query.filter(date_col >= start_date)
                    if end_date:
                        query = query.filter(date_col <= end_date)
            query = query.order_by(asc(VistaClienteProyeccion.proxima_compra_estimada).nulls_last())
            page, per_page = validate_pagination_params()
            paginated_results = query.paginate(page=page, per_page=per_page, error_out=False)
            clientes_con_proyeccion = []
            for vp in paginated_results.items:
                estado = None
                if vp.frecuencia_compra_dias and vp.frecuencia_compra_dias > 0 and vp.ultima_fecha_compra:
                    estado = 'retrasado' if (vp.dias_retraso or 0) > 0 else ('proximo' if vp.estado_proyeccion == 'proximo' else 'programado')
                cliente_data = {
                    'codigo': str(vp.id),
                    'nombre': vp.nombre,
                    'telefono': vp.telefono,
                    'ciudad': vp.ciudad,
                    'ultima_fecha_compra': vp.ultima_fecha_compra.isoformat() if vp.ultima_fecha_compra else None,
                    'proxima_fecha_estimada': vp.proxima_compra_estimada.isoformat() if vp.proxima_compra_estimada else None,
                    'estado_proyeccion': estado or 'sin_proyeccion'
                }
                clientes_con_proyeccion.append(cliente_data)
            return {
                'data': clientes_con_proyeccion,
                'pagination': {
                    'total': paginated_results.total,
                    'page': paginated_results.page,
                    'per_page': paginated_results.per_page,
                    'pages': paginated_results.pages
                },
                'resumen': self._generar_resumen_global(clientes_con_proyeccion)
            }, 200
        except ValueError as ve:
            return {'error': str(ve)}, 400
        except Exception as e:
            logger.error(f"Error al obtener lista de proyecciones: {str(e)}")
            db.session.rollback()
            return {"error": "Error al procesar la lista de proyecciones"}, 500

    def _calcular_proyeccion_compra(self, cliente):
        """
        Calcula la próxima fecha estimada de compra con análisis detallado
        """
        if not cliente.ultima_fecha_compra:
            return {
                'disponible': False,
                'mensaje': 'Cliente sin historial de compras'
            }
        
        if not cliente.frecuencia_compra_dias or cliente.frecuencia_compra_dias <= 0:
            return {
                'disponible': False,
                'mensaje': 'Se necesitan al menos 2 compras para calcular frecuencia'
            }
        
        try:
            fecha_actual = datetime.now(timezone.utc)
            
            # Calcular próxima fecha estimada
            proxima_fecha = cliente.ultima_fecha_compra + timedelta(days=cliente.frecuencia_compra_dias)
            
            # Calcular días desde última compra
            dias_desde_ultima = (fecha_actual.date() - cliente.ultima_fecha_compra.date()).days
            
            # Calcular retraso o días restantes
            if proxima_fecha.date() < fecha_actual.date():
                dias_retraso = (fecha_actual.date() - proxima_fecha.date()).days
                estado = 'retrasado'
                nivel_urgencia = self._calcular_urgencia(dias_retraso, cliente.frecuencia_compra_dias)
            else:
                dias_retraso = 0
                dias_restantes = (proxima_fecha.date() - fecha_actual.date()).days
                estado = 'proximo' if dias_restantes <= 3 else 'programado'
                nivel_urgencia = 'alta' if dias_restantes <= 3 else 'normal'
            
            return {
                'disponible': True,
                'fecha_estimada': proxima_fecha.isoformat(),
                'fecha_estimada_formato': proxima_fecha.strftime('%Y-%m-%d'),
                'dias_retraso': dias_retraso,
                'dias_desde_ultima_compra': dias_desde_ultima,
                'frecuencia_dias': cliente.frecuencia_compra_dias,
                'estado': estado,
                'nivel_urgencia': nivel_urgencia,
                'porcentaje_ciclo': min(100, int((dias_desde_ultima / cliente.frecuencia_compra_dias) * 100))
            }
            
        except Exception as e:
            logger.error(f"Error calculando proyección para cliente {cliente.id}: {str(e)}")
            return {
                'disponible': False,
                'mensaje': 'Error al calcular proyección'
            }
    
    def _calcular_urgencia(self, dias_retraso, frecuencia_compra):
        """
        Calcula nivel de urgencia basado en el retraso relativo
        """
        if dias_retraso <= 0:
            return 'normal'
        
        porcentaje_retraso = (dias_retraso / frecuencia_compra) * 100
        
        if porcentaje_retraso > 100:  # Más del doble del ciclo
            return 'critica'
        elif porcentaje_retraso > 50:  # 50% de retraso
            return 'alta'
        elif porcentaje_retraso > 25:
            return 'media'
        else:
            return 'baja'
    
    def _calcular_estadisticas_cliente(self, cliente, ventas):
        """
        Calcula estadísticas detalladas del cliente
        """
        estadisticas = {
            'total_ventas': len(ventas),
            'monto_total_comprado': 0,
            'saldo_pendiente': float(cliente.saldo_pendiente),
            'promedio_compra': 0,
            'ventas_por_estado': {
                'pagado': 0,
                'parcial': 0,
                'pendiente': 0
            },
            'ultima_actividad': None,
            'tendencia_compra': None
        }
        
        if ventas:
            # Montos
            estadisticas['monto_total_comprado'] = float(sum(v.total for v in ventas))
            estadisticas['promedio_compra'] = round(
                estadisticas['monto_total_comprado'] / len(ventas), 2
            )
            estadisticas['ultima_actividad'] = ventas[0].fecha.isoformat()
            
            # Contar por estado
            for venta in ventas:
                estadisticas['ventas_por_estado'][venta.estado_pago] += 1
            
            # Análisis de tendencia (últimas 3 vs anteriores)
            if len(ventas) >= 6:
                ultimas_3 = sum(v.total for v in ventas[:3]) / 3
                anteriores_3 = sum(v.total for v in ventas[3:6]) / 3
                
                diferencia_porcentual = ((ultimas_3 - anteriores_3) / anteriores_3) * 100
                
                if diferencia_porcentual > 10:
                    estadisticas['tendencia_compra'] = 'creciente'
                elif diferencia_porcentual < -10:
                    estadisticas['tendencia_compra'] = 'decreciente'
                else:
                    estadisticas['tendencia_compra'] = 'estable'
        
        return estadisticas
    
    def _calcular_prioridad(self, cliente, proyeccion, total_ventas, monto_total):
        """
        Calcula prioridad del cliente para seguimiento
        """
        puntos = 0
        
        # Retraso (máx 40 puntos)
        if proyeccion and proyeccion.get('disponible'):
            dias_retraso = proyeccion.get('dias_retraso', 0)
            if dias_retraso > 0:
                puntos += min(40, dias_retraso * 2)
        
        # Valor del cliente (máx 30 puntos)
        if monto_total > 10000:
            puntos += 30
        elif monto_total > 5000:
            puntos += 20
        elif monto_total > 1000:
            puntos += 10
        
        # Frecuencia de compra (máx 20 puntos)
        if total_ventas > 20:
            puntos += 20
        elif total_ventas > 10:
            puntos += 15
        elif total_ventas > 5:
            puntos += 10
        
        # Saldo pendiente (máx 10 puntos)
        saldo = float(cliente.saldo_pendiente)
        if saldo > 1000:
            puntos += 10
        elif saldo > 500:
            puntos += 5
        
        # Clasificar
        if puntos >= 70:
            return 'alta'
        elif puntos >= 40:
            return 'media'
        else:
            return 'baja'

    def _sanitize_text(self, value):
        """Sanitiza texto de entrada para filtros no estructurados (como ciudad)."""
        if not value:
            return None
        return re.sub(r'[^\w\s\-áéíóúÁÉÍÓÚñÑ]', '', value)

    def _param_bool(self, value):
        """Convierte parámetros booleanos ("true"/"false") en bool."""
        if value is None:
            return False
        return str(value).strip().lower() in {'true', '1', 'yes', 'y'}

    def _parse_date_value(self, value):
        """Parsea una fecha en múltiples formatos y retorna `date`.

        Formatos aceptados: YYYY-MM-DD, DD/MM/YYYY, YYYY/MM/DD, DD-MM-YYYY.
        """
        if not value:
            return None
        value = str(value).strip()
        formats = ['%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y']
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Formato de fecha inválido: {value}")

    def _parse_date_args(self, args):
        """Obtiene y valida parámetros de fecha: fecha, fecha_desde, fecha_hasta."""
        single_date = None
        start_date = None
        end_date = None

        if args.get('fecha'):
            single_date = self._parse_date_value(args.get('fecha'))

        if args.get('fecha_desde'):
            start_date = self._parse_date_value(args.get('fecha_desde'))
        if args.get('fecha_hasta'):
            end_date = self._parse_date_value(args.get('fecha_hasta'))

        if start_date and end_date and start_date > end_date:
            raise ValueError('El rango de fechas es inválido: fecha_desde > fecha_hasta')

        return single_date, start_date, end_date

    def _apply_date_filters(self, query, cliente_date_col, ultima_venta_col, single_date, start_date, end_date, proxima_compra_col=None):
        """Aplica filtros de fecha sobre columnas de última compra/venta.

        Si se proporciona `proxima_compra_col`, se filtra por proyección; de lo contrario
        aplica sobre coalesce(ultima_venta, ultima_fecha_compra) para que clientes sin ventas
        recientes usen su `ultima_fecha_compra`.
        """
        date_expr = proxima_compra_col if proxima_compra_col is not None else func.coalesce(ultima_venta_col, cliente_date_col)

        if single_date:
            query = query.filter(date_expr == single_date)
        else:
            if start_date:
                query = query.filter(date_expr >= start_date)
            if end_date:
                query = query.filter(date_expr <= end_date)
        return query

    def _apply_order_strategy(self, query, order_by):
        """Aplica la estrategia de ordenamiento según `order_by`."""
        strategies = {
            'ultima_compra': lambda q: q.order_by(desc(Cliente.ultima_fecha_compra)),
            'saldo': lambda q: q.order_by(desc(Cliente.saldo_pendiente)),
            'nombre': lambda q: q.order_by(asc(Cliente.nombre)),
            'frecuencia': lambda q: q.order_by(asc(Cliente.frecuencia_compra_dias))
        }
        strategy = strategies.get(order_by, strategies['ultima_compra'])
        return strategy(query)
    
    def _generar_resumen_global(self, clientes):
        """
        Genera resumen ejecutivo de todos los clientes
        """
        total_clientes = len(clientes)
        
        if total_clientes == 0:
            return {
                'total_clientes': 0,
                'con_proyeccion': 0,
                'con_retraso': 0,
                'urgencia_critica': 0
            }
        
        con_proyeccion = sum(1 for c in clientes 
                            if c.get('proxima_compra_estimada', {}).get('disponible'))
        
        con_retraso = sum(1 for c in clientes 
                         if c.get('tiene_retraso', False))
        
        urgencia_critica = sum(1 for c in clientes 
                              if c.get('proxima_compra_estimada', {}).get('nivel_urgencia') == 'critica')
        
        return {
            'total_clientes': total_clientes,
            'con_proyeccion': con_proyeccion,
            'con_retraso': con_retraso,
            'urgencia_critica': urgencia_critica,
            'porcentaje_retraso': round((con_retraso / total_clientes) * 100, 1) if total_clientes > 0 else 0
        }

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