import os
import logging
from decimal import Decimal
from datetime import datetime, timezone

from extensions import db
from models import Users, Cliente, PresentacionProducto, Inventario, Lote, Movimiento, Almacen, Receta, ComponenteReceta
from services.telegram_service import telegram_service
from services.sunat_service import sunat_service

logger = logging.getLogger(__name__)

class TransferenciaHandler:
    @staticmethod
    def prepare_transferencia(chat_id, user, args, original_text, resolver_almacen_fn, buscar_presentacion_fn):
        origen_nombre = args.get("almacen_origen") or args.get("almacen_origen_nombre")
        destino_nombre = args.get("almacen_destino") or args.get("almacen_destino_nombre")
        items = args.get("items", [])

        if not items or not destino_nombre:
            telegram_service.send_message(chat_id, "❌ Error: Debes especificar los productos a transferir y el almacén de destino.")
            return

        if origen_nombre:
            almacen_origen = Almacen.query.filter(Almacen.nombre.ilike(f"%{origen_nombre}%")).first()
        else:
            almacen_origen_id, _ = resolver_almacen_fn(user, original_text)
            almacen_origen = Almacen.query.get(almacen_origen_id) if almacen_origen_id else None

        if not almacen_origen:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo determinar el almacén de origen.")
            return

        almacen_destino = Almacen.query.filter(Almacen.nombre.ilike(f"%{destino_nombre}%")).first()
        if not almacen_destino:
            telegram_service.send_message(chat_id, f"❌ Error: No se encontró ningún almacén con el nombre '{destino_nombre}'.")
            return

        if almacen_origen.id == almacen_destino.id:
            telegram_service.send_message(chat_id, "❌ Error: El almacén de origen y destino no pueden ser el mismo.")
            return

        items_enriched = []
        warnings = []
        detalles_txt = []
        for item in items:
            prod_name = item.get("producto_nombre")
            cantidad = Decimal(str(item.get("cantidad", 0)))
            if cantidad <= 0 or not prod_name:
                continue

            presentacion = buscar_presentacion_fn(prod_name, ['procesado', 'briqueta', 'insumo'])
            if not presentacion:
                telegram_service.send_message(chat_id, f"❌ Error: No se encontró la presentación '{prod_name}' en el catálogo.")
                return

            invs_origen = Inventario.query.filter_by(
                almacen_id=almacen_origen.id,
                presentacion_id=presentacion.id
            ).all()
            stock_disp = sum(inv.cantidad for inv in invs_origen)

            if stock_disp < cantidad:
                warnings.append(f"⚠️ Stock insuficiente en {almacen_origen.nombre} para '{presentacion.nombre}'. Req: {cantidad}, Disp: {stock_disp}")

            items_enriched.append({
                "presentacion_id": presentacion.id,
                "presentacion_nombre": presentacion.nombre,
                "cantidad": float(cantidad)
            })
            detalles_txt.append(f"• {cantidad}x {presentacion.nombre}")

        if not items_enriched:
            telegram_service.send_message(chat_id, "❌ Error: No se interpretaron productos válidos para transferir.")
            return

        context_data = {
            "action": "transferencia",
            "almacen_origen_id": almacen_origen.id,
            "almacen_origen_nombre": almacen_origen.nombre,
            "almacen_destino_id": almacen_destino.id,
            "almacen_destino_nombre": almacen_destino.nombre,
            "transferencias": items_enriched
        }
        user.telegram_context = context_data
        db.session.commit()

        prod_txt = "\n".join(detalles_txt)
        warnings_txt = "\n".join(warnings) if warnings else ""

        card = (
            f"📋 <b>Confirmar Traslado de Inventario</b>\n\n"
            f"📤 <b>Origen:</b> {almacen_origen.nombre}\n"
            f"📥 <b>Destino:</b> {almacen_destino.nombre}\n\n"
            f"📦 <b>Mercadería a Mover:</b>\n{prod_txt}\n\n"
        )
        if warnings_txt:
            card += f"⚠️ <b>Alertas de Stock:</b>\n{warnings_txt}\n\n"
        card += "¿Confirmas el traslado físico de estos productos?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Traslado", "callback_data": "confirm:transferencia"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_transferencia(chat_id, user, context, message_id):
        almacen_origen_id = context["almacen_origen_id"]
        almacen_destino_id = context["almacen_destino_id"]
        transferencias_raw = context["transferencias"]

        payload = {
            "almacen_origen_id": almacen_origen_id,
            "almacen_destino_id": almacen_destino_id,
            "transferencias": [
                {"presentacion_id": t["presentacion_id"], "cantidad": t["cantidad"]}
                for t in transferencias_raw
            ]
        }

        from resources.transferencia_resource import TransferenciaService
        from unittest.mock import patch

        with patch('resources.transferencia_resource.get_jwt', return_value={"sub": user.id, "rol": user.rol, "almacen_id": user.almacen_id}):
            service = TransferenciaService(payload)
            result = service.ejecutar_transferencia()
            db.session.commit()

        detalles_str = "\n".join([f"• {t['cantidad']}x {t.get('presentacion_nombre', 'Producto')}" for t in transferencias_raw])

        telegram_service.edit_message(
            chat_id,
            message_id,
            f"✅ <b>¡Traslado de inventario registrado con éxito!</b>\n\n"
            f"📤 <b>Origen:</b> {context['almacen_origen_nombre']}\n"
            f"📥 <b>Destino:</b> {context['almacen_destino_nombre']}\n"
            f"🔑 <b>Op ID:</b> {result['id_operacion']}\n\n"
            f"<b>Productos transferidos:</b>\n{detalles_str}"
        )
