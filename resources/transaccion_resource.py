import logging
from datetime import UTC
from decimal import Decimal, InvalidOperation

from flask import request
from flask_jwt_extended import get_jwt, jwt_required
from flask_restful import Resource

from common import handle_db_errors
from extensions import db
from models import Gasto, Inventario, Movimiento, Pago, Venta, VentaDetalle
from services.authorization import autorizar_almacen
from services.money import monto_decimal_positivo
from utils.date_parser import parse_fecha_operacion
from utils.date_utils import get_peru_now, to_peru_time

logger = logging.getLogger(__name__)

_FECHA_AUSENTE = object()

class TransaccionCompletaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """
        Recibe un JSON confirmado con datos de Venta, Pagos y Gasto.
        Ejecuta todo en una sola transacción atómica.
        """
        data = request.get_json(silent=True) or {}
        claims = get_jwt()
        usuario_id = claims.get('sub')
        almacen_id = claims.get('almacen_id')

        if not almacen_id:
             return {"error": "Usuario no tiene almacén asignado."}, 403

        # Resolver el actor vigente en BD y validar que el almacén del claim
        # realmente le corresponde antes de tocar stock.
        actor = autorizar_almacen(usuario_id, almacen_id)
        usuario_id = actor.id

        # Una fecha ausente puede usar el instante actual; una fecha explícita
        # (incluido null o cadena vacía) debe ser válida o rechazarse sin efectos.
        fecha_raw = data.get('fecha', _FECHA_AUSENTE)
        if fecha_raw is _FECHA_AUSENTE:
            fecha_transaccion = get_peru_now().astimezone(UTC)
        else:
            try:
                fecha_transaccion = parse_fecha_operacion(fecha_raw)
            except ValueError as error:
                return {"error": str(error)}, 400

        # --- 1. Validaciones Básicas ---
        cliente_data = data.get('cliente')
        items = data.get('items', [])
        pagos = data.get('pagos', [])
        gasto_data = data.get('gasto_asociado')

        if not cliente_data or not cliente_data.get('id'):
            return {"error": "Se requiere un cliente válido (ID)."}, 400
        if not items:
            return {"error": "Se requiere al menos un producto."}, 400
        if not isinstance(pagos, list):
            return {"error": "El campo 'pagos' debe ser una lista."}, 400

        pagos_validados = []
        for pago_info in pagos:
            if not isinstance(pago_info, dict):
                return {"error": "Cada pago debe ser un objeto."}, 400
            try:
                monto = monto_decimal_positivo(pago_info.get('monto'), 'monto')
            except ValueError as error:
                return {"error": str(error)}, 400
            metodo = str(pago_info.get('metodo_pago') or 'efectivo').lower()
            pagos_validados.append({
                'monto': monto,
                'metodo_pago': metodo,
                'es_deposito': bool(pago_info.get('es_deposito', False)),
            })

        try:
            # --- 2. Crear Venta ---
            total_venta = Decimal(0)
            detalles_venta = []

            for item in items:
                prod_id = item.get('producto_id')
                cantidad = item.get('cantidad')
                precio = Decimal(str(item.get('precio_unitario', 0)))
                lote_id = item.get('lote_id')

                if not prod_id or not lote_id:
                     return {"error": f"Faltan datos (ID o Lote) para el producto: {item.get('producto_nombre_buscado')}"}, 400

                # Verificar stock nuevamente (seguridad)
                inventario = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=prod_id, lote_id=lote_id).with_for_update().first()
                if not inventario or inventario.cantidad < cantidad:
                     raise ValueError(f"Stock insuficiente para producto ID {prod_id} durante la transacción.")

                # Deducir Stock
                inventario.cantidad -= cantidad

                detalle = VentaDetalle(
                    presentacion_id=prod_id,
                    cantidad=cantidad,
                    precio_unitario=precio,
                    lote_id=lote_id
                )
                detalles_venta.append(detalle)
                total_venta += (cantidad * precio)

            total_pagos_validados = sum(
                (pago['monto'] for pago in pagos_validados), Decimal('0')
            )
            if total_pagos_validados > total_venta + Decimal('0.001'):
                db.session.rollback()
                return {
                    "error": "El total de pagos no puede exceder el total de la venta."
                }, 400

            nueva_venta = Venta(
                cliente_id=cliente_data['id'],
                almacen_id=almacen_id,
                vendedor_id=usuario_id,
                total=total_venta,
                tipo_pago='contado', # Se ajustará según los pagos
                fecha=fecha_transaccion,
                detalles=detalles_venta
            )
            db.session.add(nueva_venta)
            db.session.flush() # Para obtener ID de venta

            # --- 3. Registrar Movimientos de Salida ---
            for detalle in nueva_venta.detalles:
                movimiento = Movimiento(
                    tipo='salida',
                    presentacion_id=detalle.presentacion_id,
                    lote_id=detalle.lote_id,
                    cantidad=detalle.cantidad,
                    usuario_id=usuario_id,
                    motivo=f"Venta ID: {nueva_venta.id} (Voz)"
                )
                db.session.add(movimiento)

            # --- 4. Registrar Pagos ---
            total_pagado = Decimal(0)
            for pago_info in pagos_validados:
                monto = pago_info['monto']
                nuevo_pago = Pago(
                    venta_id=nueva_venta.id,
                    usuario_id=usuario_id,
                    monto=monto,
                    metodo_pago=pago_info['metodo_pago'],
                    fecha=fecha_transaccion,
                    depositado=pago_info['es_deposito'],
                    referencia="Pago voz"
                )
                db.session.add(nuevo_pago)
                total_pagado += monto

            # Actualizar estado de pago de la venta
            if total_pagado >= total_venta:
                nueva_venta.estado_pago = 'pagado'
            elif total_pagado > 0:
                nueva_venta.estado_pago = 'parcial'
            else:
                nueva_venta.estado_pago = 'pendiente'

            if total_pagado < total_venta:
                nueva_venta.tipo_pago = 'credito'

            # --- 5. Registrar Gasto (si existe) ---
            if gasto_data:
                try:
                    monto_gasto = Decimal(str(gasto_data.get('monto', 0)))
                except (InvalidOperation, TypeError, ValueError):
                    db.session.rollback()
                    return {"error": "Monto de gasto inválido."}, 400
                if not monto_gasto.is_finite() or monto_gasto < 0:
                    db.session.rollback()
                    return {"error": "El monto de gasto debe ser finito y no negativo."}, 400
                nuevo_gasto = Gasto(
                    descripcion=gasto_data.get('descripcion'),
                    monto=monto_gasto,
                    categoria=gasto_data.get('categoria'),
                    fecha=to_peru_time(fecha_transaccion).date(),
                    usuario_id=usuario_id,
                    almacen_id=almacen_id
                )
                db.session.add(nuevo_gasto)

            db.session.commit()

            return {
                "message": "Transacción completada exitosamente.",
                "venta_id": nueva_venta.id,
                "total_venta": float(total_venta),
                "total_pagado": float(total_pagado)
            }, 201

        except ValueError as ve:
            db.session.rollback()
            return {"error": str(ve)}, 400
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en TransaccionCompleta: {e}", exc_info=True)
            return {"error": "Error interno al procesar la transacción."}, 500
