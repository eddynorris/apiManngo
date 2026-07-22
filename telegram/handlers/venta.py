import os
import logging
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import func

from extensions import db
from models import Users, Cliente, PresentacionProducto, Inventario, Lote, Venta, VentaDetalle, Pago, Gasto, Movimiento, Almacen
from services.telegram_service import telegram_service
from services.venta_service import VentaService

logger = logging.getLogger(__name__)

class VentaHandler:
    @staticmethod
    def prepare_venta(chat_id, user, args, original_text, resolver_almacen_fn, buscar_presentacion_fn):
        almacen_id, almacen_nombre = resolver_almacen_fn(user, original_text)
        if not almacen_id:
            telegram_service.send_message(chat_id, "❌ Error: Especifica el almacén en tu mensaje o asigna uno por defecto a tu usuario.")
            return

        cliente_nombre = args.get("cliente_nombre")
        phone_match = args.get("cliente_telefono")
        ruc_match = args.get("cliente_ruc")
        items_raw = args.get("items", [])

        if not items_raw:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar la lista de productos de la venta.")
            return

        # Resolver Cliente
        phone = phone_match.group(1) if hasattr(phone_match, 'group') else phone_match
        ruc_val = ruc_match.group(1) if hasattr(ruc_match, 'group') else ruc_match
        cliente = None
        warnings = []

        if phone:
            cliente = Cliente.query.filter_by(telefono=phone).first()
            if cliente:
                if ruc_val and not cliente.ruc:
                    cliente.ruc = ruc_val
                    db.session.commit()
                    warnings.append(f"Se identificó al cliente {cliente.nombre} y se le asignó RUC {ruc_val}.")
                else:
                    warnings.append(f"Se identificó al cliente {cliente.nombre} por el número de teléfono {phone}.")
            else:
                nombre_nuevo = cliente_nombre if cliente_nombre and cliente_nombre.strip() else f"Cliente {phone}"
                cliente = Cliente(
                    nombre=nombre_nuevo,
                    telefono=phone,
                    ruc=ruc_val,
                    direccion="Dirección no especificada",
                    ciudad="Lima"
                )
                db.session.add(cliente)
                db.session.flush()
                extra_ruc_txt = f" y RUC {ruc_val}" if ruc_val else ""
                warnings.append(f"👤 <b>Cliente Nuevo Creado</b>: Se registró automáticamente a '{nombre_nuevo}' con teléfono {phone}{extra_ruc_txt}.")

        if not cliente and cliente_nombre:
            cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{cliente_nombre}%")).first()
            if not cliente:
                try:
                    cliente = Cliente.query.filter(func.similarity(Cliente.nombre, cliente_nombre) > 0.3).order_by(func.similarity(Cliente.nombre, cliente_nombre).desc()).first()
                    if cliente:
                        warnings.append(f"No se encontró cliente '{cliente_nombre}', se asumió '{cliente.nombre}'.")
                except Exception:
                    pass

        if not cliente:
            warnings.append(f"⚠️ Cliente '{cliente_nombre}' no encontrado. Se asociará al Cliente Genérico.")
            cliente = Cliente.query.filter(Cliente.nombre.ilike("%genérico%")).first()
            if not cliente:
                cliente = Cliente.query.first()

        # Resolver Productos y Verificar Stock
        items_enriched = []
        total_estimado = Decimal("0")

        for item in items_raw:
            prod_name = item.get("producto_nombre")
            cantidad = item.get("cantidad", 1)
            precio_explicito = item.get("precio")

            presentacion = buscar_presentacion_fn(prod_name, ['procesado'])
            if not presentacion:
                telegram_service.send_message(chat_id, f"❌ Error: No se encontró el producto '{prod_name}' en el catálogo.")
                return

            precio_unitario = precio_explicito if precio_explicito else float(presentacion.precio_venta)
            subtotal = cantidad * precio_unitario
            total_estimado += Decimal(str(subtotal))

            from models import Lote as LoteModel
            invs_fifo = (
                Inventario.query
                .join(LoteModel, Inventario.lote_id == LoteModel.id, isouter=True)
                .filter(
                    Inventario.presentacion_id == presentacion.id,
                    Inventario.almacen_id == almacen_id,
                    Inventario.cantidad > 0
                )
                .order_by(LoteModel.fecha_ingreso.asc(), Inventario.id.asc())
                .all()
            )
            lote_id = invs_fifo[0].lote_id if invs_fifo else None

            all_invs = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=presentacion.id).all()
            stock_actual = sum(float(i.cantidad) for i in all_invs) if all_invs else 0.0

            if stock_actual < cantidad:
                warnings.append(f"⚠️ Stock insuficiente para '{presentacion.nombre}'. Solicitado: {cantidad}, Disponible: {stock_actual}")

            items_enriched.append({
                "producto_id": presentacion.id,
                "producto_nombre": presentacion.nombre,
                "presentacion_id": presentacion.id,
                "cantidad": cantidad,
                "precio_unitario": precio_unitario,
                "subtotal": subtotal,
                "lote_id": lote_id,
                "stock_actual": stock_actual
            })

        # Lógica de Pago
        condicion_pago = args.get("condicion_pago")
        porcentaje_abono = args.get("porcentaje_abono")
        pagos_raw = args.get("pagos", [])

        if not condicion_pago:
            if pagos_raw:
                condicion_pago = "completo"
            elif porcentaje_abono and porcentaje_abono > 0:
                condicion_pago = "parcial"
            else:
                condicion_pago = "credito"

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
        estado = args.get("estado", "completado").lower()
        fecha = args.get("fecha")

        context_data = {
            "action": "venta",
            "cliente_id": cliente.id,
            "cliente_nombre": cliente.nombre,
            "items": items_enriched,
            "pagos": pagos,
            "gasto_asociado": gasto_asociado,
            "total": float(total_estimado),
            "almacen_id": almacen_id,
            "almacen_nombre": almacen_nombre,
            "estado": estado,
            "fecha": fecha
        }
        user.telegram_context = context_data
        db.session.commit()

        items_txt = "\n".join([f"• {item['cantidad']}x {item['producto_nombre']} (S/ {item['precio_unitario']:.2f})" for item in items_enriched])
        pagos_txt = "\n".join([f"• S/ {p['monto']:.2f} ({p['metodo_pago']})" for p in pagos]) if pagos else "• Al crédito"
        warnings_txt = "\n".join(warnings) if warnings else ""

        gasto_txt = f"\n💸 <b>Gasto Asociado:</b> S/ {gasto_asociado.get('monto')} ({gasto_asociado.get('descripcion')})" if gasto_asociado else ""
        fecha_txt = f"\n📅 <b>Fecha:</b> {fecha}" if fecha else ""
        card_title = "Pedido (Sin descuento de Stock)" if estado == 'pedido' else "Venta"
        confirm_text = "pedido" if estado == 'pedido' else "venta"

        card = (
            f"📋 <b>Confirmar {card_title}</b>\n\n"
            f"👤 <b>Cliente:</b> {cliente.nombre}\n"
            f"🏪 <b>Almacén:</b> {almacen_nombre}\n"
            f"📦 <b>Productos:</b>\n{items_txt}\n"
            f"💰 <b>Total:</b> S/ {total_estimado:.2f}\n"
            f"💳 <b>Pagos:</b>\n{pagos_txt}"
            f"{gasto_txt}"
            f"{fecha_txt}\n"
        )
        if warnings_txt and estado != 'pedido':
            card += f"\n⚠️ <b>Alertas:</b>\n{warnings_txt}\n"

        card += f"\n¿Confirmas el registro de este {confirm_text}?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": f"✅ Confirmar {confirm_text.capitalize()}", "callback_data": "confirm:venta"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_venta(chat_id, user, context, message_id):
        almacen_id = context["almacen_id"]
        almacen_nombre = context["almacen_nombre"]
        estado = context.get("estado", "completado")
        
        fecha_str = context.get("fecha")
        if fecha_str:
            try:
                fecha_parsed = datetime.strptime(fecha_str, "%Y-%m-%d")
                ahora = datetime.now()
                fecha_transaccion = datetime(
                    fecha_parsed.year, fecha_parsed.month, fecha_parsed.day,
                    ahora.hour, ahora.minute, ahora.second, ahora.microsecond, ahora.tzinfo
                )
            except Exception:
                fecha_transaccion = datetime.now()
        else:
            fecha_transaccion = datetime.now()

        detalles_data = [
            {
                "presentacion_id": item["presentacion_id"],
                "cantidad": item["cantidad"],
                "precio_unitario": item["precio_unitario"]
            }
            for item in context["items"]
        ]

        pagos = context.get("pagos", [])
        monto_pago = Decimal(str(sum(p["monto"] for p in pagos))) if pagos else Decimal("0")
        metodo_pago = pagos[0]["metodo_pago"] if pagos else "efectivo"

        gasto_data = context.get("gasto_asociado")
        monto_gasto = Decimal(str(gasto_data.get("monto", 0))) if gasto_data else Decimal("0")

        nueva_venta = VentaService.crear_venta(
            vendedor_id=user.id,
            cliente_id=context["cliente_id"],
            almacen_id=almacen_id,
            detalles_data=detalles_data,
            estado=estado,
            fecha=fecha_transaccion,
            monto_pago=monto_pago,
            metodo_pago=metodo_pago,
            monto_gasto=monto_gasto,
            permitir_stock_negativo=True
        )
        db.session.commit()

        total_venta = Decimal(str(context["total"]))
        if estado == 'pedido':
            telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Pedido registrado con éxito!</b>\n\n<b>Pedido ID:</b> #{nueva_venta.id}\n<b>Cliente:</b> {context['cliente_nombre']}\n<b>Almacén:</b> {almacen_nombre}\n<b>Total Estimado:</b> S/ {total_venta:.2f}\n<b>Pagado/Abono:</b> S/ {monto_pago:.2f}")
        else:
            telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Venta registrada con éxito!</b>\n\n<b>Venta ID:</b> #{nueva_venta.id}\n<b>Cliente:</b> {context['cliente_nombre']}\n<b>Almacén:</b> {almacen_nombre}\n<b>Total:</b> S/ {total_venta:.2f}\n<b>Pagado:</b> S/ {monto_pago:.2f}")

    @staticmethod
    def prepare_cliente(chat_id, user, args):
        nombre = args.get("nombre")
        telefono = args.get("telefono")
        documento = args.get("documento")
        direccion = args.get("direccion") or "Dirección no especificada"

        if not nombre or not telefono:
            telegram_service.send_message(chat_id, "❌ Error: Para registrar un cliente debes proporcionar al menos el nombre y el celular de 9 dígitos.")
            return

        existente = Cliente.query.filter_by(telefono=telefono).first()
        if existente:
            telegram_service.send_message(chat_id, f"ℹ️ El cliente <b>{existente.nombre}</b> ya está registrado con el teléfono <code>{telefono}</code>.")
            return

        user.telegram_context = {
            "action": "cliente",
            "nombre": nombre,
            "telefono": telefono,
            "documento": documento,
            "direccion": direccion
        }
        db.session.commit()

        msg = (
            f"👤 <b>Confirmar Registro de Cliente</b>\n\n"
            f"<b>Nombre:</b> {nombre}\n"
            f"<b>Teléfono:</b> {telefono}\n"
            f"<b>Documento:</b> {documento or 'No especificado'}\n"
            f"<b>Dirección:</b> {direccion}\n\n"
            f"¿Confirmas el registro de este cliente?"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar", "callback_data": "confirm:cliente"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, msg, reply_markup=keyboard)

    @staticmethod
    def execute_cliente(chat_id, user, context, message_id):
        nuevo_cliente = Cliente(
            nombre=context["nombre"],
            telefono=context["telefono"],
            ruc=context.get("documento"),
            direccion=context.get("direccion"),
            ciudad="Lima"
        )
        db.session.add(nuevo_cliente)
        db.session.commit()

        telegram_service.edit_message(
            chat_id, 
            message_id, 
            f"✅ <b>¡Cliente registrado con éxito!</b>\n\n"
            f"<b>ID:</b> #{nuevo_cliente.id}\n"
            f"<b>Nombre:</b> {context['nombre']}\n"
            f"<b>Teléfono:</b> {context['telefono']}\n"
            f"<b>RUC:</b> {context.get('documento') or 'No especificado'}\n"
            f"<b>Dirección:</b> {context.get('direccion')}"
        )
