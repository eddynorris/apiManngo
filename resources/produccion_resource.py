from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request, current_app
import json
from models import Movimiento, Inventario, PresentacionProducto, Lote, Almacen, Receta, ComponenteReceta
from extensions import db
from common import handle_db_errors
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import joinedload, selectinload
import logging
import uuid

logger = logging.getLogger(__name__)



class ProduccionResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        data = request.get_json()
        required_fields = ["almacen_id", "presentacion_id", "cantidad_a_producir", "lotes_seleccionados"]
        for field in required_fields:
            if field not in data:
                return {"error": f"Campo requerido: {field}"}, 400

        try:
            almacen_id = int(str(data['almacen_id']))
            presentacion_final_id = int(str(data['presentacion_id']))
            cantidad_a_producir = Decimal(str(data['cantidad_a_producir']))
            lote_destino_id = data.get('lote_destino_id')
            if lote_destino_id is not None:
                lote_destino_id = int(str(lote_destino_id))
            if cantidad_a_producir <= 0:
                return {"error": "La cantidad a producir debe ser mayor a cero."}, 400
            lotes_seleccionados_clean = []
            for item in data['lotes_seleccionados']:
                lotes_seleccionados_clean.append({
                    'componente_presentacion_id': int(str(item['componente_presentacion_id'])),
                    'lote_id': int(str(item['lote_id']))
                })
        except (ValueError, TypeError, InvalidOperation) as e:
            logger.error(f"Error de formato en ProduccionResource: {e}", exc_info=True)
            return {"error": "Formato de datos inválido. Asegúrese de que todos los IDs y cantidades sean números válidos.", "detalle": str(e)}, 400

        receta = Receta.query.options(
            selectinload(Receta.componentes).joinedload(ComponenteReceta.componente_presentacion)
        ).filter_by(presentacion_id=presentacion_final_id).first()

        if not receta:
            return {"error": f"No se encontró una receta para la presentación ID {presentacion_final_id}"}, 404

        salidas = []
        lotes_seleccionados_map = {item['componente_presentacion_id']: item['lote_id'] for item in lotes_seleccionados_clean}

        for componente in receta.componentes:
            cantidad_total_necesaria = componente.cantidad_necesaria * cantidad_a_producir
            if componente.tipo_consumo == 'materia_prima':
                lote_id = lotes_seleccionados_map.get(componente.componente_presentacion_id)
                if not lote_id:
                    return {"error": f"No se especificó un lote para la materia prima '{componente.componente_presentacion.nombre}' (ID: {componente.componente_presentacion_id})"}, 400
                salidas.append({
                    "tipo_consumo": "materia_prima",
                    "lote_id": lote_id,
                    "cantidad_kg": str(cantidad_total_necesaria)
                })
            elif componente.tipo_consumo == 'insumo':
                salidas.append({
                    "tipo_consumo": "insumo",
                    "presentacion_id": componente.componente_presentacion_id,
                    "cantidad_unidades": str(cantidad_total_necesaria)
                })

        ensamblaje_payload = {
            "almacen_id": almacen_id,
            "descripcion": f"Fabricación por receta de {cantidad_a_producir} unidades de {receta.presentacion.nombre}",
            "entradas": [{
                "presentacion_id": presentacion_final_id,
                "cantidad_unidades": str(cantidad_a_producir),
                "lote_destino_id": lote_destino_id
            }],
            "salidas": salidas
        }

        ensamblaje_resource = ProduccionEnsamblajeResource()
        auth_header = request.headers.get('Authorization')
        headers = {'Content-Type': 'application/json'}
        if auth_header:
            headers['Authorization'] = auth_header

        with current_app.test_request_context('/api/produccion/ensamblaje', method='POST', headers=headers, data=json.dumps(ensamblaje_payload)):
            return ensamblaje_resource.post()

class ProduccionEnsamblajeResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        data = request.get_json()
        almacen_id = data["almacen_id"]
        entradas = data["entradas"]
        salidas = data["salidas"]
        claims = get_jwt()
        usuario_id = claims.get('sub')

        try:
            # --- Fase de Verificación de Stock y Lote de Destino ---
            for item in [s for s in salidas if s['tipo_consumo'] == 'insumo']:
                cantidad_req = Decimal(item["cantidad_unidades"])
                inv = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=int(item["presentacion_id"]), lote_id=None).first()
                if not inv or inv.cantidad < cantidad_req:
                    return {"error": f"Stock de insumo insuficiente para presentación ID {item['presentacion_id']}. Requerido: {cantidad_req}, Disponible: {inv.cantidad if inv else 0}"}, 400

            for item in [s for s in salidas if s['tipo_consumo'] == 'materia_prima']:
                lote_id = int(item["lote_id"])
                cantidad_req_kg = Decimal(item["cantidad_kg"])
                lote = Lote.query.get(lote_id)
                
                if not lote:
                    return {"error": f"No se encontró el lote ID {lote_id}."}, 400
                
                if lote.cantidad_disponible_kg < cantidad_req_kg:
                    return {"error": f"Stock en KG insuficiente en Lote ID {lote_id}. Requerido: {cantidad_req_kg}, Disponible: {lote.cantidad_disponible_kg}"}, 400

            for item in entradas:
                if lote_destino_id := item.get('lote_destino_id'):
                    lote_destino = Lote.query.get(lote_destino_id)
                    if not lote_destino:
                        return {"error": f"El lote de destino con ID {lote_destino_id} no existe."}, 400

            # --- Fase de Ejecución (Transacción Atómica) ---
            id_ensamblaje = str(uuid.uuid4())
            fecha_operacion = datetime.now(timezone.utc)
            motivo_base = f"Ensamblaje {id_ensamblaje}: {data['descripcion']}"

            for item in salidas:
                if item["tipo_consumo"] == "materia_prima":
                    lote_id, cantidad_kg = int(item["lote_id"]), Decimal(item["cantidad_kg"])
                    lote = Lote.query.get(lote_id)
                    # Para materia prima, solo reducimos del lote directamente
                    lote.cantidad_disponible_kg -= cantidad_kg
                    # Registramos el movimiento sin presentacion_id específica ya que es materia prima
                    db.session.add(Movimiento(tipo='salida', presentacion_id=None, lote_id=lote_id, cantidad=cantidad_kg, fecha=fecha_operacion, motivo=motivo_base, usuario_id=usuario_id, tipo_operacion='ensamblaje'))
                elif item["tipo_consumo"] == "insumo":
                    presentacion_id, cantidad_unidades = int(item["presentacion_id"]), Decimal(item["cantidad_unidades"])
                    inv = Inventario.query.filter_by(almacen_id=almacen_id, presentacion_id=presentacion_id, lote_id=None).first()
                    inv.cantidad -= cantidad_unidades
                    db.session.add(Movimiento(tipo='salida', presentacion_id=presentacion_id, lote_id=None, cantidad=cantidad_unidades, fecha=fecha_operacion, motivo=motivo_base, usuario_id=usuario_id, tipo_operacion='ensamblaje'))

            for item in entradas:
                presentacion_id, cantidad_unidades = int(item["presentacion_id"]), Decimal(item["cantidad_unidades"])
                presentacion_final = PresentacionProducto.query.get(presentacion_id)
                cantidad_kg_producida = cantidad_unidades * (presentacion_final.capacidad_kg or Decimal('0.0'))
                lote_destino_id = item.get('lote_destino_id')

                lote_para_movimiento_id = None
                
                # Todas las presentaciones van al inventario (productos finales)
                if lote_destino_id:
                    lote_destino = Lote.query.get(lote_destino_id)
                    if not lote_destino:
                        return {"error": f"El lote de destino con ID {lote_destino_id} no existe."}, 400
                    lote_destino.cantidad_disponible_kg += cantidad_kg_producida
                    lote_para_movimiento_id = lote_destino.id
                else:
                    # Para productos sin lote específico, usar None en el lote
                    lote_para_movimiento_id = None
                
                # Buscar inventario existente por presentacion_id y almacen_id
                inv_destino = Inventario.query.filter_by(
                    presentacion_id=presentacion_id, 
                    almacen_id=almacen_id
                ).first()
                
                if inv_destino:
                    # Actualizar inventario existente
                    inv_destino.cantidad += cantidad_unidades
                    inv_destino.ultima_actualizacion = fecha_operacion
                    # Si hay un lote específico, actualizar el lote_id
                    if lote_destino_id:
                        inv_destino.lote_id = lote_destino_id
                else:
                    # Crear nuevo registro de inventario
                    inv_destino = Inventario(
                        presentacion_id=presentacion_id, 
                        almacen_id=almacen_id, 
                        lote_id=lote_destino_id, 
                        cantidad=cantidad_unidades
                    )
                    db.session.add(inv_destino)
                
                db.session.add(Movimiento(
                    tipo='entrada', 
                    presentacion_id=presentacion_id, 
                    lote_id=lote_para_movimiento_id, 
                    cantidad=cantidad_unidades, 
                    fecha=fecha_operacion, 
                    motivo=motivo_base, 
                    usuario_id=usuario_id, 
                    tipo_operacion='ensamblaje'
                ))

            db.session.commit()
            return {"mensaje": "Operación de ensamblaje registrada exitosamente", "id_ensamblaje": id_ensamblaje}, 201

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en registro de ensamblaje: {str(e)}", exc_info=True)
            return {"error": "Error interno al registrar el ensamblaje", "detalle": str(e)}, 500