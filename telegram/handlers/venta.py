import os
import logging
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import func

from extensions import db
from models import Users, Cliente, PresentacionProducto, Inventario, Lote, Venta, VentaDetalle, Pago, Gasto, Movimiento, Almacen, Receta, ComponenteReceta
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

        import re
        if not phone_match and original_text:
            phone_match = re.search(r'\b(9\d{8})\b', original_text)
        if not ruc_match and original_text:
            ruc_match = re.search(r'\b(\d{11})\b', original_text)

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

    @staticmethod
    def prepare_ventas_lote(chat_id, user, args, original_text, resolver_almacen_fn, buscar_presentacion_fn):
        fecha_str = args.get("fecha")
        ventas_raw = args.get("ventas", [])

        if not ventas_raw:
            telegram_service.send_message(chat_id, "❌ Error: No se recibieron ventas en el lote.")
            return

        enriched_ventas = []
        warnings = []
        total_lote = Decimal("0")
        
        # Almacén por defecto
        user_almacen_id, user_almacen_nombre = resolver_almacen_fn(user, original_text)

        for v in ventas_raw:
            cliente_nombre = v.get("cliente_nombre")
            items_raw = v.get("items", [])
            if not cliente_nombre or not items_raw:
                continue

            # Buscar Cliente
            cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{cliente_nombre}%")).first()
            
            # Resolver Almacén
            almacen_id = user_almacen_id
            almacen_nombre = user_almacen_nombre
            if cliente and cliente.almacen_preferido_id:
                almacen_preferido = Almacen.query.get(cliente.almacen_preferido_id)
                if almacen_preferido:
                    almacen_id = almacen_preferido.id
                    almacen_nombre = almacen_preferido.nombre

            if not almacen_id:
                telegram_service.send_message(chat_id, "❌ Error: No se pudo determinar el almacén para el lote.")
                return

            enriched_items = []
            total_venta = Decimal("0")
            for item in items_raw:
                prod_name = item.get("producto_nombre")
                cantidad = Decimal(str(item.get("cantidad", 0)))
                if cantidad <= 0 or not prod_name:
                    continue

                presentacion = buscar_presentacion_fn(prod_name, ['procesado', 'briqueta', 'insumo'])
                if not presentacion:
                    telegram_service.send_message(chat_id, f"❌ Error: No se encontró la presentación '{prod_name}' en el catálogo.")
                    return

                # Validar stock disponible
                invs = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=presentacion.id).all()
                stock_disp = sum(inv.cantidad for inv in invs)
                if stock_disp < cantidad:
                    warnings.append(f"⚠️ Stock bajo en {almacen_nombre} para '{presentacion.nombre}'. Req: {cantidad}, Disp: {stock_disp}")

                precio = presentacion.precio_venta or Decimal("0")
                total_venta += cantidad * precio
                enriched_items.append({
                    "presentacion_id": presentacion.id,
                    "presentacion_nombre": presentacion.nombre,
                    "cantidad": float(cantidad),
                    "precio_unitario": float(precio)
                })

            if not enriched_items:
                continue

            total_lote += total_venta
            enriched_ventas.append({
                "cliente_id": cliente.id if cliente else None,
                "cliente_nombre_original": cliente_nombre,
                "cliente_nombre": cliente.nombre if cliente else cliente_nombre,
                "almacen_id": almacen_id,
                "almacen_nombre": almacen_nombre,
                "items": enriched_items,
                "total": float(total_venta)
            })

        if not enriched_ventas:
            telegram_service.send_message(chat_id, "❌ Error: No se interpretaron ventas válidas en el lote.")
            return

        user.telegram_context = {
            "action": "ventas_lote",
            "fecha": fecha_str,
            "ventas": enriched_ventas
        }
        db.session.commit()

        # Construir tarjeta resumen
        resumen_txt = []
        for i, ev in enumerate(enriched_ventas, 1):
            items_desc = ", ".join(f"{it['cantidad']}x {it['presentacion_nombre']}" for it in ev["items"])
            resumen_txt.append(f"{i}. <b>{ev['cliente_nombre']}</b> ({ev['almacen_nombre']}): {items_desc} - S/ {ev['total']:.2f}")

        resumen_str = "\n".join(resumen_txt)
        warnings_txt = "\n".join(warnings) if warnings else ""

        card = (
            f"📋 <b>Confirmar Lote de Ventas</b>\n\n"
            f"📅 <b>Fecha del Lote:</b> {fecha_str or 'Hoy'}\n\n"
            f"📦 <b>Ventas a Registrar:</b>\n{resumen_str}\n\n"
            f"💰 <b>Total Acumulado Lote:</b> S/ {total_lote:.2f}\n\n"
        )
        if warnings_txt:
            card += f"⚠️ <b>Alertas / Advertencias:</b>\n{warnings_txt}\n\n"
        card += "¿Confirmas el registro de este lote de ventas?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Lote", "callback_data": "confirm:ventas_lote"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_ventas_lote(chat_id, user, context, message_id):
        ventas_list = context.get("ventas", [])
        fecha_str = context.get("fecha")
        
        # Resolver fecha del lote
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

        ventas_registradas = []
        for v in ventas_list:
            cliente_id = v.get("cliente_id")
            cliente_nombre_original = v.get("cliente_nombre_original")
            almacen_id = v.get("almacen_id")
            items = v.get("items", [])
            total = Decimal(str(v.get("total", 0)))

            # 1. Auto-registro de cliente si no existe
            if not cliente_id:
                nuevo_cliente = Cliente(
                    nombre=cliente_nombre_original,
                    telefono="999999999", # Teléfono genérico
                    ciudad="Lima",
                    almacen_preferido_id=almacen_id
                )
                db.session.add(nuevo_cliente)
                db.session.flush()
                cliente_id = nuevo_cliente.id
                cliente_nombre = nuevo_cliente.nombre
            else:
                cliente = Cliente.query.get(cliente_id)
                cliente_nombre = cliente.nombre

            # 2. Registrar Venta reutilizando el VentaService
            detalles_data = [
                {
                    "presentacion_id": item["presentacion_id"],
                    "cantidad": Decimal(str(item["cantidad"])),
                    "precio_unitario": Decimal(str(item["precio_unitario"]))
                }
                for item in items
            ]

            nueva_venta = VentaService.crear_venta(
                vendedor_id=user.id,
                cliente_id=cliente_id,
                almacen_id=almacen_id,
                detalles_data=detalles_data,
                estado="completado",
                fecha=fecha_transaccion,
                monto_pago=Decimal("0"),
                metodo_pago="efectivo",
                monto_gasto=Decimal("0"),
                permitir_stock_negativo=True
            )

            ventas_registradas.append(f"• Venta #{nueva_venta.id} a <b>{cliente_nombre}</b> por S/ {total:.2f}")

        db.session.commit()

        detalles_txt = "\n".join(ventas_registradas)
        telegram_service.edit_message(
            chat_id,
            message_id,
            f"✅ <b>¡Lote de {len(ventas_list)} ventas registrado con éxito!</b>\n\n"
            f"📅 Fecha: {fecha_transaccion.strftime('%Y-%m-%d')}\n\n"
            f"<b>Resumen de Ventas creadas:</b>\n{detalles_txt}"
        )

    @staticmethod
    def prepare_produccion(chat_id, user, args, original_text, resolver_almacen_fn, buscar_presentacion_fn):
        producciones_raw = args.get("producciones")
        
        # Fallback si es unitario
        if not producciones_raw:
            prod_name = args.get("producto_nombre")
            cant = args.get("cantidad_a_producir")
            if prod_name and cant:
                producciones_raw = [{"producto_nombre": prod_name, "cantidad_a_producir": cant}]
                
        if not producciones_raw:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar el producto o la cantidad a producir.")
            return

        planta = Almacen.query.filter_by(es_planta=True).first()
        if not planta:
            planta = Almacen.query.filter(Almacen.nombre.ilike("%planta%")).first()
            
        if planta:
            almacen_id = planta.id
            almacen_nombre = planta.nombre
        else:
            almacen_id, almacen_nombre = resolver_almacen_fn(user, original_text)
            
        if not almacen_id:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo determinar el almacén de producción ('Planta').")
            return

        producciones_enriched = []
        warnings = []
        detalles_consumo = []

        for p_item in producciones_raw:
            producto_nombre = p_item.get("producto_nombre")
            cantidad_a_producir = Decimal(str(p_item.get("cantidad_a_producir", 0)))
            
            if cantidad_a_producir <= 0 or not producto_nombre:
                continue

            presentacion = buscar_presentacion_fn(producto_nombre, ['procesado', 'briqueta'])
            if not presentacion:
                telegram_service.send_message(chat_id, f"❌ Error: No se encontró la presentación '{producto_nombre}' de tipo procesado o briqueta en el catálogo.")
                return

            receta = Receta.query.filter_by(presentacion_id=presentacion.id).first()
            if not receta:
                telegram_service.send_message(chat_id, f"❌ Error: No se encontró una receta de producción para '{presentacion.nombre}'.")
                return

            lotes_seleccionados = []
            inherited_lote_desc = "Ninguno"
            inherited_lote_id = None
            
            for componente in receta.componentes:
                cantidad_req = Decimal(str(componente.cantidad_necesaria)) * cantidad_a_producir
                
                if componente.tipo_consumo == 'materia_prima':
                    lotes_disponibles = Lote.query.filter(
                        Lote.producto_id == componente.componente_presentacion.producto_id,
                        Lote.cantidad_disponible_kg > 0,
                        Lote.is_active == True
                    ).order_by(Lote.created_at.desc()).all()
                    
                    cantidad_acumulada = Decimal("0")
                    for lote in lotes_disponibles:
                        lote_disponible = Decimal(str(lote.cantidad_disponible_kg))
                        lotes_seleccionados.append({
                            "componente_presentacion_id": componente.componente_presentacion_id,
                            "lote_id": lote.id,
                            "cantidad_req_kg": float(cantidad_req)
                        })
                        
                        if inherited_lote_id is None:
                            inherited_lote_id = lote.id
                            inherited_lote_desc = lote.descripcion or lote.codigo_lote or f"Lote #{lote.id}"
                            
                        cantidad_acumulada += lote_disponible
                        detalles_consumo.append(f"• Consumir de '{componente.componente_presentacion.nombre}': Lote '{lote.descripcion or lote.codigo_lote or lote.id}' (Disp: {lote_disponible}kg)")
                        if cantidad_acumulada >= cantidad_req:
                            break
                            
                    if cantidad_acumulada < cantidad_req:
                        warnings.append(f"⚠️ Stock de materia prima '{componente.componente_presentacion.nombre}' es insuficiente. Req: {cantidad_req}kg, Disp: {cantidad_acumulada}kg.")
                
                elif componente.tipo_consumo == 'insumo':
                    invs_insumo = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=componente.componente_presentacion_id).all()
                    insumo_disponible = sum(Decimal(str(i.cantidad)) for i in invs_insumo) if invs_insumo else Decimal("0")
                    detalles_consumo.append(f"• Consumir insumo '{componente.componente_presentacion.nombre}': {cantidad_req} unidades (Disp: {insumo_disponible})")
                    if insumo_disponible < cantidad_req:
                        warnings.append(f"⚠️ Stock de insumo '{componente.componente_presentacion.nombre}' es insuficiente. Req: {cantidad_req}, Disp: {insumo_disponible}.")
            
            producciones_enriched.append({
                "presentacion_id": presentacion.id,
                "presentacion_nombre": presentacion.nombre,
                "cantidad_a_producir": float(cantidad_a_producir),
                "lotes_seleccionados": lotes_seleccionados,
                "lote_destino_id": inherited_lote_id,
                "lote_destino_desc": inherited_lote_desc
            })

        if not producciones_enriched:
            telegram_service.send_message(chat_id, "❌ Error: No se interpretó ninguna producción válida.")
            return

        context_data = {
            "action": "produccion",
            "producciones": producciones_enriched,
            "almacen_id": almacen_id,
            "almacen_nombre": almacen_nombre
        }
        user.telegram_context = context_data
        db.session.commit()

        prod_txt = "\n".join([f"• {p['cantidad_a_producir']}x {p['presentacion_nombre']} (Asociado a lote: '{p['lote_destino_desc']}')" for p in producciones_enriched])
        warnings_txt = "\n".join(warnings) if warnings else ""

        card = (
            f"📋 <b>Confirmar Registro de Producción</b>\n\n"
            f"🏪 <b>Almacén Destino:</b> {almacen_nombre}\n\n"
            f"📦 <b>Productos a Fabricar:</b>\n{prod_txt}\n\n"
        )
        if warnings_txt:
            card += f"⚠️ <b>Alertas de Stock:</b>\n{warnings_txt}\n\n"
        card += "¿Confirmas la fabricación de estos productos?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Producción", "callback_data": "confirm:produccion"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_produccion(chat_id, user, context, message_id):
        producciones_list = context.get("producciones")
        
        if not producciones_list:
            producciones_list = [{
                "presentacion_id": context["presentacion_id"],
                "presentacion_nombre": context.get("presentacion_nombre"),
                "cantidad_a_producir": context["cantidad_a_producir"],
                "lotes_seleccionados": context["lotes_seleccionados"],
                "lote_destino_id": context["lotes_seleccionados"][0]["lote_id"] if context["lotes_seleccionados"] else None,
                "lote_destino_desc": f"Lote #{context['lotes_seleccionados'][0]['lote_id']}" if context["lotes_seleccionados"] else "Ninguno"
            }]

        almacen_id = context.get("almacen_id", user.almacen_id)
        almacen_nombre = context.get("almacen_nombre", "Desconocido")
        fecha_operacion = datetime.now(timezone.utc)
        
        fabricados = []
        
        for p in producciones_list:
            final_id = p["presentacion_id"]
            cant = Decimal(str(p["cantidad_a_producir"]))
            lotes_sel = p["lotes_seleccionados"]
            lote_dest_id = p["lote_destino_id"]
            lote_dest_desc = p["lote_destino_desc"]

            receta = Receta.query.filter_by(presentacion_id=final_id).first()
            if not receta:
                raise ValueError(f"No se encontró una receta para la presentación ID {final_id}")

            motivo_base = f"Ensamblaje Telegram: Fabricación de {cant} unidades de {receta.presentacion.nombre}"

            # Descontar de lotes
            for item in lotes_sel:
                componente_pres_id = item["componente_presentacion_id"]
                lote_id = item["lote_id"]
                cantidad_req_kg = Decimal(str(item.get("cantidad_req_kg", 0)))
                
                if cantidad_req_kg <= 0:
                    comp = next(c for c in receta.componentes if c.componente_presentacion_id == componente_pres_id)
                    cantidad_req_kg = comp.cantidad_necesaria * cant

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

            # Descontar insumos sin lote
            for comp in receta.componentes:
                if comp.tipo_consumo == 'insumo':
                    cantidad_req_insumo = comp.cantidad_necesaria * cant
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

            # Registrar Entrada de Producto Final
            inv_destino = Inventario.query.filter_by(
                presentacion_id=final_id,
                almacen_id=almacen_id,
                lote_id=lote_dest_id
            ).first()

            if inv_destino:
                inv_destino.cantidad += cant
                inv_destino.ultima_actualizacion = fecha_operacion
            else:
                inv_destino = Inventario(
                    presentacion_id=final_id,
                    almacen_id=almacen_id,
                    lote_id=lote_dest_id,
                    cantidad=cant
                )
                db.session.add(inv_destino)

            db.session.add(Movimiento(
                tipo='entrada',
                presentacion_id=final_id,
                lote_id=lote_dest_id,
                cantidad=cant,
                fecha=fecha_operacion,
                motivo=motivo_base,
                usuario_id=user.id,
                tipo_operacion='ensamblaje'
            ))
            
            fabricados.append(f"• {cant}x {p['presentacion_nombre']} (Asociado a lote '{lote_dest_desc}')")

        db.session.commit()
        
        detalles_txt = "\n".join(fabricados)
        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Producción registrada con éxito!</b>\n\n<b>Almacén:</b> {almacen_nombre}\n\n<b>Productos fabricados:</b>\n{detalles_txt}")
