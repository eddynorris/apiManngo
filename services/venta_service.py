# services/venta_service.py
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from werkzeug.exceptions import Conflict

from extensions import db
from models import Cliente, Gasto, Movimiento, Pago, PresentacionProducto, Venta, VentaDetalle
from services.authorization import (
    autorizar_almacen,
    autorizar_venta,
    es_admin,
    normalizar_ids,
    requerir_encontrados,
)
from services.pago_service import PagoService
from services.stock_service import StockInsuficienteError, StockService


class VentaService:
    @staticmethod
    def crear_venta(vendedor_id: int, cliente_id: int, almacen_id: int, detalles_data: list[dict],
                    estado: str = 'completado', fecha: datetime = None, monto_pago: Decimal = Decimal('0'),
                    metodo_pago: str = 'efectivo', monto_gasto: Decimal = Decimal('0'),
                    file_comprobante=None, permitir_stock_negativo: bool = False,
                    estado_pago: str = None, actor_id: int = None) -> Venta:
        """
        Crea una venta, valida y descuenta stock usando el criterio FIFO,
        registra los movimientos de inventario y opcionalmente el pago y gasto asociado.

        ``actor_id`` es opcional para no romper llamadas internas legítimas
        (Telegram). Cuando se proporciona, se autoriza contra el usuario vigente
        en BD antes de tocar stock.
        """
        if actor_id is not None:
            actor = autorizar_almacen(actor_id, almacen_id)
            if not es_admin(actor) and str(vendedor_id) != str(actor.id):
                from werkzeug.exceptions import Forbidden
                raise Forbidden("No puede registrar ventas a nombre de otro usuario")

        try:
            monto_pago = Decimal(str(monto_pago if monto_pago is not None else 0))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("Monto de pago inicial inválido") from None
        if not monto_pago.is_finite() or monto_pago < 0:
            raise ValueError("El monto de pago inicial debe ser finito y no negativo")

        try:
            monto_gasto = Decimal(str(monto_gasto if monto_gasto is not None else 0))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("Monto de gasto inválido") from None
        if not monto_gasto.is_finite() or monto_gasto < 0:
            raise ValueError("El monto de gasto debe ser finito y no negativo")

        detalles_data = normalizar_ids(detalles_data) if isinstance(detalles_data, list) else detalles_data

        if not detalles_data:
            raise ValueError("La venta debe tener al menos un detalle")

        estado = (estado or 'completado').lower()
        if estado not in ['pedido', 'completado']:
            raise ValueError("Estado inválido. Opciones: pedido, completado")

        cliente = db.session.get(Cliente, cliente_id)
        if not cliente:
            raise ValueError("Cliente no encontrado")

        fecha = fecha or datetime.now(UTC)
        total = Decimal('0')
        detalles_para_venta = []

        if estado == 'pedido':
            for detalle_data in detalles_data:
                presentacion_id = int(detalle_data.get('presentacion_id'))
                cantidad_solicitada = Decimal(str(detalle_data.get('cantidad')))
                if not all([presentacion_id, cantidad_solicitada]):
                    raise ValueError("Cada detalle debe incluir presentacion_id y cantidad")

                pres = db.session.get(PresentacionProducto, presentacion_id)
                if not pres:
                    raise ValueError(f"Presentación ID {presentacion_id} no encontrada")

                precio_unitario = Decimal(str(detalle_data.get('precio_unitario') or pres.precio_venta))

                nuevo_detalle = VentaDetalle(
                    presentacion_id=presentacion_id,
                    cantidad=cantidad_solicitada,
                    precio_unitario=precio_unitario,
                    lote_id=None
                )
                detalles_para_venta.append(nuevo_detalle)
                total += cantidad_solicitada * precio_unitario
        else:
            # Completo: Carga y bloquea todos los inventarios requeridos en una sola consulta
            presentacion_ids = list({int(d.get('presentacion_id')) for d in detalles_data})
            invs_dict = StockService.bloquear_y_obtener_inventarios(almacen_id, presentacion_ids)

            for detalle_data in detalles_data:
                presentacion_id = int(detalle_data.get('presentacion_id'))
                cantidad_solicitada = Decimal(str(detalle_data.get('cantidad')))

                if not all([presentacion_id, cantidad_solicitada]):
                    raise ValueError("Cada detalle debe incluir presentacion_id y cantidad")

                invs_disponibles = invs_dict.get(presentacion_id, [])
                stock_total = sum(inv.cantidad for inv in invs_disponibles)

                if not invs_disponibles and not permitir_stock_negativo:
                    raise ValueError(f"No se encontró inventario para presentación ID {presentacion_id} en este almacén.")

                if stock_total < cantidad_solicitada and not permitir_stock_negativo:
                    nombre = invs_disponibles[0].presentacion.nombre if invs_disponibles else None
                    if not nombre:
                        pres = db.session.get(PresentacionProducto, presentacion_id)
                        nombre = pres.nombre if pres else str(presentacion_id)
                    raise StockInsuficienteError(presentacion_id, cantidad_solicitada, stock_total, presentacion_nombre=nombre)

                # Precio por unidad
                pres = db.session.get(PresentacionProducto, presentacion_id)
                if not pres:
                    raise ValueError(f"Presentación ID {presentacion_id} no encontrada")
                precio_unitario = Decimal(str(
                    detalle_data.get('precio_unitario') or pres.precio_venta
                ))

                # Descontar FIFO usando el servicio común
                consumos = StockService.descontar_fifo(
                    almacen_id=almacen_id,
                    presentacion_id=presentacion_id,
                    cantidad=cantidad_solicitada,
                    permitir_negativo=permitir_stock_negativo,
                    invs_disponibles=invs_disponibles
                )

                for consumo in consumos:
                    nuevo_detalle = VentaDetalle(
                        presentacion_id=presentacion_id,
                        cantidad=consumo.cantidad,
                        precio_unitario=precio_unitario,
                        lote_id=consumo.lote_id
                    )
                    detalles_para_venta.append(nuevo_detalle)
                    total += consumo.cantidad * precio_unitario

        # Derivar tipo de pago
        tipo_pago_derivado = 'contado' if monto_pago > 0 else 'credito'
        fecha_pedido_val = datetime.now(UTC) if estado == 'pedido' else None
        fecha_entrega_val = fecha if estado == 'pedido' else None

        nueva_venta = Venta(
            cliente_id=cliente_id,
            almacen_id=almacen_id,
            vendedor_id=vendedor_id,
            total=total,
            tipo_pago=tipo_pago_derivado,
            fecha=fecha if estado == 'completado' else None,
            estado=estado,
            fecha_pedido=fecha_pedido_val,
            fecha_entrega=fecha_entrega_val,
            detalles=detalles_para_venta
        )

        db.session.add(nueva_venta)
        db.session.flush()

        if estado == 'completado':
            for detalle in nueva_venta.detalles:
                movimiento = Movimiento(
                    tipo='salida',
                    presentacion_id=detalle.presentacion_id,
                    lote_id=detalle.lote_id,
                    cantidad=detalle.cantidad,
                    usuario_id=vendedor_id,
                    motivo=f"Venta ID: {nueva_venta.id} - Cliente: {cliente.nombre}",
                    tipo_operacion='venta',
                    venta_id=nueva_venta.id
                )
                db.session.add(movimiento)

        # Registrar pago si corresponde
        if monto_pago == 0 and estado_pago == 'pagado':
            monto_pago = total

        if monto_pago > total + Decimal('0.001'):
            raise ValueError("El monto de pago inicial no puede exceder el total de la venta")

        if monto_pago > 0:
            monto_a_registrar = monto_pago
            pago_instancia = Pago(
                venta_id=nueva_venta.id,
                monto=monto_a_registrar,
                metodo_pago=metodo_pago,
                fecha=fecha
            )
            PagoService.create_pago(pago_instancia, file_comprobante, vendedor_id)
        else:
            nueva_venta.actualizar_estado()

        # Registrar gasto asociado si corresponde
        if monto_gasto > 0:
            nuevo_gasto = Gasto(
                monto=monto_gasto,
                descripcion=f"Gasto asociado a venta #{nueva_venta.id}",
                almacen_id=almacen_id,
                usuario_id=vendedor_id,
                fecha=fecha.date() if hasattr(fecha, 'date') else fecha,
                categoria='logistica'
            )
            db.session.add(nuevo_gasto)

        return nueva_venta

    @staticmethod
    def actualizar_venta(venta_id: int, vendedor_id: int, data: dict, file_comprobante=None,
                         actor_id: int = None) -> Venta:
        """
        Actualiza una venta existente. Revierte el stock FIFO anterior y vuelve a descontar.
        Autoriza la venta real y el nuevo almacén (si cambia) antes de revertir stock.
        """
        data = normalizar_ids(data)
        venta = Venta.query.options(db.joinedload(Venta.detalles)).get_or_404(venta_id)

        # Autorizar el objeto real antes de cualquier reversión de stock.
        if actor_id is not None:
            autorizar_venta(actor_id, venta)

        target_almacen_id = data.get('almacen_id', venta.almacen_id)
        if actor_id is not None and str(target_almacen_id) != str(venta.almacen_id):
            autorizar_almacen(actor_id, target_almacen_id)

        # Bloquear la fila de la venta
        db.session.query(Venta).filter_by(id=venta_id).with_for_update().first()

        nuevos_detalles_data = data.get('detalles', [])
        if not nuevos_detalles_data:
            raise ValueError("La venta debe tener al menos un detalle")

        was_completado = (venta.estado == 'completado')
        is_completado = (data.get('estado', venta.estado) == 'completado')

        # 1. Revertir el estado anterior si estaba completado
        if was_completado:
            StockService.revertir_venta(venta)
            # Limpiar detalles anteriores físicamente de la venta
            for d in list(venta.detalles):
                db.session.delete(d)
            db.session.flush()

        # 2. Procesar y aplicar el nuevo estado
        nuevo_total = Decimal('0')
        nuevos_detalles_obj = []

        if is_completado:
            # Lógica FIFO para descontar stock con bloqueo pesimista
            presentacion_ids = list({int(d.get('presentacion_id')) for d in nuevos_detalles_data})
            invs_dict = StockService.bloquear_y_obtener_inventarios(target_almacen_id, presentacion_ids)

            for detalle_data in nuevos_detalles_data:
                presentacion_id = int(detalle_data.get('presentacion_id'))
                cantidad_solicitada = Decimal(str(detalle_data.get('cantidad')))
                precio_unitario = Decimal(str(detalle_data.get('precio_unitario')))

                invs_disponibles = invs_dict.get(presentacion_id, [])
                stock_total = sum(inv.cantidad for inv in invs_disponibles)

                if stock_total < cantidad_solicitada:
                    raise StockInsuficienteError(presentacion_id, cantidad_solicitada, stock_total)

                # Descontar de inventarios FIFO
                consumos = StockService.descontar_fifo(
                    almacen_id=target_almacen_id,
                    presentacion_id=presentacion_id,
                    cantidad=cantidad_solicitada,
                    invs_disponibles=invs_disponibles
                )

                for consumo in consumos:
                    detalle_obj = VentaDetalle(
                        presentacion_id=presentacion_id,
                        cantidad=consumo.cantidad,
                        precio_unitario=precio_unitario,
                        lote_id=consumo.lote_id
                    )
                    nuevos_detalles_obj.append(detalle_obj)
                    nuevo_total += consumo.cantidad * precio_unitario
        else:
            # Pedido: No se descuenta stock
            for detalle_data in nuevos_detalles_data:
                presentacion_id = int(detalle_data.get('presentacion_id'))
                cantidad_solicitada = Decimal(str(detalle_data.get('cantidad')))

                pres = db.session.get(PresentacionProducto, presentacion_id)
                if not pres:
                    raise ValueError(f"Presentación ID {presentacion_id} no encontrada")
                precio_unitario = Decimal(str(detalle_data.get('precio_unitario') or pres.precio_venta))

                detalle_obj = VentaDetalle(
                    presentacion_id=presentacion_id,
                    cantidad=cantidad_solicitada,
                    precio_unitario=precio_unitario,
                    lote_id=None
                )
                nuevos_detalles_obj.append(detalle_obj)
                nuevo_total += cantidad_solicitada * precio_unitario

        total_pagado = Decimal(str(
            db.session.query(func.coalesce(func.sum(Pago.monto), 0))
            .filter(Pago.venta_id == venta.id)
            .scalar() or 0
        ))
        if total_pagado > nuevo_total + Decimal('0.001'):
            raise Conflict(
                "El nuevo total de la venta no puede ser menor que los pagos ya registrados; "
                "se requiere un flujo explícito de devolución."
            )

        # 3. Actualizar la venta
        is_transition_to_completed = (venta.estado == 'pedido' and is_completado)
        venta.cliente_id = data.get('cliente_id', venta.cliente_id)
        venta.almacen_id = target_almacen_id
        venta.estado = data.get('estado', venta.estado)
        venta.estado_pago = data.get('estado_pago', venta.estado_pago)

        if data.get('fecha'):
            from common import parse_iso_datetime
            venta.fecha = parse_iso_datetime(data.get('fecha'))
        elif is_transition_to_completed or (is_completado and not venta.fecha):
            venta.fecha = datetime.now(UTC)

        # Asignar los nuevos detalles
        venta.detalles = nuevos_detalles_obj
        venta.total = nuevo_total

        # Re-crear los movimientos si está completado
        if is_completado:
            cliente_nombre = db.session.get(Cliente, venta.cliente_id).nombre
            for detalle in venta.detalles:
                movimiento = Movimiento(
                    tipo='salida',
                    presentacion_id=detalle.presentacion_id,
                    lote_id=detalle.lote_id,
                    cantidad=detalle.cantidad,
                    usuario_id=vendedor_id,
                    motivo=f"Venta ID: {venta.id} - Cliente: {cliente_nombre} (Actualizada)",
                    tipo_operacion='venta',
                    venta_id=venta.id
                )
                db.session.add(movimiento)

        # Registrar pago automático si se actualiza a 'pagado'
        if data.get('estado_pago') == 'pagado':
            saldo_pendiente = venta.saldo_pendiente
            if saldo_pendiente > 0:
                metodo_pago = data.get('metodo_pago', 'efectivo')
                pago_instancia = Pago(
                    venta_id=venta.id,
                    monto=saldo_pendiente,
                    metodo_pago=metodo_pago,
                    fecha=datetime.now(UTC)
                )
                PagoService.create_pago(pago_instancia, file_comprobante, vendedor_id)

        # Actualizar estado de pagos
        venta.actualizar_estado()
        return venta

    @staticmethod
    def eliminar_venta(venta_id: int, actor_id: int = None) -> None:
        """
        Elimina una venta y revierte el stock asociado.
        Autoriza la venta real antes de revertir stock o borrar pagos en cascada.
        """
        venta = Venta.query.options(db.joinedload(Venta.detalles)).get_or_404(venta_id)
        if actor_id is not None:
            autorizar_venta(actor_id, venta)

        db.session.query(Venta).filter_by(id=venta_id).with_for_update().first()

        StockService.revertir_venta(venta)
        db.session.delete(venta)

    @staticmethod
    def eliminar_ventas_en_lote(venta_ids: list[int], actor_id: int = None) -> int:
        """
        Elimina un lote de ventas revirtiendo sus inventarios de forma segura.
        Valida la existencia y autorización de todas las ventas antes de mutar.
        """
        count = 0
        if not venta_ids:
            return count

        # Bloquear ventas y cargarlas
        ventas = (
            Venta.query
            .options(db.joinedload(Venta.detalles))
            .filter(Venta.id.in_(venta_ids))
            .with_for_update(of=Venta)
            .all()
        )

        if actor_id is not None:
            requerir_encontrados(list(venta_ids), [v.id for v in ventas])
            for venta in ventas:
                autorizar_venta(actor_id, venta)

        for venta in ventas:
            StockService.revertir_venta(venta)
            db.session.delete(venta)
            count += 1

        return count

def parse_iso_datetime(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        # Remover 'Z' al final y reemplazar por formato estándar
        clean_val = val.replace('Z', '+00:00')
        return datetime.fromisoformat(clean_val)
    except ValueError:
        return None
