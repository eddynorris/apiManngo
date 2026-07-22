import os
import re
import logging
from decimal import Decimal
from datetime import datetime, timezone

from extensions import db
from models import Users
from services.gemini_service import gemini_service
from services.telegram_service import telegram_service

from telegram.resolvers import resolver_almacen, buscar_presentacion, intentar_vinculacion
from telegram.context import set_user_context, clear_user_context, update_user_history
from telegram.handlers.venta import VentaHandler
from telegram.handlers.pago import PagoHandler
from telegram.handlers.transferencia import TransferenciaHandler
from telegram.handlers.produccion import ProduccionHandler
from telegram.handlers.guia_sunat import GuiaSunatHandler
from telegram.handlers.consulta import ConsultaHandler

logger = logging.getLogger(__name__)

class TelegramRouter:
    @staticmethod
    def resolver_almacen(user, text):
        return resolver_almacen(user, text)

    @staticmethod
    def buscar_presentacion(prod_name, tipos_validos=None):
        return buscar_presentacion(prod_name, tipos_validos)

    @staticmethod
    def intentar_vinculacion(chat_id, text):
        return intentar_vinculacion(chat_id, text)

    @staticmethod
    def handle_message(message):
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if not text:
            return

        user = Users.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            if intentar_vinculacion(chat_id, text):
                return
                
            msg = (
                f"❌ <b>Acceso Denegado</b>\n\n"
                f"Tu Telegram Chat ID no está vinculado a ningún usuario en el sistema.\n"
                f"<b>Chat ID:</b> <code>{chat_id}</code>\n\n"
                f"Para vincular tu cuenta, ingresa a tu perfil en Manngo, genera tu código de vinculación de 6 dígitos e ingrésalo aquí (ejemplo: <code>/vincular 123456</code>)."
            )
            telegram_service.send_message(chat_id, msg)
            return

        if text.lower() in ["/start", "/help", "hola"]:
            welcome_msg = (
                f"👋 ¡Hola <b>{user.username}</b>!\n\n"
                f"Bienvenido al asistente de <b>Manngo</b> via Telegram. Puedes escribirme comandos en lenguaje natural para realizar operaciones:\n\n"
                f"• <b>Ventas:</b> <i>'vendí 3 sacos de 20 a juan pérez pago completo'</i>\n"
                f"• <b>Gastos:</b> <i>'gasté 40 soles en combustible categoría logistica'</i>\n"
                f"• <b>Pagos:</b> <i>'abono de maría de 100 soles por yape'</i>\n"
                f"• <b>Depósitos:</b> <i>'depositados 300 soles en cuenta con referencia 8394'</i>\n"
                f"• <b>Producción:</b> <i>'se produjeron 10 sacos de briquetas de 5kg'</i>\n\n"
                f"¿Qué deseas realizar hoy?"
            )
            telegram_service.send_message(chat_id, welcome_msg)
            return

        telegram_service.send_message(chat_id, "🔄 <i>Procesando con Gemini...</i>")
        result = gemini_service.process_command(text, user.telegram_history)

        history_entry = result.get("history_entry")
        if history_entry:
            update_user_history(user, history_entry["user"], history_entry["model"])

        action = result.get("action")
        args = result.get("args", {})

        if action == "interpretar_operacion":
            VentaHandler.prepare_venta(chat_id, user, args, text, resolver_almacen, buscar_presentacion)
        elif action == "registrar_ventas_lote":
            VentaHandler.prepare_ventas_lote(chat_id, user, args, text, resolver_almacen, buscar_presentacion)
        elif action == "registrar_gasto":
            PagoHandler.prepare_gasto(chat_id, user, args, text, resolver_almacen)
        elif action == "registrar_pago":
            PagoHandler.prepare_pago(chat_id, user, args, resolver_almacen)
        elif action == "registrar_deposito":
            PagoHandler.prepare_deposito(chat_id, user, args)
        elif action == "registrar_produccion":
            ProduccionHandler.prepare_produccion(chat_id, user, args, text, resolver_almacen, buscar_presentacion)
        elif action == "registrar_compra_insumos":
            PagoHandler.prepare_compra_insumos(chat_id, user, args, text, resolver_almacen, buscar_presentacion)
        elif action == "solicitar_guia_remision":
            GuiaSunatHandler.prepare_guia_remision(chat_id, user, args, text, resolver_almacen, buscar_presentacion)
        elif action == "registrar_cliente":
            VentaHandler.prepare_cliente(chat_id, user, args)
        elif action == "registrar_transferencia":
            TransferenciaHandler.prepare_transferencia(chat_id, user, args, text, resolver_almacen, buscar_presentacion)
        elif action == "consultar_stock":
            ConsultaHandler.consultar_stock(chat_id, user, args, buscar_presentacion)
        elif action == "consultar_deudas":
            ConsultaHandler.consultar_deudas(chat_id, user, args)
        else:
            msg = result.get("message", "No entendí la operación. Intenta reformular.")
            telegram_service.send_message(chat_id, f"ℹ️ {msg}")
            db.session.commit()

    @staticmethod
    def handle_callback_query(callback_query):
        chat_id = callback_query["message"]["chat"]["id"]
        message_id = callback_query["message"]["message_id"]
        data = callback_query.get("data", "")

        user = Users.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            telegram_service.send_message(chat_id, "❌ Error de autenticación: Usuario no encontrado.")
            return

        if data == "cancel":
            clear_user_context(user)
            telegram_service.edit_message(chat_id, message_id, "❌ <b>Operación cancelada.</b>")
            return

        if data.startswith("confirm:"):
            context = user.telegram_context
            if not context:
                telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta operación expiró o ya fue procesada.</i>")
                return

            action = context.get("action")
            try:
                if action == "venta":
                    VentaHandler.execute_venta(chat_id, user, context, message_id)
                elif action == "ventas_lote":
                    VentaHandler.execute_ventas_lote(chat_id, user, context, message_id)
                elif action == "gasto":
                    PagoHandler.execute_gasto(chat_id, user, context, message_id)
                elif action == "pago":
                    PagoHandler.execute_pago(chat_id, user, context, message_id)
                elif action == "deposito":
                    PagoHandler.execute_deposito(chat_id, user, context, message_id)
                elif action == "produccion":
                    ProduccionHandler.execute_produccion(chat_id, user, context, message_id)
                elif action == "compra_insumos":
                    PagoHandler.execute_compra_insumos(chat_id, user, context, message_id)
                elif action == "guia_remision":
                    GuiaSunatHandler.execute_guia_remision(chat_id, user, context, message_id)
                elif action == "cliente":
                    VentaHandler.execute_cliente(chat_id, user, context, message_id)
                elif action == "transferencia":
                    TransferenciaHandler.execute_transferencia(chat_id, user, context, message_id)
                else:
                    telegram_service.edit_message(chat_id, message_id, f"❌ Error: Acción '{action}' no implementada.")

                clear_user_context(user)
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error al ejecutar acción '{action}': {e}", exc_info=True)
                telegram_service.edit_message(chat_id, message_id, f"❌ <b>Error interno:</b> {str(e)}")
