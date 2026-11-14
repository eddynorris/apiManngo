"""Guía de remisión (GRE remitente) desde Telegram.

La tarjeta muestra exactamente lo que se firmará (``guia_service``). Nada se
inventa: si falta un dato, la tarjeta lo dice y no ofrece «Emitir». En un
traslado, al emitir se registra también la transferencia de inventario y sus
movimientos quedan enlazados a la guía.
"""
import logging
import re
import uuid
from datetime import date

from extensions import db
from models import Almacen, Cliente, Conductor, Vehiculo
from services import guia_service
from services.authorization import autorizar_almacen
from services.telegram_service import telegram_service
from telegram.state_machine import StateMachine

logger = logging.getLogger(__name__)

TIPOS_DOC = {"1": "DNI", "4": "CE", "6": "RUC", "7": "Pasaporte"}


def _fecha_ddmmaaaa(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(iso)


def _num(valor) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    return f"{numero:,.0f}" if numero == int(numero) else f"{numero:,.2f}"


def construir_tarjeta_guia(contexto, usuario):
    b = contexto["borrador"]
    origen = db.session.get(Almacen, b.get("almacen_origen_id") or 0)
    destino = db.session.get(Almacen, b.get("almacen_destino_id") or 0) if b.get("almacen_destino_id") else None
    cliente = db.session.get(Cliente, b.get("destinatario_cliente_id") or 0) if b.get("destinatario_cliente_id") else None
    vehiculo = db.session.get(Vehiculo, b.get("vehiculo_id") or 0) if b.get("vehiculo_id") else None
    conductor = db.session.get(Conductor, b.get("conductor_id") or 0) if b.get("conductor_id") else None
    es_traslado = b.get("origen_tipo") == "traslado"

    if b.get("motivo") == guia_service.MOTIVO_TRASLADO_PROPIO:
        motivo_txt = "Traslado entre establecimientos de la misma empresa"
        destinatario_txt = "La propia empresa"
    else:
        motivo_txt = "Venta"
        destinatario_txt = (
            f"{cliente.nombre} ({TIPOS_DOC.get(cliente.tipo_documento, 'doc.')} {cliente.ruc or '—'})"
            if cliente else "— sin destinatario —"
        )
    llegada_txt = destino.nombre if es_traslado and destino else (cliente.nombre if cliente else "—")

    bienes_txt = "\n".join(
        f"• {bien['descripcion']} = {_num(bien.get('peso_kg'))} kg" for bien in b.get("bienes", [])
    )
    conductor_txt = (
        f"{conductor.apellidos or ''} {conductor.nombres or ''}".strip() or conductor.numero_documento
        if conductor else "—"
    )

    texto = (
        "🚚 <b>Guía de Remisión Electrónica (borrador)</b>\n\n"
        f"📤 <b>Partida:</b> {origen.nombre if origen else '—'}\n"
        f"📥 <b>Llegada:</b> {llegada_txt}\n"
        f"📝 <b>Motivo:</b> {motivo_txt}\n"
        f"👤 <b>Destinatario:</b> {destinatario_txt}\n"
        f"📦 <b>Bienes:</b>\n{bienes_txt}\n"
        f"⚖️ <b>Peso bruto total:</b> {_num(b.get('peso_total_kg'))} kg\n"
        f"🚛 <b>Vehículo:</b> {vehiculo.placa if vehiculo else '—'}\n"
        f"🪪 <b>Conductor:</b> {conductor_txt}\n"
        f"📅 <b>Inicio del traslado:</b> {_fecha_ddmmaaaa(b.get('fecha_inicio_traslado'))}\n"
    )
    if es_traslado:
        texto += (f"\n📦 Al emitir se registra también el traslado de inventario "
                  f"{origen.nombre if origen else ''} → {destino.nombre if destino else ''}.\n")

    falta = guia_service.faltantes(b)
    habilitado = guia_service.envio_habilitado()
    if falta:
        texto += "\n⚠️ <b>Falta completar:</b>\n" + "\n".join(f"• {f}" for f in falta) + "\n"
    if not habilitado:
        texto += "\n⛔ El envío a SUNAT está deshabilitado: todavía no se pueden emitir guías.\n"

    filas = [
        [{"text": "🚛 Vehículo", "callback_data": "guia:vehiculo"},
         {"text": "🪪 Conductor", "callback_data": "guia:conductor"}],
        [{"text": "📅 Fecha de inicio", "callback_data": "guia:fecha"}],
    ]
    if es_traslado and b.get("motivo") == guia_service.MOTIVO_VENTA:
        filas[1].append({"text": "👤 Destinatario", "callback_data": "guia:destinatario"})
    ultima = []
    if not falta and habilitado:
        ultima.append({"text": "✅ Emitir Guía", "callback_data": "confirm:guia_remision"})
    ultima.append({"text": "❌ Cancelar", "callback_data": "cancel"})
    filas.append(ultima)
    return texto, {"inline_keyboard": filas}


def _message_id(respuesta):
    if isinstance(respuesta, dict) and isinstance(respuesta.get("result"), dict):
        return respuesta["result"].get("message_id")
    return None


def enviar_tarjeta_guia(chat_id, user, contexto, message_id_anterior=None):
    texto, teclado = construir_tarjeta_guia(contexto, user)
    nuevo_id = _message_id(telegram_service.send_message(chat_id, texto, teclado))
    if message_id_anterior:
        telegram_service.edit_message(
            chat_id, message_id_anterior, "✏️ <i>Guía actualizada: revisa la tarjeta de abajo.</i>",
            reply_markup=None,
        )
    StateMachine.set_awaiting_confirmation(user, "guia_remision", contexto, nuevo_id or message_id_anterior)


def _almacen_por_nombre(nombre):
    if not nombre or not str(nombre).strip():
        return None
    return Almacen.query.filter(Almacen.nombre.ilike(f"%{str(nombre).strip()}%")).order_by(Almacen.id).first()


def _vehiculo_por_placa(placa):
    normal = re.sub(r"[\s-]", "", str(placa)).upper()
    return Vehiculo.query.filter_by(placa=normal, activo=True).first()


def _conductor_por_texto(texto):
    valor = str(texto).strip()
    if not valor:
        return None
    exacto = Conductor.query.filter_by(numero_documento=valor, activo=True).first()
    if exacto:
        return exacto
    patron = f"%{valor}%"
    return Conductor.query.filter(
        Conductor.activo.is_(True),
        (Conductor.nombres.ilike(patron)) | (Conductor.apellidos.ilike(patron)),
    ).order_by(Conductor.id).first()


def _sin_permiso(chat_id):
    telegram_service.send_message(chat_id, "⛔ No tienes permiso sobre ese almacén para hacer la guía.")


class GuiaSunatHandler:
    @staticmethod
    def prepare_guia_remision(chat_id, user, args, text, resolver_almacen_fn, buscar_presentacion_fn):
        args = args or {}
        try:
            vehiculo_id = conductor_id = None
            if args.get("placa"):
                vehiculo = _vehiculo_por_placa(args["placa"])
                if not vehiculo:
                    telegram_service.send_message(chat_id, f"❌ No encontré el vehículo con placa {args['placa']}.")
                    return
                vehiculo_id = vehiculo.id
            if args.get("conductor"):
                conductor = _conductor_por_texto(args["conductor"])
                if not conductor:
                    telegram_service.send_message(chat_id, f"❌ No encontré al conductor «{args['conductor']}».")
                    return
                conductor_id = conductor.id

            if args.get("venta_id"):
                from models import Venta
                venta = db.session.get(Venta, int(args["venta_id"]))
                if venta is None:
                    telegram_service.send_message(chat_id, f"❌ No existe la venta #{args['venta_id']}.")
                    return
                try:
                    autorizar_almacen(user.id, venta.almacen_id)
                except Exception:
                    _sin_permiso(chat_id)
                    return
                borrador = guia_service.borrador_venta(
                    user, venta.id, fecha_inicio=args.get("fecha_inicio"),
                    vehiculo_id=vehiculo_id, conductor_id=conductor_id,
                )
            else:
                destino = _almacen_por_nombre(args.get("almacen_destino"))
                if destino is None:
                    telegram_service.send_message(
                        chat_id, "❌ Indica el almacén de destino de la guía (p. ej. «a Andahuaylas»)."
                    )
                    return
                origen = _almacen_por_nombre(args.get("almacen_origen"))
                if origen is None:
                    origen = Almacen.query.filter_by(es_planta=True).order_by(Almacen.id).first()
                if origen is None and user.almacen_id:
                    origen = db.session.get(Almacen, user.almacen_id)
                if origen is None:
                    telegram_service.send_message(chat_id, "❌ No pude determinar el almacén de partida.")
                    return
                try:
                    autorizar_almacen(user.id, origen.id)
                    autorizar_almacen(user.id, destino.id)
                except Exception:
                    _sin_permiso(chat_id)
                    return

                items = []
                for item in args.get("items") or []:
                    nombre = item.get("producto_nombre")
                    presentacion = buscar_presentacion_fn(nombre, ['procesado', 'briqueta', 'insumo'])
                    if presentacion is None:
                        telegram_service.send_message(chat_id, f"❌ No encontré el producto «{nombre}».")
                        return
                    items.append({"presentacion_id": presentacion.id, "cantidad": item.get("cantidad")})
                if not items:
                    telegram_service.send_message(chat_id, "❌ Indica qué productos y cantidades van en la guía.")
                    return
                borrador = guia_service.borrador_traslado(
                    user, origen.id, destino.id, items, fecha_inicio=args.get("fecha_inicio"),
                    vehiculo_id=vehiculo_id, conductor_id=conductor_id,
                )
        except guia_service.GuiaError as exc:
            telegram_service.send_message(chat_id, f"❌ {exc}")
            return

        contexto = {"action": "guia_remision", "borrador": borrador, "idempotency_key": uuid.uuid4().hex}
        enviar_tarjeta_guia(chat_id, user, contexto)

    @staticmethod
    def execute_guia_remision(chat_id, user, context, message_id):
        borrador = context["borrador"]
        clave = context["idempotency_key"]

        def responder(texto):
            telegram_service.edit_message(chat_id, message_id, texto)

        falta = guia_service.faltantes(borrador)
        if falta:
            StateMachine.clear_context(user)
            responder("❌ <b>No se emitió la guía.</b> Falta:\n" + "\n".join(f"• {f}" for f in falta))
            return
        if not guia_service.envio_habilitado():
            StateMachine.clear_context(user)
            responder("⛔ <b>No se emitió la guía:</b> el envío a SUNAT está deshabilitado.")
            return

        movimiento_ids = None
        if borrador.get("origen_tipo") == "traslado":
            from resources.transferencia_resource import TransferenciaService
            payload = {
                "almacen_origen_id": borrador["almacen_origen_id"],
                "almacen_destino_id": borrador["almacen_destino_id"],
                "transferencias": [
                    {"presentacion_id": b["presentacion_id"], "cantidad": b["cantidad"]}
                    for b in borrador["bienes"]
                ],
            }
            claims = {"sub": user.id, "rol": user.rol, "almacen_id": user.almacen_id}
            try:
                servicio = TransferenciaService(payload, claims=claims)
                servicio.ejecutar_transferencia()
                movimiento_ids = list(servicio.movimiento_ids)
            except Exception as exc:
                db.session.rollback()
                StateMachine.clear_context(user)
                detalle = str(exc) if isinstance(exc, ValueError) else "error al mover el inventario"
                responder(f"❌ <b>No se registró el traslado ni la guía:</b> {detalle}")
                return

        try:
            guia = guia_service.emitir(borrador, user, clave, chat_id=chat_id, movimiento_ids=movimiento_ids)
        except guia_service.GuiaError as exc:
            db.session.rollback()
            StateMachine.clear_context(user)
            responder(f"❌ <b>No se pudo emitir la guía:</b> {exc}")
            return

        StateMachine.clear_context(user)
        numero = guia_service.numero_completo(guia)
        traslado_txt = "\n📦 Traslado de inventario registrado." if movimiento_ids else ""
        if guia.estado == guia_service.ESTADO_ENVIADA:
            responder(
                f"📡 <b>Guía {numero} enviada a SUNAT</b> (ticket {guia.ticket}).{traslado_txt}\n"
                "Te aviso aquí con el PDF y el QR cuando SUNAT la acepte (unos minutos). "
                f"También puedes escribir <code>/guia {numero}</code>."
            )
        elif guia.estado == guia_service.ESTADO_INCIERTA:
            responder(
                f"⚠️ <b>Guía {numero} quedó incierta</b> (no hubo respuesta clara de SUNAT).{traslado_txt}\n"
                "No se sabe si SUNAT la recibió. Revísala en SUNAT SOL antes de emitir otra; "
                "no la vuelvas a emitir con otro número."
            )
        else:
            responder(
                f"❌ <b>No se pudo enviar la guía {numero} a SUNAT.</b>{traslado_txt}\n"
                f"{guia.cdr_mensaje or ''}"
            )
