import os
import logging
from flask import request
from flask_restful import Resource
from sqlalchemy import func
from decimal import Decimal
from datetime import datetime, timezone

from extensions import db
from models import Users, Cliente, PresentacionProducto, Inventario, Lote, Venta, VentaDetalle, Pago, Gasto, Movimiento, Receta, ComponenteReceta, Almacen
from services.gemini_service import gemini_service
from services.telegram_service import telegram_service
from common import handle_db_errors, parse_iso_datetime

logger = logging.getLogger(__name__)

class TelegramWebhookResource(Resource):
    def post(self, webhook_token):
        # 1. Validar el token de seguridad del Webhook
        expected_token = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        if not expected_token or webhook_token != expected_token:
            logger.warning(f"Intento de acceso no autorizado al webhook con token: {webhook_token}")
            return {"error": "Unauthorized webhook token"}, 403

        update = request.get_json()
        if not update:
            return {"error": "No data received"}, 400

        try:
            # 2. Procesar Callback Queries (pulsación de botones interactivos)
            if "callback_query" in update:
                self._handle_callback_query(update["callback_query"])
                return {"status": "ok"}, 200

            # 3. Procesar Mensajes de Texto normales
            if "message" in update:
                self._handle_message(update["message"])
                return {"status": "ok"}, 200

        except Exception as e:
            logger.error(f"Error procesando webhook de Telegram: {e}", exc_info=True)
            return {"error": str(e)}, 500

        return {"status": "ignored"}, 200

    def _handle_message(self, message):
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if not text:
            return

        # Buscar usuario asociado al chat id
        user = Users.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            msg = (
                f"❌ <b>Acceso Denegado</b>\n\n"
                f"Tu Telegram Chat ID no está vinculado a ningún usuario en el sistema.\n"
                f"<b>Chat ID:</b> <code>{chat_id}</code>\n\n"
                f"Por favor, solicita a un administrador que registre este Chat ID en tu perfil de usuario en Supabase."
            )
            telegram_service.send_message(chat_id, msg)
            return

        # Respuestas básicas
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

        # Procesar con Gemini
        telegram_service.send_message(chat_id, "🔄 <i>Procesando con Gemini...</i>")
        result = gemini_service.process_command(text)

        action = result.get("action")
        args = result.get("args", {})

        if action == "interpretar_operacion":
            self._prepare_venta(chat_id, user, args, text)
        elif action == "registrar_gasto":
            self._prepare_gasto(chat_id, user, args, text)
        elif action == "registrar_pago":
            self._prepare_pago(chat_id, user, args, text)
        elif action == "registrar_deposito":
            self._prepare_deposito(chat_id, user, args, text)
        elif action == "registrar_produccion":
            self._prepare_produccion(chat_id, user, args, text)
        else:
            # Error o mensaje conversacional
            msg = result.get("message", "No entendí la operación. Intenta reformular.")
            telegram_service.send_message(chat_id, f"ℹ️ {msg}")

    def _prepare_venta(self, chat_id, user, args, original_text):
        if not user.almacen_id:
            telegram_service.send_message(chat_id, "❌ Error: Tu usuario no tiene un almacén asignado.")
            return

        cliente_nombre = args.get("cliente_nombre")
        items_raw = args.get("items", [])

        if not cliente_nombre or not items_raw:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo identificar el cliente o los productos de la venta.")
            return

        # 1. Resolver Cliente
        cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{cliente_nombre}%")).first()
        warnings = []
        if not cliente:
            try:
                cliente = Cliente.query.filter(func.similarity(Cliente.nombre, cliente_nombre) > 0.3).order_by(func.similarity(Cliente.nombre, cliente_nombre).desc()).first()
                if cliente:
                    warnings.append(f"No se encontró cliente '{cliente_nombre}', se asumió '{cliente.nombre}'.")
            except Exception:
                pass

        if not cliente:
            # Crear un cliente genérico o advertir
            warnings.append(f"⚠️ Cliente '{cliente_nombre}' no encontrado. Se asociará al Cliente Genérico.")
            cliente = Cliente.query.filter(Cliente.nombre.ilike("%genérico%")).first()
            if not cliente:
                cliente = Cliente.query.first() # Fallback al primero

        # 2. Resolver Productos y Verificar Stock
        items_enriched = []
        total_estimado = Decimal("0")

        for item in items_raw:
            prod_name = item.get("producto_nombre")
            cantidad = item.get("cantidad", 1)
            precio_explicito = item.get("precio")

            # Buscar por trigrams o LIKE
            prod_name_safe = prod_name.replace('%', '').replace('_', '')
            presentacion = PresentacionProducto.query.filter(
                PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%"),
                PresentacionProducto.tipo == 'procesado'
            ).first()

            if not presentacion:
                try:
                    presentacion = PresentacionProducto.query.filter(
                        func.similarity(PresentacionProducto.nombre, prod_name_safe) > 0.3,
                        PresentacionProducto.tipo == 'procesado'
                    ).order_by(func.similarity(PresentacionProducto.nombre, prod_name_safe).desc()).first()
                except Exception:
                    pass

            if not presentacion:
                # Buscar cualquiera
                presentacion = PresentacionProducto.query.filter(PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%")).first()

            if not presentacion:
                telegram_service.send_message(chat_id, f"❌ Error: No se encontró el producto '{prod_name}' en el catálogo.")
                return

            precio_unitario = precio_explicito if precio_explicito else float(presentacion.precio_venta)
            subtotal = cantidad * precio_unitario
            total_estimado += Decimal(str(subtotal))

            # Resolver Lote y Stock en almacén del usuario
            inventario = Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=presentacion.id).first()
            lote_id = inventario.lote_id if inventario else None
            stock_actual = float(inventario.cantidad) if inventario else 0.0

            if stock_actual < cantidad:
                warnings.append(f"⚠️ Stock insuficiente para '{presentacion.nombre}'. Solicitado: {cantidad}, Disponible: {stock_actual}")

            items_enriched.append({
                "producto_id": presentacion.id,
                "producto_nombre": presentacion.nombre,
                "cantidad": cantidad,
                "precio_unitario": precio_unitario,
                "subtotal": subtotal,
                "lote_id": lote_id,
                "stock_actual": stock_actual
            })

        # 3. Lógica de Pago
        condicion_pago = args.get("condicion_pago", "completo")
        porcentaje_abono = args.get("porcentaje_abono")
        pagos_raw = args.get("pagos", [])

        pagos = []
        if condicion_pago == "completo":
            metodo = pagos_raw[0].get("metodo_pago", "efectivo") if pagos_raw else "efectivo"
            pagos = [{
                "monto": float(total_estimado),
                "metodo_pago": metodo,
                "es_deposito": False
            }]
        elif porcentaje_abono and porcentaje_abono > 0:
            monto_abono = round(float(total_estimado) * porcentaje_abono / 100.0, 2)
            metodo = pagos_raw[0].get("metodo_pago", "efectivo") if pagos_raw else "efectivo"
            pagos = [{
                "monto": monto_abono,
                "metodo_pago": metodo,
                "es_deposito": False
            }]
        elif pagos_raw:
            for p in pagos_raw:
                pagos.append({
                    "monto": p.get("monto"),
                    "metodo_pago": p.get("metodo_pago", "efectivo"),
                    "es_deposito": p.get("es_deposito", False)
                })

        gasto_asociado = args.get("gasto_asociado")

        # Guardar en contexto del usuario
        context_data = {
            "action": "venta",
            "cliente_id": cliente.id,
            "cliente_nombre": cliente.nombre,
            "items": items_enriched,
            "pagos": pagos,
            "gasto_asociado": gasto_asociado,
            "total": float(total_estimado)
        }
        user.telegram_context = context_data
        db.session.commit()

        # Enviar resumen
        items_txt = "\n".join([f"• {item['cantidad']}x {item['producto_nombre']} (S/ {item['precio_unitario']:.2f})" for item in items_enriched])
        pagos_txt = "\n".join([f"• S/ {p['monto']:.2f} ({p['metodo_pago']})" for p in pagos]) if pagos else "• Al crédito"
        warnings_txt = "\n".join(warnings) if warnings else ""

        gasto_txt = ""
        if gasto_asociado:
            gasto_txt = f"\n💸 <b>Gasto Asociado:</b> S/ {gasto_asociado.get('monto')} ({gasto_asociado.get('descripcion')})"

        card = (
            f"📋 <b>Confirmar Venta</b>\n\n"
            f"👤 <b>Cliente:</b> {cliente.nombre}\n"
            f"📦 <b>Productos:</b>\n{items_txt}\n"
            f"💰 <b>Total Venta:</b> S/ {total_estimado:.2f}\n"
            f"💳 <b>Pagos:</b>\n{pagos_txt}"
            f"{gasto_txt}\n"
        )
        if warnings_txt:
            card += f"\n⚠️ <b>Alertas:</b>\n{warnings_txt}\n"

        card += "\n¿Confirmas el registro de esta venta?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Venta", "callback_data": "confirm:venta"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    def _prepare_gasto(self, chat_id, user, args, original_text):
        descripcion = args.get("descripcion")
        monto = args.get("monto")
        categoria = args.get("categoria", "logistica")

        if not descripcion or not monto:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar la descripción o el monto del gasto.")
            return

        context_data = {
            "action": "gasto",
            "descripcion": descripcion,
            "monto": monto,
            "categoria": categoria,
            "almacen_id": user.almacen_id
        }
        user.telegram_context = context_data
        db.session.commit()

        card = (
            f"📋 <b>Confirmar Gasto</b>\n\n"
            f" Concepto: {descripcion}\n"
            f"💰 Monto: S/ {monto:.2f}\n"
            f"🏷️ Categoría: {categoria}\n\n"
            f"¿Confirmas el registro de este gasto?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Gasto", "callback_data": "confirm:gasto"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    def _prepare_pago(self, chat_id, user, args, original_text):
        cliente_nombre = args.get("cliente_nombre")
        monto = args.get("monto")
        metodo_pago = args.get("metodo_pago", "efectivo")
        referencia = args.get("referencia")

        if not monto:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar el monto del pago.")
            return

        # 1. Resolver Cliente
        cliente = None
        warnings = []
        if cliente_nombre:
            cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{cliente_nombre}%")).first()
            if not cliente:
                try:
                    cliente = Cliente.query.filter(func.similarity(Cliente.nombre, cliente_nombre) > 0.3).order_by(func.similarity(Cliente.nombre, cliente_nombre).desc()).first()
                except Exception:
                    pass

        if not cliente:
            telegram_service.send_message(chat_id, "❌ Error: Debes especificar un cliente válido con deuda para registrar el pago.")
            return

        # Verificar saldo pendiente
        saldo = float(cliente.saldo_pendiente)
        if saldo <= 0:
            warnings.append(f"⚠️ El cliente {cliente.nombre} no tiene deudas pendientes en el sistema.")

        context_data = {
            "action": "pago",
            "cliente_id": cliente.id,
            "cliente_nombre": cliente.nombre,
            "monto": monto,
            "metodo_pago": metodo_pago,
            "referencia": referencia
        }
        user.telegram_context = context_data
        db.session.commit()

        warnings_txt = "\n".join(warnings) if warnings else ""

        card = (
            f"📋 <b>Confirmar Pago de Deuda</b>\n\n"
            f"👤 Cliente: {cliente.nombre}\n"
            f"💰 Monto: S/ {monto:.2f}\n"
            f"💳 Método de Pago: {metodo_pago}\n"
            f"🔑 Referencia: {referencia if referencia else 'Ninguna'}\n"
            f"📈 Saldo Deuda Actual: S/ {saldo:.2f}\n\n"
        )
        if warnings_txt:
            card += f"{warnings_txt}\n\n"
        card += "¿Confirmas el registro de este pago?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Pago", "callback_data": "confirm:pago"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    def _prepare_deposito(self, chat_id, user, args, original_text):
        monto_depositado = args.get("monto_depositado")
        referencia = args.get("referencia")

        if not monto_depositado:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar el monto del depósito.")
            return

        context_data = {
            "action": "deposito",
            "monto_depositado": monto_depositado,
            "referencia": referencia
        }
        user.telegram_context = context_data
        db.session.commit()

        card = (
            f"📋 <b>Confirmar Depósito en Banco</b>\n\n"
            f"💰 Monto Depósito: S/ {monto_depositado:.2f}\n"
            f"🔑 Referencia Bancaria: {referencia if referencia else 'Ninguna'}\n\n"
            f"¿Confirmas que depositaste esta cantidad?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Depósito", "callback_data": "confirm:deposito"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    def _prepare_produccion(self, chat_id, user, args, original_text):
        if not user.almacen_id:
            telegram_service.send_message(chat_id, "❌ Error: Tu usuario no tiene un almacén asignado.")
            return

        producto_nombre = args.get("producto_nombre")
        cantidad_a_producir = args.get("cantidad_a_producir")

        if not producto_nombre or not cantidad_a_producir:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar el nombre del producto o la cantidad producida.")
            return

        # 1. Buscar PresentacionProducto por similitud
        prod_name_safe = producto_nombre.replace('%', '').replace('_', '')
        presentacion = PresentacionProducto.query.filter(
            PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%")
        ).first()

        if not presentacion:
            try:
                presentacion = PresentacionProducto.query.filter(
                    func.similarity(PresentacionProducto.nombre, prod_name_safe) > 0.3
                ).order_by(func.similarity(PresentacionProducto.nombre, prod_name_safe).desc()).first()
            except Exception:
                pass

        if not presentacion:
            telegram_service.send_message(chat_id, f"❌ Error: No se encontró el producto '{producto_nombre}' en el catálogo.")
            return

        # 2. Consultar Receta
        receta = Receta.query.filter_by(presentacion_id=presentacion.id).first()
        if not receta:
            telegram_service.send_message(chat_id, f"❌ Error: No se encontró una receta de producción para '{presentacion.nombre}'.")
            return

        # 3. Validar ingredientes y auto-seleccionar lotes por FIFO (antigüedad)
        lotes_seleccionados = []
        detalles_consumo = []
        warnings = []

        for componente in receta.componentes:
            cantidad_req = Decimal(str(componente.cantidad_necesaria)) * Decimal(str(cantidad_a_producir))
            if componente.tipo_consumo == 'materia_prima':
                # Buscar lotes activos con stock disponible de ese producto en el almacén (o globalmente en lotes)
                lotes_disponibles = Lote.query.filter(
                    Lote.producto_id == componente.componente_presentacion.producto_id,
                    Lote.cantidad_disponible_kg > 0,
                    Lote.is_active == True
                ).order_by(Lote.fecha_ingreso.asc()).all() # FIFO

                cantidad_acumulada = Decimal("0")
                for lote in lotes_disponibles:
                    lote_disponible = Decimal(str(lote.cantidad_disponible_kg))
                    lotes_seleccionados.append({
                        "componente_presentacion_id": componente.componente_presentacion_id,
                        "lote_id": lote.id
                    })
                    cantidad_acumulada += lote_disponible
                    detalles_consumo.append(f"• Consumir Lote #{lote.codigo_lote or lote.id} (Disp: {lote_disponible}kg)")
                    if cantidad_acumulada >= cantidad_req:
                        break

                if cantidad_acumulada < cantidad_req:
                    warnings.append(f"⚠️ Stock de materia prima '{componente.componente_presentacion.nombre}' es insuficiente. Requerido: {cantidad_req}kg, Disponible en lotes: {cantidad_acumulada}kg.")
            elif componente.tipo_consumo == 'insumo':
                inv_insumo = Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=componente.componente_presentacion_id).first()
                insumo_disponible = Decimal(str(inv_insumo.cantidad)) if inv_insumo else Decimal("0")
                detalles_consumo.append(f"• Insumo {componente.componente_presentacion.nombre}: {cantidad_req} unidades (Disp: {insumo_disponible})")
                if insumo_disponible < cantidad_req:
                    warnings.append(f"⚠️ Stock de insumo '{componente.componente_presentacion.nombre}' es insuficiente. Requerido: {cantidad_req}, Disponible: {insumo_disponible}.")

        # Guardar en contexto
        context_data = {
            "action": "produccion",
            "presentacion_id": presentacion.id,
            "presentacion_nombre": presentacion.nombre,
            "cantidad_a_producir": float(cantidad_a_producir),
            "lotes_seleccionados": lotes_seleccionados,
            "almacen_id": user.almacen_id
        }
        user.telegram_context = context_data
        db.session.commit()

        consumo_txt = "\n".join(detalles_consumo) if detalles_consumo else "Ninguno"
        warnings_txt = "\n".join(warnings) if warnings else ""

        almacen = Almacen.query.get(user.almacen_id)
        almacen_nombre = almacen.nombre if almacen else "Desconocido"

        card = (
            f"📋 <b>Confirmar Registro de Producción</b>\n\n"
            f"📦 Producto: {presentacion.nombre}\n"
            f"🔢 Cantidad a Producir: {cantidad_a_producir}\n"
            f"🏪 Almacén Destino: {almacen_nombre}\n\n"
            f"⚙️ <b>Consumo Estimado de Ingredientes:</b>\n{consumo_txt}\n\n"
        )
        if warnings_txt:
            card += f"⚠️ <b>Alertas de Stock:</b>\n{warnings_txt}\n\n"
        card += "¿Confirmas la fabricación de este lote?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Producción", "callback_data": "confirm:produccion"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    def _handle_callback_query(self, callback):
        callback_id = callback["id"]
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        data = callback["data"]

        user = Users.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            telegram_service.answer_callback_query(callback_id, "Acceso no autorizado.")
            return

        if data == "cancel":
            user.telegram_context = None
            db.session.commit()
            telegram_service.answer_callback_query(callback_id, "Operación cancelada.")
            telegram_service.edit_message(chat_id, message_id, "❌ <i>Operación cancelada por el usuario.</i>")
            return

        context = user.telegram_context
        if not context:
            telegram_service.answer_callback_query(callback_id, "Error: No hay una operación pendiente en tu sesión.")
            telegram_service.edit_message(chat_id, message_id, "⚠️ <i>Esta operación ya expiró o fue procesada.</i>")
            return

        try:
            if data == "confirm:venta":
                self._execute_venta(chat_id, user, context, message_id)
            elif data == "confirm:gasto":
                self._execute_gasto(chat_id, user, context, message_id)
            elif data == "confirm:pago":
                self._execute_pago(chat_id, user, context, message_id)
            elif data == "confirm:deposito":
                self._execute_deposito(chat_id, user, context, message_id)
            elif data == "confirm:produccion":
                self._execute_produccion(chat_id, user, context, message_id)

            # Limpiar contexto tras el éxito
            user.telegram_context = None
            db.session.commit()
            telegram_service.answer_callback_query(callback_id, "Operación registrada con éxito.")

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error executing callback action: {e}", exc_info=True)
            telegram_service.answer_callback_query(callback_id, f"Error: {str(e)}")
            telegram_service.send_message(chat_id, f"❌ Ocurrió un error al registrar la operación: {str(e)}")

    def _execute_venta(self, chat_id, user, context, message_id):
        # Desempaquetar datos
        cliente_id = context["cliente_id"]
        items = context["items"]
        pagos = context["pagos"]
        gasto_data = context["gasto_asociado"]
        almacen_id = user.almacen_id
        fecha_transaccion = datetime.now()

        total_venta = Decimal("0")
        detalles_venta = []

        for item in items:
            prod_id = item["producto_id"]
            cantidad = item["cantidad"]
            precio = Decimal(str(item["precio_unitario"]))
            lote_id = item["lote_id"]

            if not lote_id:
                # Buscar el lote con inventario en el almacén
                inv = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=prod_id).first()
                if not inv or inv.cantidad < cantidad:
                    raise ValueError(f"Stock insuficiente para producto ID {prod_id} durante la confirmación.")
                lote_id = inv.lote_id

            inventario = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=prod_id, lote_id=lote_id).with_for_update().first()
            if not inventario or inventario.cantidad < cantidad:
                raise ValueError(f"Stock insuficiente para producto ID {prod_id} (Lote: {lote_id})")

            inventario.cantidad -= Decimal(str(cantidad))

            detalle = VentaDetalle(
                presentacion_id=prod_id,
                cantidad=cantidad,
                precio_unitario=precio,
                lote_id=lote_id
            )
            detalles_venta.append(detalle)
            total_venta += (Decimal(str(cantidad)) * precio)

        nueva_venta = Venta(
            cliente_id=cliente_id,
            almacen_id=almacen_id,
            vendedor_id=user.id,
            total=total_venta,
            tipo_pago='contado' if pagos else 'credito',
            fecha=fecha_transaccion,
            detalles=detalles_venta
        )
        db.session.add(nueva_venta)
        db.session.flush()

        # Registrar Movimientos
        for detalle in nueva_venta.detalles:
            db.session.add(Movimiento(
                tipo='salida',
                presentacion_id=detalle.presentacion_id,
                lote_id=detalle.lote_id,
                cantidad=detalle.cantidad,
                usuario_id=user.id,
                motivo=f"Venta ID: {nueva_venta.id} (Telegram)",
                tipo_operacion='venta'
            ))

        # Registrar Pagos
        total_pagado = Decimal("0")
        for p in pagos:
            monto = Decimal(str(p["monto"]))
            metodo = p["metodo_pago"]
            if monto > 0:
                db.session.add(Pago(
                    venta_id=nueva_venta.id,
                    usuario_id=user.id,
                    monto=monto,
                    metodo_pago=metodo,
                    fecha=fecha_transaccion,
                    depositado=p.get("es_deposito", False),
                    referencia="Pago Telegram"
                ))
                total_pagado += monto

        # Actualizar estado de venta
        if total_pagado >= total_venta:
            nueva_venta.estado_pago = 'pagado'
        elif total_pagado > 0:
            nueva_venta.estado_pago = 'parcial'
        else:
            nueva_venta.estado_pago = 'pendiente'

        if total_pagado < total_venta:
            nueva_venta.tipo_pago = 'credito'

        # Registrar Gasto
        if gasto_data:
            db.session.add(Gasto(
                descripcion=gasto_data.get("descripcion"),
                monto=Decimal(str(gasto_data.get("monto"))),
                categoria=gasto_data.get("categoria"),
                fecha=fecha_transaccion.date(),
                usuario_id=user.id,
                almacen_id=almacen_id
            ))

        db.session.commit()
        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Venta registrada con éxito!</b>\n\n<b>Venta ID:</b> #{nueva_venta.id}\n<b>Cliente:</b> {context['cliente_nombre']}\n<b>Total:</b> S/ {total_venta:.2f}\n<b>Pagado:</b> S/ {total_pagado:.2f}")

    def _execute_gasto(self, chat_id, user, context, message_id):
        nuevo_gasto = Gasto(
            descripcion=context["descripcion"],
            monto=Decimal(str(context["monto"])),
            categoria=context["categoria"],
            fecha=datetime.now().date(),
            usuario_id=user.id,
            almacen_id=context["almacen_id"]
        )
        db.session.add(nuevo_gasto)
        db.session.commit()

        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Gasto registrado con éxito!</b>\n\n<b>Gasto ID:</b> #{nuevo_gasto.id}\n<b>Concepto:</b> {nuevo_gasto.descripcion}\n<b>Monto:</b> S/ {nuevo_gasto.monto:.2f}")

    def _execute_pago(self, chat_id, user, context, message_id):
        cliente_id = context["cliente_id"]
        monto = Decimal(str(context["monto"]))
        metodo_pago = context["metodo_pago"]
        referencia = context["referencia"]

        # Buscar ventas pendientes/parciales de ese cliente
        ventas_pendientes = Venta.query.filter(
            Venta.cliente_id == cliente_id,
            Venta.estado_pago != 'pagado'
        ).order_by(Venta.fecha.asc()).all()

        if not ventas_pendientes:
            raise ValueError(f"El cliente {context['cliente_nombre']} no tiene deudas pendientes para asociar el pago.")

        monto_restante = monto
        pagos_registrados = []

        for venta in ventas_pendientes:
            if monto_restante <= 0:
                break

            # Calcular saldo pendiente real de la venta
            saldo_venta = venta.total - sum(p.monto for p in venta.pagos)
            if saldo_venta <= 0:
                continue

            abono = min(monto_restante, saldo_venta)
            nuevo_pago = Pago(
                venta_id=venta.id,
                usuario_id=user.id,
                monto=abono,
                metodo_pago=metodo_pago,
                fecha=datetime.now(),
                referencia=referencia if referencia else "Abono Telegram"
            )
            db.session.add(nuevo_pago)
            db.session.flush()
            venta.actualizar_estado()

            pagos_registrados.append(f"• Abono de S/ {abono:.2f} a Venta #{venta.id}")
            monto_restante -= abono

        db.session.commit()

        detalles_pagos = "\n".join(pagos_registrados)
        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Pago registrado con éxito!</b>\n\n<b>Cliente:</b> {context['cliente_nombre']}\n<b>Monto Total:</b> S/ {monto:.2f}\n\n<b>Aplicado a:</b>\n{detalles_pagos}")

    def _execute_deposito(self, chat_id, user, context, message_id):
        monto_depositado = Decimal(str(context["monto_depositado"]))
        referencia = context["referencia"]

        # Buscar pagos en efectivo o no depositados (depositado = False)
        pagos_pendientes = Pago.query.filter(
            Pago.depositado == False,
            Pago.metodo_pago == 'efectivo'
        ).order_by(Pago.fecha.asc()).all()

        if not pagos_pendientes:
            # Si no hay pagos pendientes, crearemos un registro pero advertimos que no hay efectivo pendiente
            raise ValueError("No se encontraron pagos en efectivo pendientes de depósito en el sistema.")

        monto_restante = monto_depositado
        fecha_deposito = datetime.now()
        pagos_afectados = []

        for pago in pagos_pendientes:
            if monto_restante <= 0:
                break
            
            saldo_deposito_pendiente = pago.monto - (pago.monto_depositado or Decimal("0"))
            if saldo_deposito_pendiente <= 0:
                continue

            abono_deposito = min(monto_restante, saldo_deposito_pendiente)
            pago.monto_depositado = (pago.monto_depositado or Decimal("0")) + abono_deposito
            pago.depositado = True
            pago.fecha_deposito = fecha_deposito
            if referencia:
                pago.referencia = f"{pago.referencia or ''} | Dep: {referencia}".strip(" | ")

            pagos_afectados.append(f"• Depósito de S/ {abono_deposito:.2f} (de Pago #{pago.id})")
            monto_restante -= abono_deposito

        db.session.commit()

        detalles_dep = "\n".join(pagos_afectados)
        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Depósito registrado con éxito!</b>\n\n<b>Monto Depositado:</b> S/ {monto_depositado:.2f}\n<b>Referencia OP:</b> {referencia if referencia else 'Ninguna'}\n\n<b>Pagos liquidados:</b>\n{detalles_dep}")

    def _execute_produccion(self, chat_id, user, context, message_id):
        almacen_id = context["almacen_id"]
        presentacion_final_id = context["presentacion_id"]
        cantidad_a_producir = Decimal(str(context["cantidad_a_producir"]))
        lotes_seleccionados = context["lotes_seleccionados"]

        receta = Receta.query.filter_by(presentacion_id=presentacion_final_id).first()
        if not receta:
            raise ValueError(f"No se encontró una receta para la presentación ID {presentacion_final_id}")

        fecha_operacion = datetime.now(timezone.utc)
        motivo_base = f"Ensamblaje Telegram: Fabricación de {cantidad_a_producir} unidades de {receta.presentacion.nombre}"

        # 1. Registrar Salidas de materia prima y descontar stock de Lotes
        for item in lotes_seleccionados:
            componente_pres_id = item["componente_presentacion_id"]
            lote_id = item["lote_id"]
            
            # Buscar el componente en la receta para saber cuánta cantidad descontar
            comp = next(c for c in receta.componentes if c.componente_presentacion_id == componente_pres_id)
            cantidad_req_kg = comp.cantidad_necesaria * cantidad_a_producir

            lote = Lote.query.get(lote_id)
            if not lote or lote.cantidad_disponible_kg < cantidad_req_kg:
                raise ValueError(f"Stock insuficiente en Lote ID {lote_id} para materia prima ID {componente_pres_id}")

            lote.cantidad_disponible_kg -= cantidad_req_kg
            db.session.add(Movimiento(
                tipo='salida',
                presentacion_id=None,
                lote_id=lote_id,
                cantidad=cantidad_req_kg,
                fecha=fecha_operacion,
                motivo=motivo_base,
                usuario_id=user.id,
                tipo_operacion='ensamblaje'
            ))

        # Descontar insumos sin lote si aplica
        for comp in receta.componentes:
            if comp.tipo_consumo == 'insumo':
                cantidad_req_insumo = comp.cantidad_necesaria * cantidad_a_producir
                inv = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=comp.componente_presentacion_id, lote_id=None).first()
                if not inv or inv.cantidad < cantidad_req_insumo:
                    raise ValueError(f"Stock de insumo {comp.componente_presentacion.nombre} es insuficiente.")
                inv.cantidad -= cantidad_req_insumo
                db.session.add(Movimiento(
                    tipo='salida',
                    presentacion_id=comp.componente_presentacion_id,
                    lote_id=None,
                    cantidad=cantidad_req_insumo,
                    fecha=fecha_operacion,
                    motivo=motivo_base,
                    usuario_id=user.id,
                    tipo_operacion='ensamblaje'
                ))

        # 2. Registrar Entrada del Producto Final (Añadir inventario final)
        # Obtenemos el lote de origen (usamos el primer lote de materia prima seleccionado)
        lote_destino_id = lotes_seleccionados[0]["lote_id"] if lotes_seleccionados else None

        inv_destino = Inventario.query.filter_by(
            presentacion_id=presentacion_final_id,
            almacen_id=almacen_id,
            lote_id=lote_destino_id
        ).first()

        if inv_destino:
            inv_destino.cantidad += cantidad_a_producir
            inv_destino.ultima_actualizacion = fecha_operacion
        else:
            inv_destino = Inventario(
                presentacion_id=presentacion_final_id,
                almacen_id=almacen_id,
                lote_id=lote_destino_id,
                cantidad=cantidad_a_producir
            )
            db.session.add(inv_destino)

        # Movimiento de entrada
        db.session.add(Movimiento(
            tipo='entrada',
            presentacion_id=presentacion_final_id,
            lote_id=lote_destino_id,
            cantidad=cantidad_a_producir,
            fecha=fecha_operacion,
            motivo=motivo_base,
            usuario_id=user.id,
            tipo_operacion='ensamblaje'
        ))

        db.session.commit()

        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Producción registrada con éxito!</b>\n\n<b>Producto:</b> {context['presentacion_name'] if 'presentacion_name' in context else context.get('presentacion_nombre')}\n<b>Cantidad Producida:</b> {cantidad_a_producir}\n<b>Lote Destino Asociado:</b> Lote #{lote_destino_id if lote_destino_id else 'Ninguno'}")
