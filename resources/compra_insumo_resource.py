from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Inventario, PresentacionProducto, Almacen, Movimiento, Gasto, Proveedor
from extensions import db
from common import handle_db_errors, rol_requerido
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class InsumoAlertaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Retorna todos los insumos cuyo stock es menor o igual al stock mínimo.
        Incluye nombre del producto, almacén, stock actual, stock mínimo y porcentaje restante.
        
        Filtros opcionales:
        - almacen_id: Filtrar por almacén específico
        """
        try:
            claims = get_jwt()
            rol = claims.get('rol')
            almacen_id_jwt = claims.get('almacen_id')

            query = db.session.query(Inventario).join(
                PresentacionProducto, Inventario.presentacion_id == PresentacionProducto.id
            ).join(
                Almacen, Inventario.almacen_id == Almacen.id
            ).filter(
                PresentacionProducto.tipo == 'insumo',
                Inventario.cantidad <= Inventario.stock_minimo
            )

            # Restricción por almacén para usuarios no-admin
            if rol != 'admin':
                if not almacen_id_jwt:
                    return {"error": "Usuario sin almacén asignado"}, 400
                query = query.filter(Inventario.almacen_id == almacen_id_jwt)

            # Filtro adicional por almacén (admins pueden filtrar)
            if almacen_id_param := request.args.get('almacen_id'):
                try:
                    query = query.filter(Inventario.almacen_id == int(almacen_id_param))
                except ValueError:
                    return {"error": "ID de almacén inválido"}, 400

            inventarios = query.order_by(Inventario.cantidad.asc()).all()

            alertas = []
            for inv in inventarios:
                pres = inv.presentacion
                alm = inv.almacen
                stock_actual = float(inv.cantidad)
                stock_minimo = float(inv.stock_minimo)
                porcentaje = round((stock_actual / stock_minimo * 100), 1) if stock_minimo > 0 else 0

                alertas.append({
                    "inventario_id": inv.id,
                    "presentacion_id": pres.id,
                    "nombre_insumo": pres.nombre,
                    "almacen_id": alm.id,
                    "nombre_almacen": alm.nombre,
                    "stock_actual": stock_actual,
                    "stock_minimo": stock_minimo,
                    "porcentaje_stock": porcentaje,
                    "critico": porcentaje <= 25,  # Marcado como crítico si queda 25% o menos
                    "precio_unitario": float(pres.precio_venta) if pres.precio_venta else 0,
                })

            return {
                "total_alertas": len(alertas),
                "alertas": alertas
            }, 200

        except Exception as e:
            logger.error(f"Error en InsumoAlertaResource: {str(e)}", exc_info=True)
            return {"error": "Error al obtener alertas de insumos"}, 500


class CompraInsumoResource(Resource):
    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def post(self):
        """
        Registra una compra de insumos en un solo paso atómico.
        
        Realiza en una sola transacción:
        1. Valida que la presentación sea de tipo 'insumo'
        2. Actualiza o crea el registro de Inventario (sin lote)
        3. Registra un Movimiento de tipo 'entrada' / tipo_operacion 'compra'  
        4. Registra un Gasto con categoría 'insumos'

        Body esperado:
        {
            "presentacion_id": 3,        -- REQUERIDO: ID de la presentación (tipo insumo)
            "almacen_id": 1,             -- REQUERIDO
            "cantidad": 500,             -- REQUERIDO: Cuántas unidades se compraron
            "costo_total": 200.00,       -- REQUERIDO: Costo total de la compra (para Gasto)
            "proveedor_id": 1,           -- OPCIONAL: ID del proveedor
            "descripcion": "...",        -- OPCIONAL: Descripción adicional
            "fecha": "2026-03-11"        -- OPCIONAL: Fecha de compra (default: hoy)
        }
        """
        data = request.get_json()
        if not data:
            return {"error": "No se proporcionaron datos"}, 400

        # --- Validación de campos requeridos ---
        required_fields = ['presentacion_id', 'almacen_id', 'cantidad', 'costo_total']
        for field in required_fields:
            if field not in data:
                return {"error": f"El campo '{field}' es requerido"}, 400

        try:
            presentacion_id = int(data['presentacion_id'])
            almacen_id = int(data['almacen_id'])
            cantidad = Decimal(str(data['cantidad']))
            costo_total = Decimal(str(data['costo_total']))

            if cantidad <= 0:
                return {"error": "La cantidad debe ser mayor a cero"}, 400
            if costo_total < 0:
                return {"error": "El costo total no puede ser negativo"}, 400

        except (ValueError, TypeError, InvalidOperation):
            return {"error": "Valores numéricos inválidos en el payload"}, 400

        # --- Validar entidades relacionadas ---
        presentacion = PresentacionProducto.query.get(presentacion_id)
        if not presentacion:
            return {"error": f"Presentación con ID {presentacion_id} no encontrada"}, 404

        if presentacion.tipo != 'insumo':
            return {
                "error": f"La presentación '{presentacion.nombre}' es de tipo '{presentacion.tipo}', no 'insumo'. "
                         f"Solo se pueden comprar insumos a través de este endpoint."
            }, 400

        almacen = Almacen.query.get(almacen_id)
        if not almacen:
            return {"error": f"Almacén con ID {almacen_id} no encontrado"}, 404

        proveedor_id = data.get('proveedor_id')
        proveedor_nombre = "No especificado"
        if proveedor_id:
            proveedor = Proveedor.query.get(proveedor_id)
            if not proveedor:
                return {"error": f"Proveedor con ID {proveedor_id} no encontrado"}, 404
            proveedor_nombre = proveedor.nombre

        # --- Fecha de la operación ---
        fecha_str = data.get('fecha')
        if fecha_str:
            try:
                fecha_operacion = datetime.strptime(fecha_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                return {"error": "Formato de fecha inválido. Usar YYYY-MM-DD"}, 400
        else:
            fecha_operacion = datetime.now(timezone.utc)

        claims = get_jwt()
        usuario_id = claims.get('sub')

        descripcion_base = data.get('descripcion') or f"Compra de {cantidad} {presentacion.nombre}"
        descripcion_completa = f"{descripcion_base} | Proveedor: {proveedor_nombre}"
        motivo_mov = f"Compra de insumos: {presentacion.nombre} | Proveedor: {proveedor_nombre}"

        try:
            # 1. Actualizar o crear el registro de Inventario (siempre lote_id=None para insumos)
            inventario = Inventario.query.filter_by(
                presentacion_id=presentacion_id,
                almacen_id=almacen_id,
                lote_id=None
            ).first()

            if inventario:
                inventario.cantidad += cantidad
                accion_inventario = "actualizado"
            else:
                inventario = Inventario(
                    presentacion_id=presentacion_id,
                    almacen_id=almacen_id,
                    lote_id=None,
                    cantidad=cantidad,
                    stock_minimo=10  # Default razonable
                )
                db.session.add(inventario)
                accion_inventario = "creado"

            # 2. Registrar Movimiento
            movimiento = Movimiento(
                tipo='entrada',
                presentacion_id=presentacion_id,
                lote_id=None,
                cantidad=cantidad,
                usuario_id=usuario_id,
                motivo=motivo_mov,
                tipo_operacion='compra',
                fecha=fecha_operacion,
            )
            db.session.add(movimiento)

            # 3. Registrar Gasto financiero
            gasto = Gasto(
                descripcion=descripcion_completa,
                monto=costo_total,
                fecha=fecha_operacion.date(),
                categoria='insumos',
                almacen_id=almacen_id,
                lote_id=None,  # No hay lote para insumos
                usuario_id=usuario_id,
            )
            db.session.add(gasto)

            db.session.commit()

            logger.info(
                f"Compra de insumo registrada: {cantidad} x {presentacion.nombre} "
                f"en almacén {almacen.nombre} por S/ {costo_total}"
            )

            return {
                "mensaje": "Compra de insumo registrada exitosamente",
                "resumen": {
                    "insumo": presentacion.nombre,
                    "almacen": almacen.nombre,
                    "proveedor": proveedor_nombre,
                    "cantidad_comprada": float(cantidad),
                    "nuevo_stock": float(inventario.cantidad),
                    "costo_total": float(costo_total),
                    "inventario": accion_inventario,
                    "fecha": fecha_operacion.date().isoformat(),
                }
            }, 201

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en CompraInsumoResource: {str(e)}", exc_info=True)
            return {"error": "Error interno al registrar la compra de insumo", "detalle": str(e)}, 500
