from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from sqlalchemy import func, distinct
from datetime import datetime
from decimal import Decimal

from models import db, Venta, VentaDetalle, Gasto, PresentacionProducto, Lote, Pago
from schemas import VentaDetalleSchema, GastoSchema

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