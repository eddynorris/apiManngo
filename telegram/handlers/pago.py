import os
import logging
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import func

from extensions import db
from models import Users, Cliente, PresentacionProducto, Inventario, Pago, Gasto, Movimiento, Almacen
from services.telegram_service import telegram_service
from services.pago_service import PagoService, PagoValidationError

logger = logging.getLogger(__name__)

class PagoHandler:
    @staticmethod
    def prepare_pago(chat_id, user, args, resolver_almacen_fn):
        cliente_nombre = args.get("cliente_nombre")
        monto = args.get("monto")
        metodo_pago = args.get("metodo_pago", "efectivo")
        referencia = args.get("referencia")

        if not monto:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar el monto del pago.")
            return

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

        saldo = float(cliente.saldo_pendiente)
        if saldo <= 0:
            warnings.append(f"⚠️ El cliente {cliente.nombre} no tiene deudas pendientes en el sistema.")

        fecha = args.get("fecha")
        context_data = {
            "action": "pago",
            "cliente_id": cliente.id,
            "cliente_nombre": cliente.nombre,
            "monto": float(monto),
            "metodo_pago": metodo_pago,
            "referencia": referencia,
            "saldo_pendiente": saldo,
            "fecha": fecha
        }
        user.telegram_context = context_data
        db.session.commit()

        warnings_txt = "\n".join(warnings) if warnings else ""
        fecha_txt = f"📅 <b>Fecha:</b> {fecha}\n" if fecha else ""
        card = (
            f"📋 <b>Confirmar Pago / Abono de Cliente</b>\n\n"
            f"👤 <b>Cliente:</b> {cliente.nombre}\n"
            f"💵 <b>Monto Acreditar:</b> S/ {float(monto):.2f}\n"
            f"💳 <b>Método:</b> {metodo_pago.upper()}\n"
            f"🔖 <b>Referencia:</b> {referencia or 'Sin referencia'}\n"
            f"📉 <b>Deuda Actual:</b> S/ {saldo:.2f}\n"
            f"{fecha_txt}\n"
        )
        if warnings_txt:
            card += f"{warnings_txt}\n\n"

        card += "¿Confirmas el ingreso de este pago en la cuenta del cliente?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Pago", "callback_data": "confirm:pago"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_pago(chat_id, user, context, message_id):
        cliente_id = context["cliente_id"]
        monto = Decimal(str(context["monto"]))
        metodo_pago = context["metodo_pago"]
        referencia = context["referencia"]

        fecha_str = context.get("fecha")
        if fecha_str:
            try:
                fecha_parsed = datetime.strptime(fecha_str, "%Y-%m-%d")
                ahora = datetime.now()
                fecha_pago = datetime(
                    fecha_parsed.year, fecha_parsed.month, fecha_parsed.day,
                    ahora.hour, ahora.minute, ahora.second, ahora.microsecond, ahora.tzinfo
                )
            except Exception:
                fecha_pago = datetime.now()
        else:
            fecha_pago = datetime.now()

        cliente = db.session.get(Cliente, cliente_id)
        from models import Venta
        ventas_pendientes = Venta.query.filter(
            Venta.cliente_id == cliente_id,
            Venta.estado_pago.in_(['pendiente', 'parcial'])
        ).order_by(Venta.fecha.asc()).all()

        monto_restante = monto
        pagos_registrados = []

        for venta in ventas_pendientes:
            if monto_restante <= 0:
                break

            saldo_v = venta.saldo_pendiente
            if saldo_v <= 0:
                continue

            monto_aplicar = min(monto_restante, saldo_v)
            nuevo_pago = Pago(
                venta_id=venta.id,
                usuario_id=user.id,
                monto=monto_aplicar,
                fecha=fecha_pago,
                metodo_pago=metodo_pago,
                referencia=referencia
            )
            PagoService.create_pago(nuevo_pago, None, user.id)
            monto_restante -= monto_aplicar
            pagos_registrados.append(f"• Venta #{venta.id}: S/ {monto_aplicar:.2f}")

        db.session.commit()
        detalles_txt = "\n".join(pagos_registrados) if pagos_registrados else "No se encontraron ventas pendientes. El saldo fue registrado."
        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Pago registrado con éxito!</b>\n\n<b>Cliente:</b> {context['cliente_nombre']}\n<b>Monto Total:</b> S/ {monto:.2f}\n<b>Distribución:</b>\n{detalles_txt}")

    @staticmethod
    def prepare_gasto(chat_id, user, args, original_text, resolver_almacen_fn):
        gastos_raw = args.get("gastos")
        if not gastos_raw:
            descripcion = args.get("descripcion")
            monto = args.get("monto")
            categoria = args.get("categoria", "logistica")
            if descripcion and monto:
                gastos_raw = [{"descripcion": descripcion, "monto": monto, "categoria": categoria}]
        
        if not gastos_raw:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar la descripción o el monto de los gastos.")
            return

        almacen_id, almacen_nombre = resolver_almacen_fn(user, original_text)
        if not almacen_id:
            telegram_service.send_message(chat_id, "❌ Error: Especifica el almacén en tu mensaje ya que no tienes uno por defecto asignado.")
            return

        gastos_normalized = []
        total_monto = Decimal("0")
        for g in gastos_raw:
            desc = g.get("descripcion")
            monto = Decimal(str(g.get("monto", 0)))
            cat = g.get("categoria", "logistica")
            if desc and monto > 0:
                gastos_normalized.append({
                    "descripcion": desc,
                    "monto": float(monto),
                    "categoria": cat
                })
                total_monto += monto

        if not gastos_normalized:
            telegram_service.send_message(chat_id, "❌ Error: No se encontraron gastos válidos con montos mayores a cero.")
            return

        fecha = args.get("fecha")
        context_data = {
            "action": "gasto",
            "gastos": gastos_normalized,
            "almacen_id": almacen_id,
            "almacen_nombre": almacen_nombre,
            "total_monto": float(total_monto),
            "fecha": fecha
        }
        user.telegram_context = context_data
        db.session.commit()

        gastos_txt = "\n".join([f"• {g['descripcion']}: S/ {g['monto']:.2f} ({g['categoria']})" for g in gastos_normalized])
        fecha_txt = f"📅 <b>Fecha:</b> {fecha}\n" if fecha else ""

        card = (
            f"📋 <b>Confirmar Registro de Gastos</b>\n\n"
            f"🏪 <b>Almacén:</b> {almacen_nombre}\n"
            f"💵 <b>Detalle de Gastos:</b>\n{gastos_txt}\n\n"
            f"{fecha_txt}"
            f"💰 <b>Total Acumulado:</b> S/ {total_monto:.2f}\n\n"
            f"¿Confirmas el registro de estos gastos?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Gastos", "callback_data": "confirm:gasto"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_gasto(chat_id, user, context, message_id):
        gastos_list = context.get("gastos")
        if not gastos_list:
            gastos_list = [{
                "descripcion": context["descripcion"],
                "monto": context["monto"],
                "categoria": context["categoria"]
            }]
            
        almacen_id = context.get("almacen_id", user.almacen_id)
        almacen_nombre = context.get("almacen_nombre", "Desconocido")
        
        fecha_str = context.get("fecha")
        if fecha_str:
            try:
                fecha_gasto = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            except Exception:
                fecha_gasto = datetime.now().date()
        else:
            fecha_gasto = datetime.now().date()
        
        registros_creados = []
        for g in gastos_list:
            nuevo_gasto = Gasto(
                descripcion=g["descripcion"],
                monto=Decimal(str(g["monto"])),
                categoria=g["categoria"],
                fecha=fecha_gasto,
                usuario_id=user.id,
                almacen_id=almacen_id
            )
            db.session.add(nuevo_gasto)
            db.session.flush()
            registros_creados.append(f"• #{nuevo_gasto.id} - {g['descripcion']}: S/ {g['monto']:.2f}")

        db.session.commit()
        detalles = "\n".join(registros_creados)
        telegram_service.edit_message(chat_id, message_id, f"✅ <b>¡Gastos registrados con éxito!</b>\n\n<b>Almacén:</b> {almacen_nombre}\n\n<b>Detalle:</b>\n{detalles}\n\n<b>Total:</b> S/ {context.get('total_monto', 0):.2f}")

    @staticmethod
    def prepare_deposito(chat_id, user, args):
        monto_depositado = args.get("monto_depositado")
        referencia = args.get("referencia")

        if not monto_depositado:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar el monto del depósito.")
            return

        context_data = {
            "action": "deposito",
            "monto_depositado": float(monto_depositado),
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

    @staticmethod
    def execute_deposito(chat_id, user, context, message_id):
        monto_depositado = Decimal(str(context["monto_depositado"]))
        referencia = context["referencia"]

        # Buscar pagos en efectivo o no depositados (depositado = False)
        pagos_pendientes = Pago.query.filter(
            Pago.depositado == False,
            Pago.metodo_pago == 'efectivo'
        ).order_by(Pago.fecha.asc()).all()

        if not pagos_pendientes:
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

    @staticmethod
    def prepare_compra_insumos(chat_id, user, args, original_text, resolver_almacen_fn, buscar_presentacion_fn):
        items_raw = args.get("items", [])
        if not items_raw:
            telegram_service.send_message(chat_id, "❌ Error: No se pudo interpretar la lista de insumos comprados.")
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

        items_enriched = []
        total_gasto = Decimal("0")

        for item in items_raw:
            prod_name = item.get("producto_nombre")
            cantidad = Decimal(str(item.get("cantidad", 1)))
            monto_compra = item.get("monto_compra")

            if not prod_name or cantidad <= 0:
                continue

            presentacion = buscar_presentacion_fn(prod_name, ['insumo'])
            if not presentacion:
                telegram_service.send_message(chat_id, f"❌ Error: No se encontró el insumo '{prod_name}' en el catálogo.")
                return

            monto_item = Decimal(str(monto_compra)) if monto_compra is not None else Decimal("0")
            total_gasto += monto_item

            items_enriched.append({
                "producto_id": presentacion.id,
                "producto_nombre": presentacion.nombre,
                "cantidad": float(cantidad),
                "monto_compra": float(monto_item)
            })

        if not items_enriched:
            telegram_service.send_message(chat_id, "❌ Error: No se encontraron insumos válidos en el mensaje.")
            return

        context_data = {
            "action": "compra_insumos",
            "items": items_enriched,
            "almacen_id": almacen_id,
            "almacen_nombre": almacen_nombre,
            "total_gasto": float(total_gasto)
        }
        user.telegram_context = context_data
        db.session.commit()

        items_txt = "\n".join([f"• {item['cantidad']}x {item['producto_nombre']}" + (f" (Costo: S/ {item['monto_compra']:.2f})" if item['monto_compra'] > 0 else "") for item in items_enriched])

        card = (
            f"📋 <b>Confirmar Compra de Insumos</b>\n\n"
            f"🏪 <b>Almacén:</b> {almacen_nombre}\n"
            f"📦 <b>Insumos a Ingresar:</b>\n{items_txt}\n\n"
            f"💸 <b>Gasto Total (Insumos):</b> S/ {total_gasto:.2f}\n\n"
            f"¿Confirmas el ingreso al stock y el registro del gasto?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirmar Compra", "callback_data": "confirm:compra_insumos"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_compra_insumos(chat_id, user, context, message_id):
        items = context["items"]
        almacen_id = context.get("almacen_id", user.almacen_id)
        almacen_nombre = context.get("almacen_nombre", "Desconocido")
        total_gasto = Decimal(str(context.get("total_gasto", 0)))
        fecha_operacion = datetime.now()

        gasto_id = None
        if total_gasto > 0:
            nuevo_gasto = Gasto(
                descripcion=f"Compra de insumos: " + ", ".join([f"{item['cantidad']} {item['producto_nombre']}" for item in items]),
                monto=total_gasto,
                categoria="insumos",
                fecha=fecha_operacion.date(),
                usuario_id=user.id,
                almacen_id=almacen_id
            )
            db.session.add(nuevo_gasto)
            db.session.flush()
            gasto_id = nuevo_gasto.id

        insumos_ingresados = []
        for item in items:
            prod_id = item["producto_id"]
            cantidad = Decimal(str(item["cantidad"]))

            inv = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=prod_id, lote_id=None).first()
            if inv:
                inv.cantidad += cantidad
                inv.ultima_actualizacion = fecha_operacion
            else:
                inv = Inventario(
                    presentacion_id=prod_id,
                    almacen_id=almacen_id,
                    lote_id=None,
                    cantidad=cantidad,
                    ultima_actualizacion=fecha_operacion
                )
                db.session.add(inv)

            db.session.add(Movimiento(
                tipo='entrada',
                presentacion_id=prod_id,
                lote_id=None,
                cantidad=cantidad,
                fecha=fecha_operacion,
                motivo=f"Compra de insumos (Telegram)" + (f" | Gasto #{gasto_id}" if gasto_id else ""),
                usuario_id=user.id,
                tipo_operacion='compra'
            ))

            insumos_ingresados.append(f"• {cantidad}x {item['producto_nombre']}")

        db.session.commit()

        detalles_txt = "\n".join(insumos_ingresados)
        gasto_info = f"\n💸 <b>Gasto Registrado:</b> S/ {total_gasto:.2f} (Gasto ID: #{gasto_id})" if gasto_id else ""
        
        telegram_service.edit_message(
            chat_id, 
            message_id, 
            f"✅ <b>¡Compra de insumos registrada con éxito!</b>\n\n"
            f"🏪 <b>Almacén:</b> {almacen_nombre}\n\n"
            f"📦 <b>Insumos ingresados al stock:</b>\n{detalles_txt}\n"
            f"{gasto_info}"
        )
