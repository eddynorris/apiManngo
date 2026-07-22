import os
import re
import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import func

from extensions import db
from models import Users, Almacen, PresentacionProducto
from services.gemini_service import gemini_service
from services.telegram_service import telegram_service

from telegram.handlers.venta import VentaHandler
from telegram.handlers.pago import PagoHandler
from telegram.handlers.transferencia import TransferenciaHandler

logger = logging.getLogger(__name__)

class TelegramRouter:
    @staticmethod
    def resolver_almacen(user, text):
        if user.rol == 'admin':
            almacenes = Almacen.query.all()
            for al in almacenes:
                if al.nombre.lower() in text.lower():
                    return al.id, al.nombre
            if user.almacen_id:
                al = db.session.get(Almacen, user.almacen_id)
                return user.almacen_id, al.nombre if al else "Desconocido"
            return None, None
        else:
            if user.almacen_id:
                al = db.session.get(Almacen, user.almacen_id)
                return user.almacen_id, al.nombre if al else "Desconocido"
            return None, None

    @staticmethod
    def buscar_presentacion(prod_name, tipos_validos=None):
        if tipos_validos is None:
            tipos_validos = ['procesado', 'briqueta']
        prod_name_safe = prod_name.replace('%', '').replace('_', '')
        
        weight = None
        match = re.search(r'(\d+(?:\.\d+)?)\s*(?:kg|k\b)', prod_name.lower())
        if match:
            weight = Decimal(match.group(1))
        else:
            match_number = re.search(r'\b(\d+(?:\.\d+)?)\b', prod_name.lower())
            if match_number:
                weight = Decimal(match_number.group(1))
                
        if weight is not None:
            candidatos = PresentacionProducto.query.filter(
                PresentacionProducto.capacidad_kg == weight,
                PresentacionProducto.tipo.in_(tipos_validos)
            ).all()
            
            if candidatos:
                if len(candidatos) == 1:
                    return candidatos[0]
                else:
                    best_match = None
                    best_score = -1.0
                    for c in candidatos:
                        try:
                            score = db.session.query(func.similarity(c.nombre, prod_name_safe)).scalar() or 0.0
                        except Exception:
                            score = 1.0 if prod_name_safe.lower() in c.nombre.lower() else 0.0
                        if score > best_score:
                            best_score = score
                            best_match = c
                    return best_match

        presentacion = PresentacionProducto.query.filter(
            PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%"),
            PresentacionProducto.tipo.in_(tipos_validos)
        ).first()

        if not presentacion:
            try:
                presentacion = PresentacionProducto.query.filter(
                    func.similarity(PresentacionProducto.nombre, prod_name_safe) > 0.3,
                    PresentacionProducto.tipo.in_(tipos_validos)
                ).order_by(func.similarity(PresentacionProducto.nombre, prod_name_safe).desc()).first()
            except Exception:
                pass

        if not presentacion:
            presentacion = PresentacionProducto.query.filter(
                PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%")
            ).first()

        return presentacion

    @staticmethod
    def intentar_vinculacion(chat_id, text):
        code_match = re.search(r'\b(\d{6})\b', text)
        if not code_match:
            return False
            
        code = code_match.group(1)
        now = datetime.now(timezone.utc)
        user = Users.query.filter(
            Users.telegram_linking_code == code,
            Users.telegram_linking_expires > now
        ).first()
        
        if not user:
            expired_user = Users.query.filter_by(telegram_linking_code=code).first()
            if expired_user:
                telegram_service.send_message(chat_id, "❌ El código de vinculación ha expirado. Por favor, genera uno nuevo en tu perfil de Manngo.")
                return True
            return False
            
        user.telegram_chat_id = chat_id
        user.telegram_linking_code = None
        user.telegram_linking_expires = None
        db.session.commit()
        
        telegram_service.send_message(
            chat_id, 
            f"✅ <b>¡Vinculación Exitosa!</b>\n\n"
            f"Tu cuenta de Telegram ha sido asociada al usuario <b>{user.username}</b>.\n"
            f"Ya puedes empezar a registrar operaciones usando lenguaje natural."
        )
        return True

    @staticmethod
    def handle_message(message):
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if not text:
            return

        user = Users.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            if TelegramRouter.intentar_vinculacion(chat_id, text):
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
            if not user.telegram_history:
                user.telegram_history = []
            new_history = list(user.telegram_history)
            new_history.append({"role": "user", "parts": [history_entry["user"]]})
            new_history.append({"role": "model", "parts": [history_entry["model"]]})
            user.telegram_history = new_history[-10:]

        action = result.get("action")
        args = result.get("args", {})

        if action == "interpretar_operacion":
            VentaHandler.prepare_venta(chat_id, user, args, text, TelegramRouter.resolver_almacen, TelegramRouter.buscar_presentacion)
        elif action == "registrar_ventas_lote":
            VentaHandler.prepare_ventas_lote(chat_id, user, args, text, TelegramRouter.resolver_almacen, TelegramRouter.buscar_presentacion)
        elif action == "registrar_gasto":
            PagoHandler.prepare_gasto(chat_id, user, args, text, TelegramRouter.resolver_almacen)
        elif action == "registrar_pago":
            PagoHandler.prepare_pago(chat_id, user, args, TelegramRouter.resolver_almacen)
        elif action == "registrar_deposito":
            PagoHandler.prepare_deposito(chat_id, user, args)
        elif action == "registrar_produccion":
            VentaHandler.prepare_produccion(chat_id, user, args, text, TelegramRouter.resolver_almacen, TelegramRouter.buscar_presentacion)
        elif action == "registrar_compra_insumos":
            PagoHandler.prepare_compra_insumos(chat_id, user, args, text, TelegramRouter.resolver_almacen, TelegramRouter.buscar_presentacion)
        elif action == "solicitar_guia_remision":
            TransferenciaHandler.prepare_guia_remision(chat_id, user, args, text, TelegramRouter.resolver_almacen, TelegramRouter.buscar_presentacion)
        elif action == "registrar_cliente":
            VentaHandler.prepare_cliente(chat_id, user, args)
        elif action == "registrar_transferencia":
            TransferenciaHandler.prepare_transferencia(chat_id, user, args, text, TelegramRouter.resolver_almacen, TelegramRouter.buscar_presentacion)
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
            user.telegram_context = None
            db.session.commit()
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
                    VentaHandler.execute_produccion(chat_id, user, context, message_id)
                elif action == "compra_insumos":
                    PagoHandler.execute_compra_insumos(chat_id, user, context, message_id)
                elif action == "guia_remision":
                    TransferenciaHandler.execute_guia_remision(chat_id, user, context, message_id)
                elif action == "cliente":
                    VentaHandler.execute_cliente(chat_id, user, context, message_id)
                elif action == "transferencia":
                    TransferenciaHandler.execute_transferencia(chat_id, user, context, message_id)
                else:
                    telegram_service.edit_message(chat_id, message_id, f"❌ Error: Acción '{action}' no implementada.")

                user.telegram_context = None
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error al ejecutar acción '{action}': {e}", exc_info=True)
                telegram_service.edit_message(chat_id, message_id, f"❌ <b>Error interno:</b> {str(e)}")
