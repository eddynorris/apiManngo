from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
import json
from models import Movimiento, Inventario, PresentacionProducto, Lote, Almacen, Receta, ComponenteReceta
from extensions import db
from common import handle_db_errors
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import joinedload, selectinload
import logging
import uuid
from services.produccion_service import ProduccionService, ProduccionValidationError

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
            return {"error": "Formato de datos inválido. Asegúrese de que todos los IDs y cantidades sean números válidos."}, 400

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

        # Obtener el lote de origen heredado (de la primera materia prima)
        inherited_lote_id = None
        for s in salidas:
            if s["tipo_consumo"] == "materia_prima":
                inherited_lote_id = s.get("lote_id")
                break

        ensamblaje_payload = {
            "almacen_id": almacen_id,
            "descripcion": f"Fabricación por receta de {cantidad_a_producir} unidades de {receta.presentacion.nombre}",
            "entradas": [{
                "presentacion_id": presentacion_final_id,
                "cantidad_unidades": str(cantidad_a_producir),
                "lote_destino_id": lote_destino_id if lote_destino_id is not None else inherited_lote_id
            }],
            "salidas": salidas
        }

        claims = get_jwt()
        usuario_id = claims.get('sub')
        try:
            res = ProduccionService.ejecutar_ensamblaje(usuario_id, ensamblaje_payload)
            db.session.commit()
            return res, 201
        except ProduccionValidationError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            error_id = uuid.uuid4().hex[:8]
            logger.error(f"Error en ProduccionResource [{error_id}]: {str(e)}", exc_info=True)
            return {"error": "Error interno al procesar la producción", "error_id": error_id}, 500

class ProduccionEnsamblajeResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        data = request.get_json()
        claims = get_jwt()
        usuario_id = claims.get('sub')

        try:
            res = ProduccionService.ejecutar_ensamblaje(usuario_id, data)
            db.session.commit()
            return res, 201
        except ProduccionValidationError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            error_id = uuid.uuid4().hex[:8]
            logger.error(f"Error en registro de ensamblaje [{error_id}]: {str(e)}", exc_info=True)
            return {"error": "Error interno al registrar el ensamblaje", "error_id": error_id}, 500