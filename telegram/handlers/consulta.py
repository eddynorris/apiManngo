import logging
from decimal import Decimal
from sqlalchemy import func

from extensions import db
from models import Inventario, Venta, Cliente, Almacen
from services.telegram_service import telegram_service

logger = logging.getLogger(__name__)

class ConsultaHandler:
    @staticmethod
    def consultar_stock(chat_id, user, args, buscar_presentacion_fn):
        producto_nombre = args.get("producto_nombre")
        if not producto_nombre:
            # Consultar todo el inventario del almacén del usuario
            almacen_id = user.almacen_id
            if not almacen_id:
                telegram_service.send_message(chat_id, "❌ No tienes un almacén asignado para consultar stock.")
                return

            inventarios = db.session.query(
                Inventario.presentacion_id,
                func.sum(Inventario.cantidad).label("stock_total")
            ).filter(
                Inventario.almacen_id == almacen_id,
                Inventario.cantidad > 0
            ).group_by(Inventario.presentacion_id).all()

            if not inventarios:
                telegram_service.send_message(chat_id, "📦 Tu almacén actualmente no tiene stock registrado.")
                return

            detalles = []
            for inv in inventarios:
                from models import PresentacionProducto
                pres = db.session.get(PresentacionProducto, inv.presentacion_id)
                nombre_pres = pres.nombre if pres else f"Presentación #{inv.presentacion_id}"
                detalles.append(f"• <b>{nombre_pres}:</b> {inv.stock_total} unidades")

            detalles_txt = "\n".join(detalles)
            almacen = db.session.get(Almacen, almacen_id)
            nombre_almacen = almacen.nombre if almacen else "Desconocido"
            
            telegram_service.send_message(
                chat_id,
                f"📊 <b>Stock Actual - {nombre_almacen}</b>\n\n{detalles_txt}"
            )
            return

        presentacion = buscar_presentacion_fn(producto_nombre)
        if not presentacion:
            telegram_service.send_message(chat_id, f"❌ No se encontró la presentación '{producto_nombre}' en el catálogo.")
            return

        almacen_id = user.almacen_id
        if not almacen_id:
            telegram_service.send_message(chat_id, "❌ No tienes un almacén asignado para consultar stock.")
            return

        stock_total = db.session.query(
            func.sum(Inventario.cantidad)
        ).filter(
            Inventario.almacen_id == almacen_id,
            Inventario.presentacion_id == presentacion.id
        ).scalar() or Decimal("0")

        almacen = db.session.get(Almacen, almacen_id)
        nombre_almacen = almacen.nombre if almacen else "Desconocido"

        telegram_service.send_message(
            chat_id,
            f"📦 <b>Consulta de Stock</b>\n\n"
            f"• <b>Producto:</b> {presentacion.nombre}\n"
            f"• <b>Almacén:</b> {nombre_almacen}\n"
            f"• <b>Stock Disponible:</b> {stock_total} unidades"
        )

    @staticmethod
    def consultar_deudas(chat_id, user, args):
        cliente_nombre = args.get("cliente_nombre")
        if cliente_nombre:
            cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{cliente_nombre}%")).first()
            if not cliente:
                telegram_service.send_message(chat_id, f"❌ No se encontró el cliente '{cliente_nombre}'.")
                return

            ventas_pendientes = Venta.query.filter(
                Venta.cliente_id == cliente.id,
                Venta.saldo_pendiente > 0,
                Venta.estado != 'anulado'
            ).all()

            saldo_total = sum(v.saldo_pendiente for v in ventas_pendientes)
            
            if not ventas_pendientes:
                telegram_service.send_message(chat_id, f"✅ El cliente <b>{cliente.nombre}</b> no tiene deudas pendientes.")
                return

            detalles = [f"• Venta #{v.id} (Fecha: {v.created_at.strftime('%Y-%m-%d')}): Deuda S/ {v.saldo_pendiente:.2f}" for v in ventas_pendientes]
            detalles_txt = "\n".join(detalles)

            telegram_service.send_message(
                chat_id,
                f"💳 <b>Estado de Cuenta - {cliente.nombre}</b>\n\n"
                f"<b>Total Deuda:</b> S/ {saldo_total:.2f}\n\n"
                f"<b>Ventas pendientes:</b>\n{detalles_txt}"
            )
        else:
            # Consultar top clientes con deudas
            clientes_con_deuda = db.session.query(
                Venta.cliente_id,
                func.sum(Venta.saldo_pendiente).label("deuda_total")
            ).filter(
                Venta.saldo_pendiente > 0,
                Venta.estado != 'anulado'
            ).group_by(Venta.cliente_id).order_by(func.sum(Venta.saldo_pendiente).desc()).limit(10).all()

            if not clientes_con_deuda:
                telegram_service.send_message(chat_id, "✅ No hay clientes con deudas pendientes registradas.")
                return

            detalles = []
            for item in clientes_con_deuda:
                cl = db.session.get(Cliente, item.cliente_id)
                nombre_cl = cl.nombre if cl else f"Cliente #{item.cliente_id}"
                detalles.append(f"• <b>{nombre_cl}:</b> S/ {item.deuda_total:.2f}")

            detalles_txt = "\n".join(detalles)
            telegram_service.send_message(
                chat_id,
                f"💳 <b>Top Clientes con Saldo Pendiente</b>\n\n{detalles_txt}"
            )
