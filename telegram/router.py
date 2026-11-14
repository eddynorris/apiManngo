import copy
import logging
import re
import uuid
from datetime import date, timedelta

from extensions import db
from models import Almacen, Cliente, Conductor, GuiaRemision, Users, Vehiculo
from services import guia_service
from services.authorization import autorizar_almacen
from services.gemini_service import gemini_service
from services.money import monto_decimal_positivo
from services.telegram_service import telegram_service
from telegram.context import clear_user_context, update_user_history
from telegram.handlers.consulta import ConsultaHandler
from telegram.handlers.guia_sunat import GuiaSunatHandler, enviar_tarjeta_guia
from telegram.handlers.pago import PagoHandler
from telegram.handlers.produccion import ProduccionHandler
from telegram.handlers.transferencia import TransferenciaHandler
from telegram.handlers.venta import (
    VentaHandler,
    enviar_tarjeta_venta,
    nuevo_cliente_en_almacen,
    recalcular_items,
)
from telegram.resolvers import (
    almacenes_permitidos,
    buscar_cliente_db,
    buscar_presentacion,
    intentar_vinculacion,
    parsear_gasto,
    resolver_almacen,
)
from telegram.state_machine import ConversationState, StateMachine
from utils.date_utils import get_peru_now

logger = logging.getLogger(__name__)


def _positive_telegram_id(value) -> bool:
    """Acepta solo IDs positivos reales (excluye bool, strings y negativos)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _private_chat_sender_id(chat, sender):
    """Devuelve el ID del remitente solo para un chat privado 1:1 verificable.

    El bot es personal: exige ``chat.type == 'private'``, IDs positivos y que
    el remitente sea exactamente el dueño del chat. Grupos, canales, chats
    negativos y remitentes ausentes no tienen identidad válida.
    """
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    if chat.get("type") != "private":
        return None

    chat_id = chat.get("id")
    sender_id = sender.get("id")
    if not _positive_telegram_id(chat_id) or not _positive_telegram_id(sender_id):
        return None
    if chat_id != sender_id:
        return None
    return sender_id


def _borrador_actual(user):
    """Copia del borrador de la operación pendiente, esté o no a mitad de una edición."""
    contexto = user.telegram_context
    if not contexto:
        return None
    if contexto.get("state"):
        datos = contexto.get("data") or {}
        if "original_context" in datos:  # a mitad de una edición
            datos = datos["original_context"]
        contexto = datos
    return copy.deepcopy(contexto) if contexto else None


def _message_id_tarjeta(user):
    """message_id de la tarjeta vigente (None si el contexto no lo registra)."""
    contexto = user.telegram_context or {}
    if not contexto.get("state"):
        return None
    datos = contexto.get("data") or {}
    if "original_context" in datos:
        return datos.get("message_id")
    return contexto.get("message_id")


def format_user_friendly_error(e: Exception) -> str:
    """Transforma excepciones internas o de BD en mensajes seguros y amigables para la interfaz de Telegram."""
    err_str = str(e)
    logger.error(f"Excepción en bot de Telegram: {e}", exc_info=True)

    # 1. Violaciones de restricción de stock / Check constraints de DB
    if "inventario_cantidad_check" in err_str or "violates check constraint" in err_str or ("inventario" in err_str.lower() and "check" in err_str.lower()):
        return "❌ <b>Operación no realizada:</b> Stock insuficiente en el almacén para cubrir la cantidad solicitada."
    elif "foreign key constraint" in err_str.lower() or "fk_" in err_str.lower():
        return "❌ <b>Operación no realizada:</b> Referencia a un registro que no existe en el sistema."
    elif "unique constraint" in err_str.lower() or "uq_" in err_str.lower():
        return "❌ <b>Operación no realizada:</b> Ya existe un registro registrado con los mismos datos."

    # 2. Excepciones conocidas de validación
    err_type = type(e).__name__
    if err_type in ["VentaValidationError", "ProduccionValidationError", "StockInsuficienteError", "ValueError"]:
        return f"❌ <b>Atención:</b> {err_str}"

    # 3. Errores inesperados: no exponer SQL ni stacktrace al usuario final por seguridad
    error_code = uuid.uuid4().hex[:8].upper()
    return f"❌ <b>Error del sistema:</b> No se pudo completar la solicitud. Intenta nuevamente o contacta a soporte indicando la referencia <code>ERR-{error_code}</code>."

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
        try:
            if not isinstance(message, dict):
                return

            sender_id = _private_chat_sender_id(message.get("chat"), message.get("from"))
            if sender_id is None:
                logger.warning(
                    "Mensaje de Telegram rechazado: se requiere chat privado con remitente positivo"
                )
                return

            chat_id = sender_id
            text = message.get("text", "").strip()

            if not text:
                return

            user = Users.query.filter_by(telegram_chat_id=sender_id).first()
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

            # Comando /cancel para limpiar cualquier flujo activo (DEBE ir antes de verificar flujo)
            if text.lower() in ["/cancel", "/cancelar"]:
                StateMachine.clear_context(user)
                telegram_service.send_message(chat_id, "✅ Operación cancelada. ¿En qué más puedo ayudarte?")
                return

            # /guia T001-5: estado y PDF de una guía (no pasa por Gemini)
            if text.lower().startswith("/guia"):
                TelegramRouter._comando_guia(chat_id, user, text)
                return

            # === MÁQUINA DE ESTADOS: Verificar si hay un flujo activo ===
            ctx = StateMachine.get_context(user)
            if ctx.state not in (ConversationState.IDLE, ConversationState.AWAITING_CONFIRMATION):
                # El usuario está en medio de un flujo conversacional
                result = StateMachine.handle_user_response(user, text)
                if result:
                    TelegramRouter._handle_flow_response(chat_id, user, result)
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
        except Exception as e:
            db.session.rollback()
            friendly_msg = format_user_friendly_error(e)
            telegram_service.send_message(chat_id, friendly_msg)

    @staticmethod
    def _handle_flow_response(chat_id, user, result):
        """Procesa la respuesta de un flujo conversacional activo."""
        action = result.get("action")

        if action == "create_client":
            # El usuario quiere crear el cliente, iniciar flujo de creación
            client_name = result.get("client_name")
            original_data = result.get("original_data", {})
            telegram_service.send_message(
                chat_id,
                f"👤 <b>Crear Nuevo Cliente</b>\n\n"
                f"Nombre: <b>{client_name}</b>\n\n"
                f"Para crearlo, necesito al menos un número de teléfono de 9 dígitos.\n"
                f"Ingresa el teléfono (o /cancel para cancelar):"
            )
            # Guardar contexto temporal para el siguiente mensaje
            StateMachine.transition_to(
                user,
                ConversationState.AWAITING_EXTRA_DATA,
                action="creating_client",
                data={
                    "client_name": client_name,
                    "step": "awaiting_phone",
                    "original_data": {"action": result.get("original_action"), "data": original_data}
                }
            )

        elif action == "create_client_with_phone":
            # Crear el cliente con el teléfono proporcionado y continuar la venta
            client_name = result.get("client_name")
            phone = result.get("phone")
            original_data = result.get("original_data", {})
            original_action = result.get("original_action", "venta")

            # Verificar que no exista ya un cliente con ese teléfono
            existente = Cliente.query.filter_by(telefono=phone).first()
            if existente:
                cliente = existente
                telegram_service.send_message(
                    chat_id,
                    f"ℹ️ El teléfono {phone} ya pertenece al cliente <b>{existente.nombre}</b>. Usando ese cliente."
                )
            else:
                cliente = nuevo_cliente_en_almacen(
                    original_data.get("almacen_id"),
                    nombre=client_name,
                    telefono=phone,
                    direccion="Dirección no especificada",
                )
                telegram_service.send_message(
                    chat_id,
                    f"✅ Cliente <b>{client_name}</b> creado con teléfono {phone}. Continuando con la venta..."
                )

            # Continuar la operación original con el cliente ya resuelto
            TelegramRouter._resume_operation_with_cliente(chat_id, user, original_action, original_data, cliente)

        elif action == "use_generic_client":
            # Usar cliente genérico y continuar la operación original
            original_action = result.get("original_action")
            original_data = result.get("original_data", {})
            cliente = Cliente.query.filter(Cliente.nombre.ilike("%genérico%")).first()
            if not cliente:
                cliente = Cliente.query.first()
            if not cliente:
                telegram_service.send_message(chat_id, "❌ Error: No hay ningún cliente en el sistema. Crea uno primero.")
                return
            telegram_service.send_message(chat_id, f"ℹ️ Usando cliente genérico (<b>{cliente.nombre}</b>). Procesando...")
            TelegramRouter._resume_operation_with_cliente(chat_id, user, original_action, original_data, cliente)

        elif action == "retry_with_correct_name":
            # El usuario dio el nombre correcto, buscar y continuar
            correct_name = result.get("correct_name")
            original_action = result.get("original_action")
            original_data = result.get("original_data", {})

            cliente = buscar_cliente_db(
                nombre=correct_name, almacen_id=original_data.get("almacen_id")
            )
            if not cliente:
                # Aún no existe: preguntar de nuevo con el nuevo nombre
                StateMachine.ask_for_client_creation(user, correct_name, original_action, original_data)
                telegram_service.send_message(
                    chat_id,
                    f"❓ Tampoco encontré a '<b>{correct_name}</b>'.\n\n"
                    f"1️⃣ '<b>Sí</b>' para crear este cliente\n"
                    f"2️⃣ Otro nombre para reintentar\n"
                    f"3️⃣ '<b>No</b>' para usar cliente genérico"
                )
                return
            telegram_service.send_message(chat_id, f"✅ Cliente encontrado: <b>{cliente.nombre}</b>. Procesando...")
            TelegramRouter._resume_operation_with_cliente(chat_id, user, original_action, original_data, cliente)
        elif action == "set_almacen":
            almacen_name = result.get("almacen_name")
            telegram_service.send_message(chat_id, f"🏪 Almacén seleccionado: {almacen_name}. Procesando...")

        elif action == "set_price":
            price = result.get("price")
            telegram_service.send_message(chat_id, f"💲 Precio actualizado a S/ {price:.2f}")

        elif action == "invalid_price":
            telegram_service.send_message(chat_id, f"❌ {result.get('message')}")

        elif action == "invalid_phone":
            telegram_service.send_message(chat_id, f"❌ {result.get('message')}")

        elif action == "edit_response":
            # Respuesta a una edición de campo
            TelegramRouter._handle_edit_response(chat_id, user, result)

        else:
            telegram_service.send_message(chat_id, "ℹ️ No entendí tu respuesta. Intenta de nuevo o escribe /cancel.")

    @staticmethod
    def _resume_operation_with_cliente(chat_id, user, original_action, original_data, cliente):
        """Reanuda una operación (venta, etc.) con un cliente ya resuelto."""
        args = original_data.get("args", {})
        original_text = original_data.get("original_text", "")

        # Asegurar que el nombre del cliente en args coincida con el resuelto
        args = dict(args)
        args["cliente_nombre"] = cliente.nombre

        if original_action == "venta":
            VentaHandler.prepare_venta(
                chat_id, user, args, original_text,
                resolver_almacen, buscar_presentacion,
                forced_cliente=cliente
            )
        else:
            telegram_service.send_message(
                chat_id,
                f"❌ No se pudo reanudar la operación '{original_action}'. Intenta de nuevo."
            )

    @staticmethod
    def _handle_edit_response(chat_id, user, result):
        """Aplica la edición de un campo al borrador y vuelve a mostrar la tarjeta completa."""
        edit_action = result.get("edit_action")
        value = result.get("value") or ""
        original_context = result.get("original_context", {})
        message_id = result.get("message_id")

        def reintentar(mensaje):
            telegram_service.send_message(chat_id, mensaje)
            StateMachine.transition_to(
                user,
                ConversationState.AWAITING_EXTRA_DATA,
                action=edit_action,
                data={"original_context": original_context, "message_id": message_id},
            )

        try:
            if edit_action == "edit_cliente":
                nuevo_cliente = buscar_cliente_db(
                    nombre=value, almacen_id=original_context.get("almacen_id")
                )
                if not nuevo_cliente:
                    reintentar(
                        f"❌ No encontré un cliente llamado '<b>{value}</b>'. "
                        "Escribe otro nombre (o /cancel)."
                    )
                    return
                original_context["cliente_id"] = nuevo_cliente.id
                original_context["cliente_nombre"] = nuevo_cliente.nombre
                aviso = f"✅ Cliente actualizado a: <b>{nuevo_cliente.nombre}</b>"

            elif edit_action == "edit_precio":
                try:
                    new_price = monto_decimal_positivo(value.replace(",", "."), "precio")
                except Exception:
                    reintentar("❌ Precio inválido. Ingresa un número mayor que 0 (o /cancel).")
                    return
                new_price = float(new_price)
                old_total = float(original_context.get("total", 0))
                original_context["total"] = new_price
                # Recalcular precios unitarios proporcionalmente
                items = original_context.get("items", [])
                if items and old_total > 0:
                    factor = new_price / old_total
                    for item in items:
                        item["precio_unitario"] = round(item.get("precio_unitario", 0) * factor, 2)
                        item["subtotal"] = round(item.get("cantidad", 1) * item["precio_unitario"], 2)
                aviso = f"✅ Precio total actualizado a: <b>S/ {new_price:.2f}</b>"

            elif edit_action == "edit_gasto":
                gasto = parsear_gasto(value)
                if not gasto:
                    reintentar(
                        "❌ Monto inválido. Escribe el monto y, si quieres, la descripción "
                        "(ej.: <code>20 flete</code>), o /cancel."
                    )
                    return
                monto, descripcion = gasto
                original_context["gasto_asociado"] = {"monto": float(monto), "descripcion": descripcion}
                aviso = f"✅ Gasto agregado: <b>S/ {monto:.2f}</b> ({descripcion})"

            elif edit_action == "edit_almacen":
                coincidencias = [
                    a for a in almacenes_permitidos(user) if value.lower() in a.nombre.lower()
                ]
                if not value or not coincidencias:
                    reintentar(
                        f"❌ No encontré entre tus almacenes uno llamado '{value}'. "
                        "Usa los botones o escribe otro nombre (o /cancel)."
                    )
                    return
                TelegramRouter._aplicar_almacen(
                    chat_id, user, original_context, message_id, coincidencias[0].id
                )
                return

            elif edit_action == "edit_guia_destinatario":
                cliente = buscar_cliente_db(nombre=value) if value else None
                if not cliente:
                    reintentar(f"❌ No encontré un cliente llamado '<b>{value}</b>'. Escribe otro nombre (o /cancel).")
                    return
                original_context["borrador"]["destinatario_cliente_id"] = cliente.id
                telegram_service.send_message(chat_id, f"✅ Destinatario de la guía: <b>{cliente.nombre}</b>")
                enviar_tarjeta_guia(chat_id, user, original_context, message_id)
                return

            else:
                telegram_service.send_message(chat_id, "❌ Campo de edición no reconocido.")
                return

            telegram_service.send_message(chat_id, aviso)
            if original_context.get("action", "venta") == "venta":
                enviar_tarjeta_venta(chat_id, user, original_context, message_id)
            else:
                StateMachine.set_awaiting_confirmation(
                    user, original_context.get("action"), original_context, message_id
                )

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en _handle_edit_response: {e}", exc_info=True)
            telegram_service.send_message(chat_id, "❌ Error al procesar la edición. Intenta de nuevo.")

    @staticmethod
    def _aplicar_almacen(chat_id, user, borrador, message_id, almacen_id):
        """Cambia el almacén del borrador si el usuario tiene permiso y reenvía la tarjeta."""
        try:
            autorizar_almacen(user.id, almacen_id)
        except Exception:
            telegram_service.send_message(
                chat_id, "⛔ No tienes permiso para vender desde ese almacén."
            )
            return False
        almacen = db.session.get(Almacen, int(almacen_id))
        if almacen is None:
            telegram_service.send_message(chat_id, "❌ Ese almacén ya no existe.")
            return False
        borrador["almacen_id"] = almacen.id
        borrador["almacen_nombre"] = almacen.nombre
        recalcular_items(borrador)
        telegram_service.send_message(chat_id, f"✅ Almacén actualizado a: <b>{almacen.nombre}</b>")
        enviar_tarjeta_venta(chat_id, user, borrador, message_id)
        return True

    # ------------------------------------------------------------------
    # Guía de remisión
    # ------------------------------------------------------------------
    @staticmethod
    def _callback_guia(chat_id, user, message_id, data):
        partes = data.split(":")
        accion = partes[1] if len(partes) > 1 else ""
        valor = partes[2] if len(partes) > 2 else None

        if accion == "venta" and valor:
            GuiaSunatHandler.prepare_guia_remision(
                chat_id, user, {"venta_id": valor}, "", resolver_almacen, buscar_presentacion
            )
            return

        contexto = _borrador_actual(user)
        if accion == "desde_transferencia":
            if not contexto or contexto.get("action") != "transferencia":
                telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Este traslado expiró o ya fue procesado.</i>")
                return
            try:
                autorizar_almacen(user.id, contexto["almacen_origen_id"])
                autorizar_almacen(user.id, contexto["almacen_destino_id"])
                borrador = guia_service.borrador_traslado(
                    user, contexto["almacen_origen_id"], contexto["almacen_destino_id"],
                    [{"presentacion_id": t["presentacion_id"], "cantidad": t["cantidad"]}
                     for t in contexto.get("transferencias", [])],
                )
            except guia_service.GuiaError as exc:
                telegram_service.send_message(chat_id, f"❌ {exc}")
                return
            except Exception:
                telegram_service.send_message(chat_id, "⛔ No tienes permiso sobre esos almacenes.")
                return
            telegram_service.edit_message(chat_id, message_id, "📄 <i>Traslado pasado a guía: revisa la tarjeta de abajo.</i>")
            enviar_tarjeta_guia(chat_id, user, {
                "action": "guia_remision", "borrador": borrador, "idempotency_key": uuid.uuid4().hex,
            })
            return

        if not contexto or contexto.get("action") != "guia_remision":
            telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta guía expiró o ya fue procesada.</i>")
            return
        tarjeta_id = _message_id_tarjeta(user) or message_id
        borrador = contexto["borrador"]

        if accion in ("vehiculo", "conductor") and valor is None:
            modelo = Vehiculo if accion == "vehiculo" else Conductor
            filas = modelo.query.filter_by(activo=True).order_by(modelo.predeterminado.desc(), modelo.id).all()
            if not filas:
                telegram_service.send_message(chat_id, f"⚠️ No hay {accion}s registrados.")
                return
            botones = [[{
                "text": (f"🚛 {f.placa}" if accion == "vehiculo"
                         else f"🪪 {(f.apellidos or '')} {(f.nombres or '')}".strip()),
                "callback_data": f"guia:{accion}:{f.id}",
            }] for f in filas]
            telegram_service.send_message(chat_id, f"Elige el {accion}:", {"inline_keyboard": botones})
            return

        if accion in ("vehiculo", "conductor"):
            modelo = Vehiculo if accion == "vehiculo" else Conductor
            fila = db.session.get(modelo, int(valor)) if str(valor).isdigit() else None
            if fila is None or not fila.activo:
                telegram_service.send_message(chat_id, f"❌ Ese {accion} ya no está disponible.")
                return
            borrador[f"{accion}_id"] = fila.id
            TelegramRouter._reenviar_guia(chat_id, user, contexto, tarjeta_id, message_id)
            return

        if accion == "fecha" and valor is None:
            hoy = get_peru_now().date()
            botones = [[
                {"text": f"Hoy {hoy.strftime('%d/%m')}", "callback_data": f"guia:fecha:{hoy.isoformat()}"},
                {"text": f"Mañana {(hoy + timedelta(days=1)).strftime('%d/%m')}",
                 "callback_data": f"guia:fecha:{(hoy + timedelta(days=1)).isoformat()}"},
            ]]
            telegram_service.send_message(chat_id, "¿Cuándo empieza el traslado?", {"inline_keyboard": botones})
            return

        if accion == "fecha":
            try:
                fecha = date.fromisoformat(valor)
            except (TypeError, ValueError):
                telegram_service.send_message(chat_id, "❌ Fecha inválida.")
                return
            if fecha < get_peru_now().date():
                telegram_service.send_message(chat_id, "❌ La fecha de inicio no puede ser anterior a hoy.")
                return
            borrador["fecha_inicio_traslado"] = fecha.isoformat()
            TelegramRouter._reenviar_guia(chat_id, user, contexto, tarjeta_id, message_id)
            return

        if accion == "destinatario":
            StateMachine.transition_to(
                user, ConversationState.AWAITING_EXTRA_DATA, action="edit_guia_destinatario",
                data={"original_context": contexto, "message_id": tarjeta_id},
            )
            telegram_service.send_message(
                chat_id, "👤 <b>Destinatario de la guía</b>\n\nEscribe el nombre del cliente (debe tener DNI o RUC):"
            )
            return

        telegram_service.send_message(chat_id, "❌ Opción de guía no reconocida.")

    @staticmethod
    def _reenviar_guia(chat_id, user, contexto, tarjeta_id, message_id_callback):
        enviar_tarjeta_guia(chat_id, user, contexto, tarjeta_id)
        if message_id_callback and message_id_callback != tarjeta_id:
            telegram_service.edit_message(chat_id, message_id_callback, "✔️ <i>Elegido.</i>", reply_markup=None)

    @staticmethod
    def _comando_guia(chat_id, user, text):
        """/guia T001-5 → estado actual (consulta SUNAT si está pendiente) y PDF si fue aceptada."""
        match = re.match(r"^/guia(?:@\w+)?\s+([A-Za-z0-9]{4})\s*-\s*0*(\d{1,8})\s*$", text.strip())
        if not match:
            telegram_service.send_message(chat_id, "Uso: <code>/guia T001-5</code>")
            return
        serie, numero = match.group(1).upper(), int(match.group(2))
        guia = GuiaRemision.query.filter_by(serie=serie, numero=numero).first()
        if guia is None or (user.rol != "admin" and guia.creado_por_id != user.id):
            telegram_service.send_message(chat_id, f"No encontré la guía {serie}-{numero}.")
            return
        if guia.estado in guia_service.ESTADOS_PENDIENTES:
            try:
                guia_service.consultar(guia)
            except Exception as exc:
                db.session.rollback()
                logger.error("Error consultando la guía %s: %s", guia.id, type(exc).__name__)
        numero_txt = guia_service.numero_completo(guia)
        if guia.estado == guia_service.ESTADO_ACEPTADA:
            from services.guia_pdf import generar_pdf
            telegram_service.send_message(chat_id, f"✅ Guía {numero_txt}: Aceptada por SUNAT.")
            telegram_service.send_document(chat_id, f"{numero_txt}.pdf", generar_pdf(guia),
                                           caption=f"Guía de remisión {numero_txt}")
            return
        detalle = f"\n{guia.cdr_mensaje}" if guia.cdr_mensaje else ""
        telegram_service.send_message(chat_id, f"ℹ️ Guía {numero_txt}: estado <b>{guia.estado}</b>.{detalle}")

    @staticmethod
    def handle_callback_query(callback_query):
        if not isinstance(callback_query, dict):
            return

        callback_message = callback_query.get("message")
        if not isinstance(callback_message, dict):
            logger.warning("Callback de Telegram rechazado: falta el mensaje de origen")
            return

        sender_id = _private_chat_sender_id(
            callback_message.get("chat"),
            callback_query.get("from"),
        )
        if sender_id is None:
            logger.warning(
                "Callback de Telegram rechazado: se requiere chat privado con remitente positivo"
            )
            return

        chat_id = sender_id
        message_id = callback_message.get("message_id")
        data = callback_query.get("data", "")

        user = Users.query.filter_by(telegram_chat_id=sender_id).first()
        if not user:
            telegram_service.send_message(chat_id, "❌ Error de autenticación: Usuario no encontrado.")
            return

        if data == "cancel":
            clear_user_context(user)
            telegram_service.edit_message(chat_id, message_id, "❌ <b>Operación cancelada.</b>")
            return

        if data.startswith("guia:"):
            TelegramRouter._callback_guia(chat_id, user, message_id, data)
            return

        # Manejar callbacks de edición
        if data.startswith("edit:") or data.startswith("almacen:"):
            operation_context = _borrador_actual(user)
            if not operation_context:
                telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta operación expiró o ya fue procesada.</i>")
                return

            if data.startswith("almacen:"):
                tarjeta_id = _message_id_tarjeta(user) or message_id
                aplicado = TelegramRouter._aplicar_almacen(
                    chat_id, user, operation_context, tarjeta_id, data.split(":", 1)[1]
                )
                if aplicado and message_id != tarjeta_id:
                    # Mensaje con la lista de almacenes: quitarle los botones.
                    telegram_service.edit_message(
                        chat_id, message_id, "🏪 <i>Almacén elegido.</i>", reply_markup=None
                    )
                return

            field = data.split(":", 1)[1]
            preguntas = {
                "cliente": ("edit_cliente", "👤 <b>Cambiar Cliente</b>\n\nIngresa el nombre del nuevo cliente:", None),
                "precio": ("edit_precio", "💲 <b>Modificar Precio</b>\n\nIngresa el nuevo precio total:", None),
                "gasto": (
                    "edit_gasto",
                    "💸 <b>Agregar Gasto</b>\n\nEscribe el monto y, si quieres, la descripción "
                    "(ej.: <code>20 flete</code>):",
                    None,
                ),
            }
            if field == "almacen":
                botones = [
                    [{"text": f"🏪 {a.nombre}", "callback_data": f"almacen:{a.id}"}]
                    for a in almacenes_permitidos(user)
                ]
                if not botones:
                    telegram_service.send_message(chat_id, "⛔ No tienes almacenes asignados.")
                    return
                preguntas["almacen"] = (
                    "edit_almacen",
                    "🏪 <b>Cambiar Almacén</b>\n\nElige el almacén desde el que sale la venta:",
                    {"inline_keyboard": botones},
                )
            if field not in preguntas:
                telegram_service.send_message(chat_id, "❌ Campo de edición no reconocido.")
                return

            accion, pregunta, teclado = preguntas[field]
            StateMachine.transition_to(
                user,
                ConversationState.AWAITING_EXTRA_DATA,
                action=accion,
                data={"original_context": operation_context, "message_id": message_id},
            )
            if teclado:
                telegram_service.send_message(chat_id, pregunta, teclado)
            else:
                telegram_service.send_message(chat_id, pregunta)
            return

        if data.startswith("confirm:"):
            context = _borrador_actual(user)
            if not context:
                telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta operación expiró o ya fue procesada.</i>")
                return

            tarjeta_id = _message_id_tarjeta(user)
            if tarjeta_id and message_id and tarjeta_id != message_id:
                # Botón de una tarjeta vieja: solo vale la última versión del borrador.
                telegram_service.edit_message(
                    chat_id, message_id, "⚠️ <i>Tarjeta desactualizada: usa la más reciente.</i>"
                )
                return

            if context.get("status") == "processing":
                return # Ignorar doble click

            context["status"] = "processing"
            user.telegram_context = context
            db.session.commit()

            # Cambiar a estado de carga para dar feedback visual
            telegram_service.edit_message(chat_id, message_id, "⏳ <i>Procesando operación... Por favor espera.</i>")

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
                clear_user_context(user)
                friendly_msg = format_user_friendly_error(e)
                telegram_service.edit_message(chat_id, message_id, friendly_msg)
