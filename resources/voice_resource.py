from flask import request
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from services.gemini_service import gemini_service
from common import handle_db_errors
from models import Cliente, Inventario, PresentacionProducto, ComandoVozLog
from extensions import db
from sqlalchemy import func
import logging
import time

logger = logging.getLogger(__name__)

class VoiceCommandResource(Resource):   
    @jwt_required()
    @handle_db_errors
    def post(self):
        """
        Procesa un comando de voz, interpreta la intención con Gemini,
        resuelve entidades (Cliente, Productos) y verifica stock.
        Retorna un JSON enriquecido para confirmación del usuario.
        
        Rate limited: 20/minute (aplicado en init_resources)
        """
        start_time = time.time()  # Inicio de tracking de latencia
        
        data = request.get_json()
        command_text = data.get('text')
        
        if not command_text:
            return {"message": "Se requiere el campo 'text'"}, 400
            
        current_user_id = get_jwt_identity()
        claims = get_jwt()
        almacen_id = claims.get('almacen_id')
        
        # Obtener nombre del almacén para feedback
        almacen_nombre = "Desconocido"
        if almacen_id:
            from models import Almacen
            almacen_obj = Almacen.query.get(almacen_id)
            if almacen_obj:
                almacen_nombre = almacen_obj.nombre
        
        logger.info(f"Usuario {current_user_id} (Almacen {almacen_id} - {almacen_nombre}) envió comando: {command_text}")
        
        # 1. Procesar con Gemini (ya incluye sanitización interna)
        result = gemini_service.process_command(command_text)
        
        # Auditoría: Guardar log independientemente del resultado
        try:
            duration_ms = int((time.time() - start_time) * 1000)
            log_entry = ComandoVozLog(
                usuario_id=current_user_id,
                almacen_id=almacen_id,
                texto_original=command_text[:500],  # Truncar por seguridad
                interpretacion=result,
                accion_detectada=result.get('action'),
                exito=(result.get('action') not in ['error', 'security_block', 'none']),
                latencia_ms=duration_ms
            )
            db.session.add(log_entry)
            # Usar flush() en lugar de commit() para minimizar latencia
            # El commit se hará al final del request o con la venta
            db.session.flush()
        except Exception as e:
            logger.error(f"Error al registrar log de auditoría (ComandoVozLog): {e}")
            # No bloqueamos el flujo principal si falla el log, pero hacemos rollback de esa inserción fallida
            db.session.rollback()
        
        if result['action'] != 'interpretar_operacion':
            return {
                "status": "success",
                "processed_action": result['action'],
                "detected_args": result.get('args'),
                "gemini_message": result['message']
            }, 200

        args = result.get('args', {})
        enriched_data = {
            "cliente": None,
            "items": [],
            "pagos": args.get('pagos', []),
            "gasto_asociado": args.get('gasto_asociado'),
            "warnings": [],
            "contexto": {
                "almacen_id": almacen_id,
                "almacen_nombre": almacen_nombre
            }
        }

        # 2. Resolver Cliente
        cliente_nombre = args.get('cliente_nombre')
        if cliente_nombre:
            # Búsqueda insensible a mayúsculas/minúsculas
            cliente = Cliente.query.filter(Cliente.nombre.ilike(f"%{cliente_nombre}%")).first()
            
            if not cliente:
                # Intento de búsqueda difusa con PostgreSQL similarity
                # Requiere la extensión pg_trgm
                try:
                    cliente = Cliente.query.filter(
                        func.similarity(Cliente.nombre, cliente_nombre) > 0.3
                    ).order_by(
                        func.similarity(Cliente.nombre, cliente_nombre).desc()
                    ).first()
                    
                    if cliente:
                        enriched_data['warnings'].append(f"No se encontró '{cliente_nombre}', se asumió '{cliente.nombre}'.")
                except Exception as e:
                    # Si pg_trgm no está disponible, log y continuar
                    logger.warning(f"PostgreSQL similarity search no disponible: {e}")

            if cliente:
                enriched_data['cliente'] = {
                    "id": cliente.id,
                    "nombre": cliente.nombre,
                    "match_type": "exact" if cliente.nombre.lower() == cliente_nombre.lower() else "fuzzy"
                }
            else:
                enriched_data['warnings'].append(f"Cliente '{cliente_nombre}' no encontrado. Se requiere seleccionar uno manualmente.")
                enriched_data['cliente'] = {"nombre_buscado": cliente_nombre}

        # 3. Resolver Productos y Verificar Stock con PostgreSQL Similarity Search
        items_raw = args.get('items', [])
        
        for item in items_raw:
            prod_nombre = item.get('producto_nombre')
            cantidad = item.get('cantidad', 0)
            precio_explicito = item.get('precio')

            # Estrategia de Búsqueda de Producto con PostgreSQL
            presentacion = None
            match_method = "none"

            # Sanitizar nombre del producto para SQL LIKE (seguridad)
            prod_nombre_safe = prod_nombre.replace('%', '').replace('_', '')

            # A. Intento Exacto/ILike (Solo procesados)
            presentacion = PresentacionProducto.query.filter(
                PresentacionProducto.nombre.ilike(f"%{prod_nombre_safe}%"),
                PresentacionProducto.tipo == 'procesado'
            ).first()
            if presentacion:
                match_method = "exact"

            # B. Búsqueda Optimizada con PostgreSQL Trigram (Si falla el exacto)
            if not presentacion:
                try:
                    # Esta consulta usa el índice GIN creado en el Paso 1
                    # Busca similitud > 0.3 y ordena por la mayor similitud
                    presentacion = PresentacionProducto.query.filter(
                        func.similarity(PresentacionProducto.nombre, prod_nombre_safe) > 0.3,
                        PresentacionProducto.tipo == 'procesado'
                    ).order_by(
                        func.similarity(PresentacionProducto.nombre, prod_nombre_safe).desc()
                    ).first()
                    
                    if presentacion:
                        match_method = "similarity"
                except Exception as e:
                    # Si pg_trgm no está disponible, log y continuar con búsqueda por tokens
                    logger.warning(f"PostgreSQL similarity search no disponible para productos: {e}")
                    
                    # C. Fallback: Búsqueda por Tokens (si similarity falla)
                    all_products = db.session.query(
                        PresentacionProducto.id, 
                        PresentacionProducto.nombre, 
                        PresentacionProducto.precio_venta
                    ).filter(PresentacionProducto.tipo == 'procesado').all()
                    
                    # Tokenizar búsqueda (ej: "saco de 20 kg" -> ["saco", "20", "kg"])
                    search_tokens = set(prod_nombre.lower().replace('kg', ' kg').split())
                    best_score = 0
                    best_candidate = None
                    
                    for prod in all_products:
                        prod_tokens = set(prod.nombre.lower().replace('kg', ' kg').split())
                        # Calcular intersección
                        common_tokens = search_tokens.intersection(prod_tokens)
                        score = len(common_tokens)
                        
                        # Boost masivo si hay números coincidentes (ej: "20") - CRÍTICO para este negocio
                        for token in common_tokens:
                            if token.isdigit():
                                score += 3  # Peso x3 para números (kilos)
                        
                        if score > best_score:
                            best_score = score
                            best_candidate = prod
                    
                    # Umbral mínimo de coincidencia
                    if best_candidate and best_score >= 1:
                        presentacion = PresentacionProducto.query.get(best_candidate.id)
                        match_method = "token_match"

            item_enriched = {
                "producto_nombre_buscado": prod_nombre,
                "cantidad": cantidad,
                "precio_unitario": precio_explicito,
                "producto_id": None,
                "stock_actual": 0,
                "lote_id": None,
                "subtotal": 0
            }

            if presentacion:
                item_enriched['producto_id'] = presentacion.id
                item_enriched['producto_nombre'] = presentacion.nombre
                
                if not precio_explicito:
                    item_enriched['precio_unitario'] = float(presentacion.precio_venta)
                
                # Calcular subtotal
                item_enriched['subtotal'] = item_enriched['cantidad'] * item_enriched['precio_unitario']
                
                if match_method != "exact":
                     enriched_data['warnings'].append(f"No se encontró '{prod_nombre}', se asumió '{presentacion.nombre}' ({match_method}).")

                # Verificar Stock en el almacén del usuario
                if almacen_id:
                    inventario = Inventario.query.filter_by(
                        almacen_id=almacen_id,
                        presentacion_id=presentacion.id
                    ).first()
                    
                    if inventario:
                        item_enriched['stock_actual'] = float(inventario.cantidad)
                        item_enriched['lote_id'] = inventario.lote_id
                        
                        if inventario.cantidad < cantidad:
                            enriched_data['warnings'].append(f"Stock insuficiente para '{presentacion.nombre}'. Solicitado: {cantidad}, Disponible: {inventario.cantidad}")
                    else:
                        enriched_data['warnings'].append(f"No hay inventario de '{presentacion.nombre}' en tu almacén.")
                else:
                     enriched_data['warnings'].append("Usuario no tiene almacén asignado para verificar stock.")

            else:
                enriched_data['warnings'].append(f"Producto '{prod_nombre}' no encontrado en el catálogo.")
            
            enriched_data['items'].append(item_enriched)

        # 4. Lógica de Pago Automático (Pago Completo o Relativo)
        condicion_pago = args.get('condicion_pago', 'parcial')
        porcentaje_abono = args.get('porcentaje_abono')
        total_estimado = sum(item['subtotal'] for item in enriched_data['items'])
        
        if condicion_pago == 'completo':
            # Si hay un pago parcial (ej: "pago completo con yape"), usar ese método
            metodo = "efectivo"  # Default
            if enriched_data['pagos']:
                metodo = enriched_data['pagos'][0].get('metodo_pago', 'efectivo')
            
            # Sobreescribir pagos con el total
            enriched_data['pagos'] = [{
                "monto": total_estimado,
                "metodo_pago": metodo,
                "es_deposito": False  # Default, salvo que se diga lo contrario
            }]
            enriched_data['warnings'].append(f"Se generó un pago automático por el total: S/ {total_estimado}")
            
        elif porcentaje_abono and isinstance(porcentaje_abono, (int, float)) and porcentaje_abono > 0:
            # Lógica para pago relativo (ej: "mitad" -> 50%, "30%")
            monto_abono = (total_estimado * porcentaje_abono) / 100
            monto_abono = round(monto_abono, 2)
            
            metodo = "efectivo"
            if enriched_data['pagos']:
                 metodo = enriched_data['pagos'][0].get('metodo_pago', 'efectivo')

            enriched_data['pagos'] = [{
                "monto": monto_abono,
                "metodo_pago": metodo,
                "es_deposito": False
            }]
            enriched_data['warnings'].append(f"Se calculó un abono del {porcentaje_abono}%: S/ {monto_abono}")

        elif condicion_pago == 'credito':
             enriched_data['pagos'] = []  # Vaciar pagos si es crédito
             enriched_data['warnings'].append("Venta al crédito (sin pagos iniciales).")

        # 6. Agregar lista completa de clientes para selección manual
        clientes_disponibles = Cliente.query.order_by(Cliente.nombre).all()
        enriched_data['clientes_disponibles'] = [{
            "id": c.id,
            "nombre": c.nombre,
            "telefono": c.telefono,
            "ciudad": c.ciudad
        } for c in clientes_disponibles]

        from common import make_json_serializable
        response_data = {
            "status": "success",
            "processed_action": "confirmar_operacion",
            "data": enriched_data,
            "original_text": command_text,
            "performance": {
                "latency_ms": duration_ms
            }
        }
        
        return make_json_serializable(response_data), 200
