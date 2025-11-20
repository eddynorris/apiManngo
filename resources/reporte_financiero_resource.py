from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from sqlalchemy import func, distinct, case
from datetime import datetime
from decimal import Decimal
import logging
 # Asumiendo que db viene de extensions, ajustar si es models
from models import (
    db, Venta, VentaDetalle, Gasto, PresentacionProducto, 
    Lote, Pago, Inventario, Almacen
)
from common import handle_db_errors
from utils.file_handlers import get_presigned_url

logger = logging.getLogger(__name__)

# --- HELPERS / UTILIDADES ---

def _get_date_filters(req_args):
    """Parsea y valida fechas de inicio y fin desde los argumentos."""
    fecha_inicio_str = req_args.get('fecha_inicio')
    fecha_fin_str = req_args.get('fecha_fin')
    
    if not fecha_inicio_str or not fecha_fin_str:
        return None, None, None

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
        if fecha_inicio > fecha_fin:
            return None, None, "La fecha de inicio no puede ser mayor a la fecha fin."
        return fecha_inicio, fecha_fin, None
    except ValueError:
        return None, None, "Formato de fecha inválido, usar YYYY-MM-DD"

def _calcular_resumen_financiero(fecha_inicio, fecha_fin, almacen_id, lote_id):
    """
    Lógica centralizada para calcular totales financieros.
    Evita duplicar código entre el Resumen y el Reporte Unificado.
    """
    # 1. Base Query para Ventas (Detalles)
    ventas_q = db.session.query(
        VentaDetalle.venta_id,
        (VentaDetalle.cantidad * VentaDetalle.precio_unitario).label('total_linea')
    ).join(Venta, Venta.id == VentaDetalle.venta_id)

    # Filtros de Venta
    if fecha_inicio and fecha_fin:
        ventas_q = ventas_q.filter(func.date(Venta.fecha).between(fecha_inicio, fecha_fin))
    if almacen_id:
        ventas_q = ventas_q.filter(Venta.almacen_id == almacen_id)
    if lote_id:
        ventas_q = ventas_q.filter(VentaDetalle.lote_id == lote_id)

    ventas_sub = ventas_q.subquery()

    # 2. Totales de Ventas
    resumen_ventas = db.session.query(
        func.coalesce(func.sum(ventas_sub.c.total_linea), 0),
        func.count(distinct(ventas_sub.c.venta_id))
    ).first()
    
    total_ventas = resumen_ventas[0] or Decimal('0.00')
    num_ventas = resumen_ventas[1] or 0

    # 3. Cálculo de Deuda y Pagos
    # Identificar IDs de ventas involucradas
    venta_ids_filtradas = db.session.query(ventas_sub.c.venta_id).distinct()

    # Subquery de pagos totales por venta
    pagos_por_venta_sq = db.session.query(
        Pago.venta_id,
        func.sum(Pago.monto).label('total_pagado')
    ).group_by(Pago.venta_id).subquery()

    if lote_id:
        # Si filtramos por lote, la deuda se calcula sobre la FACTURA completa que contiene el lote.
        # Deuda = Suma(Total Venta - Total Pagado) para las ventas filtradas
        deuda_total_query = db.session.query(
            func.coalesce(func.sum(Venta.total - func.coalesce(pagos_por_venta_sq.c.total_pagado, 0)), 0)
        ).select_from(Venta).outerjoin(
            pagos_por_venta_sq, Venta.id == pagos_por_venta_sq.c.venta_id
        ).filter(Venta.id.in_(venta_ids_filtradas))
        
        total_deuda = deuda_total_query.scalar() or Decimal('0.00')
        # En contexto de lote, el 'total_pagado' es derivado: (Venta Filtrada - Deuda)
        # Nota: Esto es una aproximación financiera, ya que el pago no se asigna a líneas específicas.
        total_pagado = total_ventas - total_deuda if total_ventas > total_deuda else Decimal('0.00') 
    else:
        # Sin filtro de lote, sumamos pagos directos de las ventas filtradas
        total_pagado = db.session.query(func.coalesce(func.sum(Pago.monto), 0))\
            .filter(Pago.venta_id.in_(venta_ids_filtradas))\
            .scalar() or Decimal('0.00')
        total_deuda = total_ventas - total_pagado

    # 4. Gastos
    gastos_q = db.session.query(
        func.coalesce(func.sum(Gasto.monto), 0), 
        func.count(Gasto.id)
    )
    if fecha_inicio and fecha_fin:
        gastos_q = gastos_q.filter(Gasto.fecha.between(fecha_inicio, fecha_fin))
    if almacen_id:
        gastos_q = gastos_q.filter(Gasto.almacen_id == almacen_id)
    if lote_id:
        gastos_q = gastos_q.filter(Gasto.lote_id == lote_id)
    
    resumen_gastos = gastos_q.first()
    total_gastos = resumen_gastos[0]
    num_gastos = resumen_gastos[1]

    # 5. Depósitos (Solo confirmados)
    depositos_q = db.session.query(func.coalesce(func.sum(Pago.monto_depositado), 0)).filter(Pago.depositado == True)
    if fecha_inicio and fecha_fin:
        depositos_q = depositos_q.filter(func.date(Pago.fecha_deposito).between(fecha_inicio, fecha_fin))
    
    depositado_total = depositos_q.scalar() or Decimal('0.00')

    # Cálculos finales
    ganancia_neta = total_ventas - total_gastos
    margen_ganancia = (ganancia_neta / total_ventas * 100) if total_ventas > 0 else Decimal('0.00')

    return {
        'raw_values': { # Valores crudos para uso interno si es necesario
            'total_ventas': total_ventas,
            'total_gastos': total_gastos,
        },
        'formatted': {
            'total_ventas': str(total_ventas.quantize(Decimal('0.01'))),
            'total_pagado': str(total_pagado.quantize(Decimal('0.01'))),
            'total_deuda': str(total_deuda.quantize(Decimal('0.01'))),
            'total_gastos': str(total_gastos.quantize(Decimal('0.01'))),
            'ganancia_neta': str(ganancia_neta.quantize(Decimal('0.01'))),
            'margen_ganancia': f'{margen_ganancia:.2f}%',
            'depositado_total': str(depositado_total.quantize(Decimal('0.01'))),
            'numero_ventas': num_ventas,
            'numero_gastos': num_gastos
        }
    }

# --- RECURSOS ---

class ReporteVentasPresentacionResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        fecha_inicio, fecha_fin, error = _get_date_filters(request.args)
        if error: return {'error': error}, 400
        
        almacen_id = request.args.get('almacen_id', type=int)
        lote_id = request.args.get('lote_id', type=int)

        query = db.session.query(
            PresentacionProducto.id.label('presentacion_id'),
            PresentacionProducto.nombre.label('presentacion_nombre'),
            func.coalesce(func.sum(VentaDetalle.cantidad), 0).label('unidades_vendidas'),
            func.coalesce(func.sum(VentaDetalle.cantidad * VentaDetalle.precio_unitario), 0).label('total_vendido')
        ).join(VentaDetalle, VentaDetalle.presentacion_id == PresentacionProducto.id)\
         .join(Venta, Venta.id == VentaDetalle.venta_id)

        if fecha_inicio and fecha_fin:
            query = query.filter(func.date(Venta.fecha).between(fecha_inicio, fecha_fin))
        if almacen_id:
            query = query.filter(Venta.almacen_id == almacen_id)
        if lote_id:
            query = query.filter(VentaDetalle.lote_id == lote_id)

        reporte = query.group_by(PresentacionProducto.id, PresentacionProducto.nombre).all()

        return [{
            'presentacion_id': r.presentacion_id,
            'presentacion_nombre': r.presentacion_nombre,
            'unidades_vendidas': int(r.unidades_vendidas),
            'total_vendido': str(r.total_vendido.quantize(Decimal('0.01')))
        } for r in reporte], 200


class ResumenFinancieroResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        fecha_inicio, fecha_fin, error = _get_date_filters(request.args)
        if error: return {'error': error}, 400

        almacen_id = request.args.get('almacen_id', type=int)
        lote_id = request.args.get('lote_id', type=int)

        data = _calcular_resumen_financiero(fecha_inicio, fecha_fin, almacen_id, lote_id)
        return data['formatted'], 200


class ReporteUnificadoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        # 1. Filtros
        fecha_inicio, fecha_fin, error = _get_date_filters(request.args)
        if error: return {'error': error}, 400
        almacen_id = request.args.get('almacen_id', type=int)
        lote_id = request.args.get('lote_id', type=int)

        # 2. Resumen Financiero (Reutilizado)
        financiero_data = _calcular_resumen_financiero(fecha_inicio, fecha_fin, almacen_id, lote_id)
        
        # 3. KPIs y Ventas por Presentación
        # Query optimizada para KPIs
        ventas_base_q = db.session.query(
            VentaDetalle.presentacion_id,
            PresentacionProducto.nombre.label('presentacion_nombre'),
            func.coalesce(func.sum(VentaDetalle.cantidad), 0).label('unidades'),
            func.coalesce(func.sum(VentaDetalle.cantidad * VentaDetalle.precio_unitario), 0).label('total_linea'),
            func.coalesce(func.sum(VentaDetalle.cantidad * PresentacionProducto.capacidad_kg), 0).label('kg_linea')
        ).join(Venta, Venta.id == VentaDetalle.venta_id)\
         .join(PresentacionProducto, PresentacionProducto.id == VentaDetalle.presentacion_id)

        if fecha_inicio and fecha_fin:
            ventas_base_q = ventas_base_q.filter(func.date(Venta.fecha).between(fecha_inicio, fecha_fin))
        if almacen_id:
            ventas_base_q = ventas_base_q.filter(Venta.almacen_id == almacen_id)
        if lote_id:
            ventas_base_q = ventas_base_q.filter(VentaDetalle.lote_id == lote_id)

        # Agrupamos por producto para el listado, pero calculamos KPIs sumando en Python para evitar otra query
        ventas_agrupadas = ventas_base_q.group_by(VentaDetalle.presentacion_id, PresentacionProducto.nombre).all()

        ventas_por_presentacion = []
        total_kg_vendidos = Decimal(0)
        total_unidades_vendidas = 0

        for r in ventas_agrupadas:
            ventas_por_presentacion.append({
                'presentacion_id': r.presentacion_id,
                'presentacion_nombre': r.presentacion_nombre,
                'unidades_vendidas': int(r.unidades),
                'total_vendido': str(r.total_linea.quantize(Decimal('0.01'))),
                'kg_vendidos': float(r.kg_linea)
            })
            total_kg_vendidos += r.kg_linea
            total_unidades_vendidas += r.unidades

        # 4. Inventario Actual (Optimizado: Una sola query agrupada por Presentacion y Almacen)
        inv_q = db.session.query(
            Inventario.presentacion_id,
            PresentacionProducto.nombre.label('p_nombre'),
            PresentacionProducto.capacidad_kg.label('p_capacidad'),
            PresentacionProducto.precio_venta.label('p_precio'),
            Almacen.nombre.label('a_nombre'),
            func.coalesce(func.sum(Inventario.cantidad), 0).label('cantidad')
        ).join(PresentacionProducto, PresentacionProducto.id == Inventario.presentacion_id)\
         .join(Almacen, Almacen.id == Inventario.almacen_id)\
         .filter(PresentacionProducto.tipo.in_(['procesado', 'briqueta']))

        if almacen_id:
            inv_q = inv_q.filter(Inventario.almacen_id == almacen_id)
        
        inv_rows = inv_q.group_by(Inventario.presentacion_id, PresentacionProducto.nombre, 
                                 PresentacionProducto.capacidad_kg, PresentacionProducto.precio_venta,
                                 Almacen.nombre).all()

        # Procesamiento en memoria para estructurar JSON
        inv_map = {}
        valor_inventario_actual = Decimal(0)

        for row in inv_rows:
            pid = row.presentacion_id
            if pid not in inv_map:
                inv_map[pid] = {
                    'presentacion_id': pid,
                    'presentacion_nombre': row.p_nombre,
                    'stock_unidades': 0,
                    'stock_kg': Decimal(0),
                    'valor_estimado': Decimal(0),
                    'detalle_almacenes': []
                }
            
            # Agregamos detalle de almacén
            inv_map[pid]['detalle_almacenes'].append({
                'almacen': row.a_nombre,
                'cantidad': int(row.cantidad)
            })
            
            # Sumamos a los totales de la presentación
            cantidad_dec = row.cantidad
            inv_map[pid]['stock_unidades'] += int(cantidad_dec)
            inv_map[pid]['stock_kg'] += cantidad_dec * row.p_capacidad
            val_linea = cantidad_dec * row.p_precio
            inv_map[pid]['valor_estimado'] += val_linea
            
            # KPI Global
            valor_inventario_actual += val_linea

        # Convertir mapa a lista y formatear decimales
        inventario_actual_list = []
        for item in inv_map.values():
            item['stock_kg'] = float(item['stock_kg'])
            item['valor_estimado'] = str(item['valor_estimado'].quantize(Decimal('0.01')))
            inventario_actual_list.append(item)

        kpis = {
            'total_kg_vendidos': float(total_kg_vendidos),
            'total_unidades_vendidas': int(total_unidades_vendidas),
            'valor_inventario_actual': float(valor_inventario_actual)
        }

        # 5. Historial Depósitos (Reutilizando lógica de filtros)
        verificado_param = request.args.get('verificado')
        query_dep = db.session.query(Pago).filter(Pago.monto_depositado.isnot(None))
        
        if verificado_param is not None:
            es_verificado = str(verificado_param).strip().lower() in {'true', '1', 'yes', 'y'}
            query_dep = query_dep.filter(Pago.depositado == es_verificado)
        else:
            query_dep = query_dep.filter(Pago.depositado == True)
            
        if fecha_inicio and fecha_fin:
            query_dep = query_dep.filter(func.date(Pago.fecha_deposito).between(fecha_inicio, fecha_fin))
        
        dep_resource = DepositosHistorialResource()
        dep_resp, dep_status = dep_resource.get()
        historial_depositos = dep_resp if dep_status == 200 else []

        return {
            'resumen_financiero': financiero_data['formatted'],
            'kpis': kpis,
            'ventas_por_presentacion': ventas_por_presentacion,
            'inventario_actual': inventario_actual_list,
            'historial_depositos': historial_depositos
        }, 200

class DepositosHistorialResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Historial de depósitos agrupados por Referencia.
        Filtros recibidos: fecha_inicio, fecha_fin.
        """
        fecha_inicio_str = request.args.get('fecha_inicio')
        fecha_fin_str = request.args.get('fecha_fin')

        query = db.session.query(
            Pago.referencia,
            Pago.url_comprobante.label('comprobante_url'),
            Pago.fecha_deposito,
            func.sum(Pago.monto_depositado).label('monto_total_agrupado'),
            func.count(Pago.id).label('cantidad_pagos')
        ).filter(
            Pago.depositado == True,
            Pago.monto_depositado.isnot(None)
        )

        # 3. Aplicar Filtro de Fechas (Si el front las envía)
        if fecha_inicio_str and fecha_fin_str:
            try:
                fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                
                # Filtrar por rango de fecha de depósito
                query = query.filter(func.date(Pago.fecha_deposito).between(fecha_inicio, fecha_fin))
            except ValueError:
                return {'error': 'Formato de fecha inválido, usar YYYY-MM-DD'}, 400

        query = query.group_by(
            Pago.referencia,
            Pago.url_comprobante,
            Pago.fecha_deposito
        )
        resultados = query.order_by(Pago.fecha_deposito.desc()).all()
        response = []
        for r in resultados:
            presigned = get_presigned_url(r.comprobante_url) if r.comprobante_url else None
            response.append({
                'fecha_deposito': r.fecha_deposito.strftime('%Y-%m-%d %H:%M') if r.fecha_deposito else None,
                'referencia': r.referencia or "Sin Referencia",
                'monto_total': str(r.monto_total_agrupado),
                'comprobante_url': presigned or r.comprobante_url,
                'cantidad_pagos': r.cantidad_pagos
            })

        return response, 200