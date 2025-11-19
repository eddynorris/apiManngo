from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from sqlalchemy import func, distinct
from datetime import datetime
from decimal import Decimal
import logging

from models import db, Venta, VentaDetalle, Gasto, PresentacionProducto, Lote, Pago, Inventario, Almacen
from common import handle_db_errors
from schemas import VentaDetalleSchema, GastoSchema

# Configurar logging
logger = logging.getLogger(__name__)

# El recurso ReporteVentasPresentacionResource no necesita cambios,
# su lógica de filtrado por lote es correcta para su propósito.
class ReporteVentasPresentacionResource(Resource):
    @jwt_required()
    def get(self):
        """
        Genera un reporte de ventas por presentación de producto.
        Filtros:
        - fecha_inicio, fecha_fin (YYYY-MM-DD)
        - almacen_id
        - lote_id
        """
        try:
            # --- Obtención y validación de filtros ---
            fecha_inicio_str = request.args.get('fecha_inicio')
            fecha_fin_str = request.args.get('fecha_fin')
            almacen_id = request.args.get('almacen_id', type=int)
            lote_id = request.args.get('lote_id', type=int)

            query = db.session.query(
                PresentacionProducto.id.label('presentacion_id'),
                PresentacionProducto.nombre.label('presentacion_nombre'),
                func.sum(VentaDetalle.cantidad).label('unidades_vendidas'),
                func.sum(VentaDetalle.cantidad * VentaDetalle.precio_unitario).label('total_vendido')
            ).join(VentaDetalle, VentaDetalle.presentacion_id == PresentacionProducto.id)\
             .join(Venta, Venta.id == VentaDetalle.venta_id)

            # Aplicar filtros de fecha si se proporcionan
            if fecha_inicio_str and fecha_fin_str:
                try:
                    fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                    fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                    query = query.filter(func.date(Venta.fecha).between(fecha_inicio, fecha_fin))
                except ValueError:
                    return {'error': 'Formato de fecha inválido, usar YYYY-MM-DD'}, 400

            # Aplicar filtro de almacén
            if almacen_id:
                query = query.filter(Venta.almacen_id == almacen_id)

            # Lógica nueva y correcta
            if lote_id:
                query = query.filter(VentaDetalle.lote_id == lote_id)

            # Agrupar para obtener los resultados
            reporte = query.group_by(PresentacionProducto.id, PresentacionProducto.nombre).all()

            # Formatear respuesta
            resultado = [{
                'presentacion_id': r.presentacion_id,
                'presentacion_nombre': r.presentacion_nombre,
                'unidades_vendidas': int(r.unidades_vendidas),
                'total_vendido': str(r.total_vendido)
            } for r in reporte]

            return resultado, 200

        except Exception as e:
            db.session.rollback()
            return {'error': 'Error interno del servidor', 'details': str(e)}, 500

class ResumenFinancieroResource(Resource):
    @jwt_required()
    def get(self):
        """
        Devuelve un resumen financiero con totales de ventas, gastos y ganancias.
        La lógica de filtrado por lote ahora es directa sobre VentaDetalle.
        """
        try:
            # --- Obtención y validación de filtros ---
            fecha_inicio_str = request.args.get('fecha_inicio')
            fecha_fin_str = request.args.get('fecha_fin')
            almacen_id = request.args.get('almacen_id', type=int)
            lote_id = request.args.get('lote_id', type=int)

            # --- 1. Construir la consulta base para VENTAS ---
            # Identifica las líneas de detalle que cumplen con los filtros.
            ventas_filtradas_query = db.session.query(
                VentaDetalle.venta_id,
                (VentaDetalle.cantidad * VentaDetalle.precio_unitario).label('total_linea')
            ).join(Venta, Venta.id == VentaDetalle.venta_id)

            # --- 2. Aplicar filtros ---
            fecha_inicio, fecha_fin = None, None
            if fecha_inicio_str and fecha_fin_str:
                try:
                    fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                    fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                    ventas_filtradas_query = ventas_filtradas_query.filter(func.date(Venta.fecha).between(fecha_inicio, fecha_fin))
                except ValueError:
                    return {'error': 'Formato de fecha inválido, usar YYYY-MM-DD'}, 400

            if almacen_id:
                ventas_filtradas_query = ventas_filtradas_query.filter(Venta.almacen_id == almacen_id)

            # --- LÓGICA DE FILTRADO POR LOTE (CORREGIDA Y DIRECTA) ---
            if lote_id:
                ventas_filtradas_query = ventas_filtradas_query.filter(VentaDetalle.lote_id == lote_id)

            ventas_subquery = ventas_filtradas_query.subquery()

            # --- 3. Calcular totales de VENTAS (Subtotal por filtros) ---
            resumen_ventas = db.session.query(
                func.sum(ventas_subquery.c.total_linea),
                func.count(distinct(ventas_subquery.c.venta_id))
            ).first()
            total_ventas, num_ventas = resumen_ventas
            total_ventas = total_ventas or Decimal('0.00')
            num_ventas = num_ventas or 0

            venta_ids_filtradas = db.session.query(ventas_subquery.c.venta_id).distinct()

            if lote_id:
                # --- 4. Lógica de deuda y pago para filtro por LOTE ---
                # La deuda se calcula sobre el TOTAL de las ventas que contienen el lote.
                pagos_por_venta_sq = db.session.query(
                    Pago.venta_id,
                    func.sum(Pago.monto).label('total_pagado')
                ).group_by(Pago.venta_id).subquery()

                deuda_total_query = db.session.query(
                    func.sum(Venta.total - func.coalesce(pagos_por_venta_sq.c.total_pagado, 0))
                ).select_from(Venta).outerjoin(
                    pagos_por_venta_sq, Venta.id == pagos_por_venta_sq.c.venta_id
                ).filter(Venta.id.in_(venta_ids_filtradas))
                
                total_deuda = deuda_total_query.scalar() or Decimal('0.00')
                total_pagado = total_ventas - total_deuda
            else:
                # --- 4. Lógica de deuda y pago para filtros GENERALES ---
                total_pagado = db.session.query(func.sum(Pago.monto))\
                    .filter(Pago.venta_id.in_(venta_ids_filtradas))\
                    .scalar() or Decimal('0.00')
                total_deuda = total_ventas - total_pagado

            # --- 5. Calcular GASTOS ---
            gastos_query = db.session.query(func.sum(Gasto.monto), func.count(Gasto.id))
            if fecha_inicio and fecha_fin:
                gastos_query = gastos_query.filter(Gasto.fecha.between(fecha_inicio, fecha_fin))
            if almacen_id:
                gastos_query = gastos_query.filter(Gasto.almacen_id == almacen_id)
            if lote_id:
                gastos_query = gastos_query.filter(Gasto.lote_id == lote_id)
            
            total_gastos, num_gastos = gastos_query.first()

            # --- 6. Formatear resultados ---
            total_gastos = total_gastos or Decimal('0.00')
            num_gastos = num_gastos or 0

            ganancia_neta = total_ventas - total_gastos
            margen_ganancia = (ganancia_neta / total_ventas * 100) if total_ventas > 0 else Decimal('0.00')

            return {
                'total_ventas': str(total_ventas.quantize(Decimal('0.01'))),
                'total_pagado': str(total_pagado.quantize(Decimal('0.01'))),
                'total_deuda': str(total_deuda.quantize(Decimal('0.01'))),
                'total_gastos': str(total_gastos.quantize(Decimal('0.01'))),
                'ganancia_neta': str(ganancia_neta.quantize(Decimal('0.01'))),
                'margen_ganancia': f'{margen_ganancia:.2f}%',
                'numero_ventas': num_ventas,
                'numero_gastos': num_gastos
            }, 200

        except Exception as e:
            db.session.rollback()
            logger.exception("Error en ResumenFinancieroResource")
            return {'error': 'Error interno del servidor', 'details': str(e)}, 500


class ReporteUnificadoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Reporte unificado:
        - resumen_financiero
        - kpis
        - ventas_por_presentacion
        - inventario_actual
        Filtros: fecha_inicio, fecha_fin (YYYY-MM-DD), almacen_id, lote_id
        """
        # --- Filtros ---
        fecha_inicio_str = request.args.get('fecha_inicio')
        fecha_fin_str = request.args.get('fecha_fin')
        almacen_id = request.args.get('almacen_id', type=int)
        lote_id = request.args.get('lote_id', type=int)

        fecha_inicio, fecha_fin = None, None
        if fecha_inicio_str and fecha_fin_str:
            try:
                fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
            except ValueError:
                return {'error': 'Formato de fecha inválido, usar YYYY-MM-DD'}, 400

        # --- Ventas base (líneas) ---
        ventas_q = db.session.query(
            VentaDetalle.presentacion_id,
            VentaDetalle.venta_id,
            VentaDetalle.cantidad.label('unidades'),
            (VentaDetalle.cantidad * VentaDetalle.precio_unitario).label('total_linea'),
            (VentaDetalle.cantidad * PresentacionProducto.capacidad_kg).label('kg_linea')
        ).join(Venta, Venta.id == VentaDetalle.venta_id)
        ventas_q = ventas_q.join(PresentacionProducto, PresentacionProducto.id == VentaDetalle.presentacion_id)
        if fecha_inicio and fecha_fin:
            ventas_q = ventas_q.filter(func.date(Venta.fecha).between(fecha_inicio, fecha_fin))
        if almacen_id:
            ventas_q = ventas_q.filter(Venta.almacen_id == almacen_id)
        if lote_id:
            ventas_q = ventas_q.filter(VentaDetalle.lote_id == lote_id)

        ventas_sq = ventas_q.subquery()

        # --- Resumen financiero ---
        total_ventas, num_ventas = db.session.query(
            func.coalesce(func.sum(ventas_sq.c.total_linea), 0),
            func.count(distinct(ventas_sq.c.venta_id))
        ).first()
        pagos_por_venta_sq = db.session.query(
            Pago.venta_id,
            func.sum(Pago.monto).label('total_pagado')
        ).group_by(Pago.venta_id).subquery()

        venta_ids_filtradas = db.session.query(ventas_sq.c.venta_id).distinct()
        if lote_id:
            deuda_total_query = db.session.query(
                func.coalesce(func.sum(Venta.total - func.coalesce(pagos_por_venta_sq.c.total_pagado, 0)), 0)
            ).select_from(Venta).outerjoin(
                pagos_por_venta_sq, Venta.id == pagos_por_venta_sq.c.venta_id
            ).filter(Venta.id.in_(venta_ids_filtradas))
            total_deuda = deuda_total_query.scalar() or Decimal('0.00')
            total_pagado = Decimal(total_ventas) - total_deuda
        else:
            total_pagado = db.session.query(func.coalesce(func.sum(Pago.monto), 0))\
                .filter(Pago.venta_id.in_(venta_ids_filtradas)).scalar() or Decimal('0.00')
            total_deuda = Decimal(total_ventas) - total_pagado

        gastos_q = db.session.query(func.coalesce(func.sum(Gasto.monto), 0), func.count(Gasto.id))
        if fecha_inicio and fecha_fin:
            gastos_q = gastos_q.filter(Gasto.fecha.between(fecha_inicio, fecha_fin))
        if almacen_id:
            gastos_q = gastos_q.filter(Gasto.almacen_id == almacen_id)
        if lote_id:
            gastos_q = gastos_q.filter(Gasto.lote_id == lote_id)
        total_gastos, num_gastos = gastos_q.first()

        total_ventas_dec = Decimal(str(total_ventas))
        total_gastos_dec = Decimal(str(total_gastos))
        ganancia_neta = total_ventas_dec - total_gastos_dec
        margen_ganancia = (ganancia_neta / total_ventas_dec * 100) if total_ventas_dec > 0 else Decimal('0.00')

        resumen_financiero = {
            'total_ventas': str(total_ventas_dec.quantize(Decimal('0.01'))),
            'total_gastos': str(total_gastos_dec.quantize(Decimal('0.01'))),
            'ganancia_neta': str(ganancia_neta.quantize(Decimal('0.01'))),
            'margen_ganancia': f'{margen_ganancia:.2f}%',
            'total_deuda': str(Decimal(str(total_deuda)).quantize(Decimal('0.01'))),
            'total_pagado': str(Decimal(str(total_pagado)).quantize(Decimal('0.01')))
        }

        # --- KPIs ---
        total_unidades_vendidas, total_kg_vendidos = db.session.query(
            func.coalesce(func.sum(ventas_sq.c.unidades), 0),
            func.coalesce(func.sum(ventas_sq.c.kg_linea), 0)
        ).first()
        # Inventario valor actual
        inv_q = db.session.query(
            func.coalesce(func.sum(Inventario.cantidad * PresentacionProducto.precio_venta), 0)
        ).join(PresentacionProducto, PresentacionProducto.id == Inventario.presentacion_id).filter(
            PresentacionProducto.tipo.in_(['procesado', 'briqueta'])
        )
        if almacen_id:
            inv_q = inv_q.filter(Inventario.almacen_id == almacen_id)
        valor_inventario_actual = inv_q.scalar() or 0
        kpis = {
            'total_kg_vendidos': float(total_kg_vendidos or 0),
            'total_unidades_vendidas': int(total_unidades_vendidas or 0),
            'valor_inventario_actual': float(valor_inventario_actual)
        }

        # --- Ventas por presentación ---
        ventas_por_presentacion = []
        vpp_rows = db.session.query(
            ventas_sq.c.presentacion_id,
            PresentacionProducto.nombre.label('presentacion_nombre'),
            func.coalesce(func.sum(ventas_sq.c.unidades), 0).label('unidades_vendidas'),
            func.coalesce(func.sum(ventas_sq.c.total_linea), 0).label('total_vendido'),
            func.coalesce(func.sum(ventas_sq.c.kg_linea), 0).label('kg_vendidos')
        ).join(PresentacionProducto, PresentacionProducto.id == ventas_sq.c.presentacion_id)\
         .group_by(ventas_sq.c.presentacion_id, PresentacionProducto.nombre).all()
        for r in vpp_rows:
            ventas_por_presentacion.append({
                'presentacion_id': int(r.presentacion_id),
                'presentacion_nombre': r.presentacion_nombre,
                'unidades_vendidas': int(r.unidades_vendidas or 0),
                'total_vendido': str(Decimal(str(r.total_vendido or 0)).quantize(Decimal('0.01'))),
                'kg_vendidos': float(r.kg_vendidos or 0)
            })

        # --- Inventario actual ---
        inventario_actual = []
        inv_rows = db.session.query(
            Inventario.presentacion_id,
            PresentacionProducto.nombre.label('presentacion_nombre'),
            func.coalesce(func.sum(Inventario.cantidad), 0).label('stock_unidades'),
            func.coalesce(func.sum(Inventario.cantidad * PresentacionProducto.capacidad_kg), 0).label('stock_kg'),
            func.coalesce(func.sum(Inventario.cantidad * PresentacionProducto.precio_venta), 0).label('valor_estimado')
        ).join(PresentacionProducto, PresentacionProducto.id == Inventario.presentacion_id).filter(
            PresentacionProducto.tipo.in_(['procesado', 'briqueta'])
        )
        if almacen_id:
            inv_rows = inv_rows.filter(Inventario.almacen_id == almacen_id)
        inv_rows = inv_rows.group_by(Inventario.presentacion_id, PresentacionProducto.nombre).all()

        # detalle por almacenes
        detalle_rows = db.session.query(
            Inventario.presentacion_id,
            Almacen.nombre.label('almacen'),
            func.coalesce(func.sum(Inventario.cantidad), 0).label('cantidad')
        ).join(Almacen, Almacen.id == Inventario.almacen_id).join(
            PresentacionProducto, PresentacionProducto.id == Inventario.presentacion_id
        ).filter(
            PresentacionProducto.tipo.in_(['procesado', 'briqueta'])
        )
        if almacen_id:
            detalle_rows = detalle_rows.filter(Inventario.almacen_id == almacen_id)
        detalle_rows = detalle_rows.group_by(Inventario.presentacion_id, Almacen.nombre).all()
        detalle_map = {}
        for d in detalle_rows:
            detalle_map.setdefault(int(d.presentacion_id), []).append({
                'almacen': d.almacen,
                'cantidad': int(d.cantidad or 0)
            })

        for r in inv_rows:
            inventario_actual.append({
                'presentacion_id': int(r.presentacion_id),
                'presentacion_nombre': r.presentacion_nombre,
                'stock_unidades': int(r.stock_unidades or 0),
                'stock_kg': float(r.stock_kg or 0),
                'valor_estimado': str(Decimal(str(r.valor_estimado or 0)).quantize(Decimal('0.01'))),
                'detalle_almacenes': detalle_map.get(int(r.presentacion_id), [])
            })

        return {
            'resumen_financiero': resumen_financiero,
            'kpis': kpis,
            'ventas_por_presentacion': ventas_por_presentacion,
            'inventario_actual': inventario_actual
        }, 200