from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Venta, Pedido, Inventario, Cliente, PresentacionProducto, Almacen, Lote, Pago
from extensions import db
from common import handle_db_errors, rol_requerido
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, case
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class DashboardResource(Resource):
    @jwt_required()
    @rol_requerido('admin')  # Permitir acceso a todos los roles
    @handle_db_errors
    def get(self):
        """
        Endpoint consolidado para alertas del dashboard de la app móvil.
        Agrega datos de inventario bajo, lotes bajos y clientes con saldo pendiente.
        Las alertas NO usan filtro de fecha.
        """
        claims = get_jwt()
        user_rol = claims.get('rol')
        user_almacen_id = claims.get('almacen_id')
        is_admin_or_gerente = user_rol in ['admin', 'gerente']

        # Inventario con stock bajo (SIN filtro de fecha)
        inventario_query = db.session.query(
            Inventario.presentacion_id,
            PresentacionProducto.nombre.label('presentacion_nombre'),
            Inventario.cantidad,
            Inventario.stock_minimo,
            Inventario.almacen_id,
            Almacen.nombre.label('almacen_nombre')
        ).join(PresentacionProducto, Inventario.presentacion_id == PresentacionProducto.id)\
         .join(Almacen, Inventario.almacen_id == Almacen.id)\
         .filter(Inventario.cantidad <= Inventario.stock_minimo) # Alerta de stock bajo

        # Lotes con cantidad baja (SIN filtro de fecha)
        # Ajusta el umbral (e.g., 500) según sea necesario
        UMBRAL_LOTE_BAJO_KG = 500
        lotes_query = db.session.query(
            Lote.id.label('lote_id'),
            Lote.descripcion.label('lote_descripcion'),
            Lote.cantidad_disponible_kg,
            Lote.producto_id, # Para posible referencia futura
            # Si necesitas el nombre del producto, añade un join:
            # .join(Producto, Lote.producto_id == Producto.id)
            # y selecciona Producto.nombre
        ).filter(Lote.cantidad_disponible_kg < UMBRAL_LOTE_BAJO_KG) # Alerta de lote bajo

        # --- Query para Clientes con Saldo Pendiente ---
        # Subquery para sumar el total de ventas con estado pendiente o parcial por cliente
        total_ventas_sq_base = db.session.query(
            Venta.cliente_id,
            func.sum(Venta.total).label('total_adeudado')
        ).filter(Venta.estado_pago.in_(['pendiente', 'parcial']))

        # Subquery para sumar todos los pagos asociados a esas ventas
        total_pagos_sq_base = db.session.query(
            Venta.cliente_id,
            func.sum(Pago.monto).label('total_pagado')
        ).join(Venta, Pago.venta_id == Venta.id)\
         .filter(Venta.estado_pago.in_(['pendiente', 'parcial']))

        # --- Aplicar Filtro de Almacén si no es Admin/Gerente ---
        if not is_admin_or_gerente:
            if not user_almacen_id:
                return {"error": "Usuario sin almacén asignado"}, 403
            # Aplicar filtro a las queries que tienen relación directa con almacén
            inventario_query = inventario_query.filter(Inventario.almacen_id == user_almacen_id)

            # Aplicar filtro de almacén a las subqueries de saldo
            total_ventas_sq_base = total_ventas_sq_base.filter(Venta.almacen_id == user_almacen_id)
            total_pagos_sq_base = total_pagos_sq_base.filter(Venta.almacen_id == user_almacen_id)
        
        # Agrupar y crear las subqueries finales
        total_ventas_sq = total_ventas_sq_base.group_by(Venta.cliente_id).subquery()
        total_pagos_sq = total_pagos_sq_base.group_by(Venta.cliente_id).subquery()
        
        # Query principal de clientes que une las subqueries
        clientes_query = db.session.query(
            Cliente.id.label('cliente_id'),
            Cliente.nombre.label('cliente_nombre'),
            (func.coalesce(total_ventas_sq.c.total_adeudado, 0) - func.coalesce(total_pagos_sq.c.total_pagado, 0)).label('saldo_pendiente')
        ).select_from(Cliente)\
         .join(total_ventas_sq, Cliente.id == total_ventas_sq.c.cliente_id)\
         .outerjoin(total_pagos_sq, Cliente.id == total_pagos_sq.c.cliente_id)\
         .filter((func.coalesce(total_ventas_sq.c.total_adeudado, 0) - func.coalesce(total_pagos_sq.c.total_pagado, 0)) > 0.009) # Filtrar por saldo > 0

        # La query de lotes (lotes_query) no se filtra por almacén aquí.

        # --- Ejecutar Queries y Formatear Resultados ---
        try:
            # Alertas de stock bajo (siempre se calculan)
            stock_bajo_items = inventario_query.order_by(Almacen.nombre, PresentacionProducto.nombre).all()
            stock_bajo_data = [
                {
                    "presentacion_id": item.presentacion_id,
                    "nombre": item.presentacion_nombre,
                    "cantidad": item.cantidad,
                    "stock_minimo": item.stock_minimo,
                    "almacen_id": item.almacen_id,
                    "almacen_nombre": item.almacen_nombre
                } for item in stock_bajo_items
            ]

            # Alertas de lotes bajos (siempre se calculan)
            lotes_bajos_items = lotes_query.order_by(Lote.cantidad_disponible_kg).all()
            lotes_alerta_data = [
                {
                    "lote_id": item.lote_id,
                    "descripcion": item.lote_descripcion,
                    "cantidad_disponible_kg": float(item.cantidad_disponible_kg or 0),
                    "producto_id": item.producto_id
                    # Añadir más detalles si es necesario
                } for item in lotes_bajos_items
            ]

            # Clientes con saldo pendiente (ahora con cálculo incluido)
            clientes_con_saldo_items = clientes_query.order_by(Cliente.nombre).all()
            clientes_saldo_data = [
                {
                    "cliente_id": c.cliente_id,
                    "nombre": c.cliente_nombre,
                    "saldo_pendiente_total": float(c.saldo_pendiente or 0)
                } for c in clientes_con_saldo_items
            ]

            # --- Ensamblar Respuesta Final ---
            dashboard_data = {
                # Ya no se incluye 'periodo', 'ventas_por_dia', 'pedidos_programados_por_dia'
                "alertas_stock_bajo": stock_bajo_data,
                "alertas_lotes_bajos": lotes_alerta_data,
                "clientes_con_saldo_pendiente": clientes_saldo_data
            }

            return dashboard_data, 200

        except Exception as e:
            logger.exception(f"Error al ejecutar queries del dashboard de alertas: {e}")
            return {"error": "Error al obtener datos para el dashboard de alertas", "details": str(e)}, 500