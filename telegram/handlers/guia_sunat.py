import os
import time
import logging
from decimal import Decimal
from datetime import datetime, timezone

from extensions import db
from models import Cliente, Almacen
from services.telegram_service import telegram_service
from services.sunat_service import sunat_service

logger = logging.getLogger(__name__)

class GuiaSunatHandler:
    @staticmethod
    def prepare_guia_remision(chat_id, user, args, text, resolver_almacen_fn, buscar_presentacion_fn):
        items_raw = args.get("items", [])
        dest_doc = str(args.get("destinatario_documento", "")).strip()
        motivo = args.get("motivo_traslado", "venta")
        placa = args.get("placa_vehiculo")
        chofer_dni = args.get("conductor_documento")

        if not items_raw or not dest_doc:
            telegram_service.send_message(chat_id, "❌ Error: Debes especificar al menos un producto y el RUC/DNI del destinatario.")
            return

        items_validados = []
        warnings = []
        for item in items_raw:
            prod_name = item.get("producto_nombre")
            cant = Decimal(str(item.get("cantidad", 1)))
            
            presentacion = buscar_presentacion_fn(prod_name, ['procesado', 'briqueta'])
            if not presentacion:
                warnings.append(f"⚠️ No se encontró la presentación '{prod_name}'. Se usará el nombre crudo.")
                items_validados.append({
                    "presentacion_id": None,
                    "presentacion_nombre": prod_name,
                    "cantidad": float(cant),
                    "peso_total_kg": float(cant * 20.0)
                })
            else:
                items_validados.append({
                    "presentacion_id": presentacion.id,
                    "presentacion_nombre": presentacion.nombre,
                    "cantidad": float(cant),
                    "peso_total_kg": float(cant * presentacion.capacidad_kg)
                })

        cliente = Cliente.query.filter_by(ruc=dest_doc).first()
        if not cliente:
            cliente = Cliente.query.filter(
                (Cliente.telefono.ilike(f"%{dest_doc}%")) | 
                (Cliente.direccion.ilike(f"%{dest_doc}%"))
            ).first()

        if not cliente:
            cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{dest_doc}%")).first()

        if cliente and cliente.ruc:
            dest_doc = cliente.ruc

        dest_nombre = cliente.nombre if cliente else "Cliente Externo"
        dest_direccion = cliente.direccion if (cliente and cliente.direccion) else os.environ.get("SUNAT_DEFAULT_LLEGADA_DIRECCION", "PSJE MANUEL ODRIA SN - TAMBURCO - ABANCAY - APURIMAC").strip()
        dest_ciudad = cliente.ciudad if (cliente and cliente.ciudad) else "Tamburco"

        partida_direccion = os.environ.get("SUNAT_DEFAULT_PARTIDA_DIRECCION", "PANAMERICANA KM 384 - COLCABAMBA - AYMARAES - APURIMAC").strip()
        if not partida_direccion:
            planta = Almacen.query.filter_by(es_planta=True).first()
            partida_direccion = planta.direccion if (planta and planta.direccion) else "PANAMERICANA KM 384 - COLCABAMBA - AYMARAES - APURIMAC"
        partida_ciudad = "Colcabamba"

        if not placa:
            placa = os.environ.get("SUNAT_DEFAULT_PLACA", "D8M790").strip()
        if not chofer_dni:
            chofer_dni = os.environ.get("SUNAT_DEFAULT_CHOFER_DNI", "31033519").strip()

        context_data = {
            "action": "guia_remision",
            "items": items_validados,
            "destinatario_documento": dest_doc,
            "destinatario_nombre": dest_nombre,
            "direccion_llegada": dest_direccion,
            "ciudad_llegada": dest_ciudad,
            "direccion_partida": partida_direccion,
            "ciudad_partida": partida_ciudad,
            "ubigeo_partida": os.environ.get("SUNAT_DEFAULT_PARTIDA_UBIGEO", "030303").strip(),
            "ubigeo_llegada": os.environ.get("SUNAT_DEFAULT_LLEGADA_UBIGEO", "030102").strip(),
            "motivo_traslado": motivo,
            "placa_vehiculo": placa,
            "conductor_documento": chofer_dni
        }

        user.telegram_context = context_data
        db.session.commit()

        prod_txt = "\n".join([f"• {item['cantidad']}x {item['presentacion_nombre']} (Peso tot: {item['peso_total_kg']} kg)" for item in items_validados])
        warnings_txt = "\n".join(warnings) if warnings else ""

        card = (
            f"📋 <b>Confirmar Guía de Remisión (SUNAT)</b>\n\n"
            f"🏢 <b>Destinatario Doc:</b> {dest_doc}\n"
            f"👤 <b>Nombre:</b> {dest_nombre}\n"
            f"📍 <b>Partida:</b> {partida_direccion}\n"
            f"🏁 <b>Llegada:</b> {dest_direccion}\n"
            f"🚛 <b>Placa:</b> {placa}\n"
            f"🪪 <b>DNI Chofer:</b> {chofer_dni}\n"
            f"📝 <b>Motivo:</b> {motivo.capitalize()}\n\n"
            f"📦 <b>Bienes a Trasladar:</b>\n{prod_txt}\n\n"
        )
        if warnings_txt:
            card += f"{warnings_txt}\n\n"
        card += "¿Confirmas la emisión electrónica de la guía en SUNAT?"

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Emitir Guía", "callback_data": "confirm:guia_remision"},
                    {"text": "❌ Cancelar", "callback_data": "cancel"}
                ]
            ]
        }
        telegram_service.send_message(chat_id, card, reply_markup)

    @staticmethod
    def execute_guia_remision(chat_id, user, context, message_id):
        items = context["items"]
        dest_doc = context["destinatario_documento"]
        dest_nombre = context["destinatario_nombre"]
        dir_llegada = context["direccion_llegada"]
        ciudad_llegada = context["ciudad_llegada"]
        dir_partida = context["direccion_partida"]
        ciudad_partida = context["ciudad_partida"]
        motivo = context["motivo_traslado"]
        placa = context["placa_vehiculo"]
        chofer_dni = context["conductor_documento"]

        ruc_emisor = os.environ.get("SUNAT_RUC", "20601234567").strip()
        razon_social_emisor = "EMPRESA DE CARBON SAC"

        default_chofer_dni = os.environ.get("SUNAT_DEFAULT_CHOFER_DNI", "00000000").strip()
        default_chofer_licencia = os.environ.get("SUNAT_DEFAULT_CHOFER_LICENCIA", f"Q{default_chofer_dni}").strip()
        default_chofer_nombre = os.environ.get("SUNAT_DEFAULT_CHOFER_NOMBRE", "CHOFER TELEGRAM").strip()

        if chofer_dni == default_chofer_dni:
            chofer_licencia = default_chofer_licencia
            chofer_nombre = default_chofer_nombre
        else:
            chofer_licencia = f"Q{chofer_dni}"
            chofer_nombre = "CHOFER TELEGRAM"

        motivo_map = {
            "venta": "01",
            "traslado": "04",
            "compra": "02",
            "devolucion": "06"
        }
        cod_motivo = motivo_map.get(motivo.lower(), "13")

        numero_correlativo = int(time.time()) % 1000000
        fecha_emision = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tipo_doc_dest = "6" if len(dest_doc) == 11 else "1"
        peso_bruto_total = sum(item["peso_total_kg"] for item in items)

        detalles_sunat = []
        for idx, item in enumerate(items):
            detalles_sunat.append({
                "codigo": f"P{idx+1:03d}",
                "descripcion": item["presentacion_nombre"],
                "whitespace_desc": item["presentacion_nombre"],
                "cantidad": float(item["whitespace_desc"] if "whitespace_desc" in item else item["cantidad"]),
                "unidadMedida": "NIU"
            })
            if "whitespace_desc" in detalles_sunat[-1]:
                del detalles_sunat[-1]["whitespace_desc"]
            detalles_sunat[-1]["cantidad"] = float(item["cantidad"])

        payload = {
            "serie": "T001",
            "numero": numero_correlativo,
            "fechaEmision": fecha_emision,
            "motivoTraslado": cod_motivo,
            "modalidadTransporte": "02",
            "unidadMedidaPeso": "KGM",
            "pesoBrutoTotal": float(peso_bruto_total),
            "remitente": {
                "numeroDocumento": ruc_emisor,
                "tipoDocumento": "6",
                "nombre": razon_social_emisor
            },
            "destinatario": {
                "numeroDocumento": dest_doc,
                "tipoDocumento": tipo_doc_dest,
                "nombre": dest_nombre
            },
            "puntoPartida": {
                "direccion": dir_partida,
                "ubigeo": context.get("ubigeo_partida", "030303")
            },
            "puntoLlegada": {
                "direccion": dir_llegada,
                "ubigeo": context.get("ubigeo_llegada", "030102")
            },
            "detalles": detalles_sunat,
            "chofer": {
                "tipoDocumento": "1",
                "numeroDocumento": chofer_dni,
                "licencia": chofer_licencia,
                "nombre": chofer_nombre
            },
            "vehiculo": {
                "placa": placa
            }
        }

        telegram_service.edit_message(chat_id, message_id, "📡 <i>Transmitiendo Guía de Remisión a la SUNAT...</i>")
        
        try:
            res = sunat_service.emitir_guia_remision(payload)
            ticket = res.get("numTicket")
            
            if ticket:
                telegram_service.edit_message(chat_id, message_id, f"✅ <b>Guía enviada. Ticket:</b> {ticket}\n⏳ <i>Consultando estado en SUNAT...</i>")
                
                time.sleep(2)
                
                status_res = sunat_service.consultar_estado_ticket(ticket)
                cod_estado = status_res.get("codRespuesta")
                mensaje = status_res.get("desRespuesta", "Procesado")
                
                if cod_estado == "99":
                    err_msg = status_res.get("error", {}).get("desError", "Error desconocido")
                    raise RuntimeError(f"SUNAT rechazó la guía: {err_msg}")
                elif cod_estado == "0" or "aceptada" in mensaje.lower() or "procesado" in mensaje.lower():
                    telegram_service.edit_message(
                        chat_id, 
                        message_id, 
                        f"✅ <b>Guía de Remisión Emitida y Aceptada por SUNAT</b>\n\n"
                        f"<b>Nro de Guía:</b> T001-{numero_correlativo:08d}\n"
                        f"<b>Ticket:</b> {ticket}\n"
                        f"<b>Estado:</b> {mensaje}\n\n"
                        f"📍 <b>Ruta:</b> {dir_partida} ➡️ {dir_llegada}\n"
                        f"📦 <b>Peso Total:</b> {peso_bruto_total} kg"
                    )
                else:
                    telegram_service.edit_message(
                        chat_id, 
                        message_id, 
                        f"⏳ <b>Guía Enviada (Procesamiento Asíncrono)</b>\n\n"
                        f"<b>Nro de Guía:</b> T001-{numero_correlativo:08d}\n"
                        f"<b>Ticket:</b> {ticket}\n"
                        f"<b>Estado:</b> {mensaje}\n\n"
                        f"La guía fue recibida por SUNAT y está en proceso de validación final."
                    )
            else:
                raise RuntimeError("SUNAT no devolvió ningún número de ticket.")

        except Exception as e:
            logger.error(f"Error al emitir guía en SUNAT: {e}", exc_info=True)
            telegram_service.edit_message(
                chat_id,
                message_id,
                f"❌ <b>Error SUNAT:</b> {str(e)}\n\n"
                f"No se pudo emitir la guía de remisión electrónica de forma definitiva."
            )
