from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Venta, Pedido, Inventario, Cliente, PresentacionProducto, Almacen, Lote, Pago
from extensions import db
from common import handle_db_errors, rol_requerido
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, case
from sqlalchemy.orm import subqueryload, joinedload
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class DashboardResource(Resource):
    @jwt_required()
    @rol_requerido('admin')
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

        # --- NUEVA QUERY ÚNICA PARA CLIENTES CON SALDO PENDIENTE ---
        # 1. Obtener todas las ventas pendientes o parciales, cargando eficientemente
        #    el cliente y los pagos asociados para evitar el problema N+1.
        ventas_pendientes_query = Venta.query\
            .options(
                joinedload(Venta.cliente),  # Usamos joinedload para cargar el cliente
                subqueryload(Venta.pagos)  # y subqueryload para los pagos
            )\
            .filter(Venta.estado_pago.in_(['pendiente', 'parcial']))


        # --- Aplicar Filtro de Almacén si no es Admin/Gerente ---
        if not is_admin_or_gerente:
            if not user_almacen_id:
                return {"error": "Usuario sin almacén asignado"}, 403
            # Aplicar filtro a las queries que tienen relación directa con almacén
            inventario_query = inventario_query.filter(Inventario.almacen_id == user_almacen_id)
            ventas_pendientes_query = ventas_pendientes_query.filter(Venta.almacen_id == user_almacen_id)
        
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

            # --- Procesar y Agrupar los resultados de la nueva query de ventas ---
            ventas_con_deuda = ventas_pendientes_query.order_by(Venta.fecha.asc()).all()
            clientes_con_saldo_map = {} # Usamos un mapa para agrupar por cliente_id

            for venta in ventas_con_deuda:
                cliente = venta.cliente
                if not cliente:
                    continue # Omitir ventas sin cliente asignado (si es posible)

                # Calcular saldo de esta venta específica
                total_pagado_venta = sum(p.monto for p in venta.pagos)
                saldo_pendiente_venta = venta.total - total_pagado_venta

                # Si el saldo de esta venta es cero o negativo, no la incluimos
                if saldo_pendiente_venta <= 0:
                    continue

                # Si es la primera vez que vemos a este cliente, lo inicializamos
                if cliente.id not in clientes_con_saldo_map:
                    clientes_con_saldo_map[cliente.id] = {
                        "cliente_id": cliente.id,
                        "nombre": cliente.nombre,
                        "saldo_pendiente_total": Decimal('0'),
                        "ventas_pendientes": []
                    }
                
                # Formatear los pagos de esta venta
                pagos_data = [
                    {
                        "pago_id": p.id,
                        "fecha": p.fecha.isoformat() if p.fecha else None,
                        "monto": float(p.monto or 0),
                        "metodo_pago": p.metodo_pago,
                        "referencia": p.referencia
                    } for p in venta.pagos
                ]

                # Formatear la venta y añadirla a la lista del cliente
                venta_data = {
                    "venta_id": venta.id,
                    "fecha": venta.fecha.isoformat() if venta.fecha else None,
                    "total_venta": float(venta.total or 0),
                    "estado_pago": venta.estado_pago,
                    "saldo_pendiente_venta": float(saldo_pendiente_venta),
                    "pagos": pagos_data
                }
                clientes_con_saldo_map[cliente.id]["ventas_pendientes"].append(venta_data)
                
                # Acumular el saldo total del cliente
                clientes_con_saldo_map[cliente.id]["saldo_pendiente_total"] += saldo_pendiente_venta

            # Convertir el mapa a una lista final y convertir el Decimal a float para JSON
            clientes_saldo_data = list(clientes_con_saldo_map.values())
            for cliente_data in clientes_saldo_data:
                cliente_data["saldo_pendiente_total"] = float(cliente_data["saldo_pendiente_total"])


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