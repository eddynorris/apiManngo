import logging

from extensions import db
from models import Users, Cliente
from services.gemini_service import gemini_service
from services.telegram_service import telegram_service

from telegram.resolvers import resolver_almacen, buscar_presentacion, intentar_vinculacion, buscar_cliente_db
from telegram.context import clear_user_context, update_user_history
from telegram.state_machine import StateMachine, ConversationState
from telegram.handlers.venta import VentaHandler
from telegram.handlers.pago import PagoHandler
from telegram.handlers.transferencia import TransferenciaHandler
from telegram.handlers.produccion import ProduccionHandler
from telegram.handlers.guia_sunat import GuiaSunatHandler
from telegram.handlers.consulta import ConsultaHandler

import uuid

logger = logging.getLogger(__name__)

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

            # Comando /cancel para limpiar cualquier flujo activo (DEBE ir antes de verificar flujo)
            if text.lower() in ["/cancel", "/cancelar"]:
                StateMachine.clear_context(user)
                telegram_service.send_message(chat_id, "✅ Operación cancelada. ¿En qué más puedo ayudarte?")
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
                cliente = Cliente(
                    nombre=client_name,
                    telefono=phone,
                    direccion="Dirección no especificada",
                    ciudad="Lima"
                )
                db.session.add(cliente)
                db.session.flush()
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
            
            cliente = buscar_cliente_db(nombre=correct_name)
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
            item_index = result.get("item_index")
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
        """Procesa la respuesta de edición de un campo y actualiza la tarjeta."""
        edit_action = result.get("edit_action")
        value = result.get("value")
        original_context = result.get("original_context", {})
        message_id = result.get("message_id")
        
        try:
            if edit_action == "edit_cliente":
                # Buscar el cliente real en la BD para actualizar también el ID
                nuevo_cliente = buscar_cliente_db(nombre=value)
                if nuevo_cliente:
                    original_context["cliente_id"] = nuevo_cliente.id
                    original_context["cliente_nombre"] = nuevo_cliente.nombre
                else:
                    original_context["cliente_nombre"] = value
                telegram_service.send_message(chat_id, f"✅ Cliente actualizado a: <b>{original_context['cliente_nombre']}</b>")
                
            elif edit_action == "edit_precio":
                try:
                    new_price = float(value)
                    old_total = float(original_context.get("total", 0))
                    original_context["total"] = new_price
                    # Recalcular precios unitarios proporcionalmente
                    items = original_context.get("items", [])
                    if items and old_total > 0:
                        factor = new_price / old_total
                        for item in items:
                            item["precio_unitario"] = round(item.get("precio_unitario", 0) * factor, 2)
                            item["subtotal"] = round(item.get("cantidad", 1) * item["precio_unitario"], 2)
                    telegram_service.send_message(chat_id, f"✅ Precio total actualizado a: <b>S/ {new_price:.2f}</b>")
                except ValueError:
                    telegram_service.send_message(chat_id, "❌ Precio inválido. Ingresa un número válido.")
                    return
                    
            elif edit_action == "edit_gasto":
                try:
                    gasto_monto = float(value)
                    original_context["gasto_asociado"] = {
                        "monto": gasto_monto,
                        "descripcion": "Gasto agregado desde Telegram"
                    }
                    telegram_service.send_message(chat_id, f"✅ Gasto agregado: <b>S/ {gasto_monto:.2f}</b>")
                except ValueError:
                    telegram_service.send_message(chat_id, "❌ Monto inválido. Ingresa un número válido.")
                    return
                    
            elif edit_action == "edit_almacen":
                from models import Almacen
                nuevo_almacen = Almacen.query.filter(Almacen.nombre.ilike(f"%{value}%")).first()
                if nuevo_almacen:
                    original_context["almacen_id"] = nuevo_almacen.id
                    original_context["almacen_nombre"] = nuevo_almacen.nombre
                    telegram_service.send_message(chat_id, f"✅ Almacén actualizado a: <b>{nuevo_almacen.nombre}</b>")
                else:
                    telegram_service.send_message(chat_id, f"❌ No encontré un almacén con nombre '{value}'. Intenta de nuevo.")
                    # Re-activar el flujo para reintentar
                    StateMachine.transition_to(
                        user,
                        ConversationState.AWAITING_EXTRA_DATA,
                        action="edit_almacen",
                        data={"original_context": original_context, "message_id": message_id}
                    )
                    return
            
            # Guardar el contexto actualizado y volver a estado de confirmación
            StateMachine.set_awaiting_confirmation(
                user,
                original_context.get("action", "venta"),
                original_context,
                message_id
            )
            
            # Actualizar la tarjeta de confirmación con los nuevos datos
            if original_context.get("action") == "venta" and message_id:
                TelegramRouter._refresh_venta_card(chat_id, user, original_context, message_id)
                
        except Exception as e:
            logger.error(f"Error en _handle_edit_response: {e}", exc_info=True)
            telegram_service.send_message(chat_id, "❌ Error al procesar la edición. Intenta de nuevo.")

    @staticmethod
    def _refresh_venta_card(chat_id, user, context, message_id):
        """Reconstruye y edita la tarjeta de confirmación de venta con datos actualizados."""
        from telegram.resolvers import resolver_almacen as _resolver_almacen
        
        items = context.get("items", [])
        items_txt = "\n".join([
            f"• {item['cantidad']}x {item['producto_nombre']} (a S/ {item['precio_unitario']:.2f} c/u) = S/ {item.get('subtotal', item['cantidad'] * item['precio_unitario']):.2f}"
            for item in items
        ])
        pagos = context.get("pagos", [])
        pagos_txt = "\n".join([f"• S/ {p['monto']:.2f} ({p['metodo_pago']})" for p in pagos]) if pagos else "• Al crédito"
        
        gasto_asociado = context.get("gasto_asociado")
        gasto_txt = f"\n💸 <b>Gasto Asociado:</b> S/ {gasto_asociado.get('monto')} ({gasto_asociado.get('descripcion')})" if gasto_asociado else ""
        fecha = context.get("fecha")
        fecha_txt = f"\n📅 <b>Fecha:</b> {fecha}" if fecha else ""
        estado = context.get("estado", "completado")
        card_title = "Pedido (Sin descuento de Stock)" if estado == 'pedido' else "Venta"
        confirm_text = "pedido" if estado == 'pedido' else "venta"

        card = (
            f"📋 <b>Confirmar {card_title}</b> (actualizada)\n\n"
            f"👤 <b>Cliente:</b> {context.get('cliente_nombre')}\n"
            f"🏪 <b>Almacén:</b> {context.get('almacen_nombre')}\n"
            f"📦 <b>Productos:</b>\n{items_txt}\n"
            f"💰 <b>Total:</b> S/ {context.get('total', 0):.2f}\n"
            f"💳 <b>Pagos:</b>\n{pagos_txt}"
            f"{gasto_txt}"
            f"{fecha_txt}\n"
            f"\n¿Confirmas el registro de este {confirm_text}?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "👤 Cambiar Cliente", "callback_data": "edit:cliente"},
                    {"text": "💲 Modificar Precio", "callback_data": "edit:precio"}
                ],
                [
                    {"text": "💸 Agregar Gasto", "callback_data": "edit:gasto"},
                    {"text": "🏪 Cambiar Almacén", "callback_data": "edit:almacen"}
                ],
                [
                    {"text": f"✅ Confirmar {confirm_text.capitalize()}", "callback_data": "confirm:venta"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.edit_message(chat_id, message_id, card, reply_markup)

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

        # Manejar callbacks de edición
        if data.startswith("edit:"):
            import copy
            field = data.split(":")[1]
            context = user.telegram_context
            if not context:
                telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta operación expiró o ya fue procesada.</i>")
                return
            
            # Extraer el contexto real de la operación (si viene de la máquina de estados, tomar data)
            if "data" in context and context.get("state"):
                operation_context = context.get("data", context)
            else:
                operation_context = context
            # Deepcopy para evitar referencias circulares al serializar a JSON
            operation_context = copy.deepcopy(operation_context)
            
            # Iniciar flujo de edición según el campo
            if field == "cliente":
                StateMachine.transition_to(
                    user,
                    ConversationState.AWAITING_EXTRA_DATA,
                    action="edit_cliente",
                    data={"original_context": operation_context, "message_id": message_id}
                )
                telegram_service.send_message(
                    chat_id,
                    "👤 <b>Cambiar Cliente</b>\n\n"
                    "Ingresa el nombre del nuevo cliente:"
                )
            elif field == "precio":
                StateMachine.transition_to(
                    user,
                    ConversationState.AWAITING_EXTRA_DATA,
                    action="edit_precio",
                    data={"original_context": operation_context, "message_id": message_id}
                )
                telegram_service.send_message(
                    chat_id,
                    "💲 <b>Modificar Precio</b>\n\n"
                    "Ingresa el nuevo precio total:"
                )
            elif field == "gasto":
                StateMachine.transition_to(
                    user,
                    ConversationState.AWAITING_EXTRA_DATA,
                    action="edit_gasto",
                    data={"original_context": operation_context, "message_id": message_id}
                )
                telegram_service.send_message(
                    chat_id,
                    "💸 <b>Agregar Gasto</b>\n\n"
                    "Ingresa el monto del gasto:"
                )
            elif field == "almacen":
                StateMachine.transition_to(
                    user,
                    ConversationState.AWAITING_EXTRA_DATA,
                    action="edit_almacen",
                    data={"original_context": operation_context, "message_id": message_id}
                )
                telegram_service.send_message(
                    chat_id,
                    "🏪 <b>Cambiar Almacén</b>\n\n"
                    "Ingresa el nombre del nuevo almacén:"
                )
            else:
                telegram_service.send_message(chat_id, "❌ Campo de edición no reconocido.")
            return

        if data.startswith("confirm:"):
            context = user.telegram_context
            if not context:
                telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta operación expiró o ya fue procesada.</i>")
                return
            
            # Si el contexto viene de la máquina de estados (tiene "state" y "data"), desenvolverlo
            if "state" in context and "data" in context:
                context = context.get("data", context)
            
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
