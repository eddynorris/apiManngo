from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from models import Venta, VentaDetalle, Pago, Gasto, Movimiento, Inventario, Cliente
from extensions import db
from common import handle_db_errors, parse_iso_datetime
from decimal import Decimal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class TransaccionCompletaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """
        Recibe un JSON confirmado con datos de Venta, Pagos y Gasto.
        Ejecuta todo en una sola transacción atómica.
        """
        data = request.get_json()
        claims = get_jwt()
        usuario_id = claims.get('sub')
        almacen_id = claims.get('almacen_id')
        
        if not almacen_id:
             return {"error": "Usuario no tiene almacén asignado."}, 403

        # Obtener fecha personalizada o usar fecha actual
        fecha_transaccion = data.get('fecha')
        if fecha_transaccion:
            try:
                fecha_transaccion = parse_iso_datetime(fecha_transaccion)
            except Exception as e:
                logger.warning(f"Fecha inválida recibida: {fecha_transaccion}, usando datetime.now()")
                fecha_transaccion = datetime.now()
        else:
            fecha_transaccion = datetime.now()

        # --- 1. Validaciones Básicas ---
        cliente_data = data.get('cliente')
        items = data.get('items', [])
        pagos = data.get('pagos', [])
        gasto_data = data.get('gasto_asociado')

        if not cliente_data or not cliente_data.get('id'):
            return {"error": "Se requiere un cliente válido (ID)."}, 400
        if not items:
            return {"error": "Se requiere al menos un producto."}, 400

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
            for pago_info in pagos:
                monto = Decimal(str(pago_info.get('monto', 0)))
                metodo = pago_info.get('metodo_pago', 'efectivo')
                es_deposito = pago_info.get('es_deposito', False)
                
                if monto > 0:
                    nuevo_pago = Pago(
                        venta_id=nueva_venta.id,
                        usuario_id=usuario_id,
                        monto=monto,
                        metodo_pago=metodo,
                        fecha=fecha_transaccion,
                        depositado=es_deposito,
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
                nuevo_gasto = Gasto(
                    descripcion=gasto_data.get('descripcion'),
                    monto=Decimal(str(gasto_data.get('monto', 0))),
                    categoria=gasto_data.get('categoria'),
                    fecha=fecha_transaccion.date(),
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
