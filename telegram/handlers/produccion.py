import logging
from decimal import Decimal
from datetime import datetime, timezone

from extensions import db
from models import Almacen, PresentacionProducto, Inventario, Lote, Movimiento, Receta, ComponenteReceta
from services.telegram_service import telegram_service

logger = logging.getLogger(__name__)

class ProduccionHandler:
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
        telegram_service.edit_message(
            chat_id,
            message_id,
            f"✅ <b>¡Producción registrada y stock actualizado con éxito!</b>\n\n"
            f"🏪 <b>Almacén:</b> {almacen_nombre}\n\n"
            f"<b>Resumen de Fabricación:</b>\n{detalles_txt}"
        )
