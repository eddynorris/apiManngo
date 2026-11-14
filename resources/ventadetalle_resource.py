from decimal import Decimal, InvalidOperation

from flask import request
from flask_jwt_extended import get_jwt, jwt_required
from flask_restful import Resource
from werkzeug.exceptions import BadRequest, HTTPException, NotFound

from common import handle_db_errors, obtener_payload_normalizado
from extensions import db
from models import PresentacionProducto, Venta, VentaDetalle
from schemas import venta_detalle_schema, ventas_detalle_schema
from services.authorization import autorizar_venta, normalizar_ids
from services.venta_service import StockInsuficienteError, VentaService


def _actor_id() -> int:
    return int(get_jwt().get('sub'))


def _decimal_positivo(valor, campo: str) -> Decimal:
    if isinstance(valor, bool) or valor is None:
        raise BadRequest(f"{campo} inválido")
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise BadRequest(f"{campo} inválido") from None
    if not numero.is_finite() or numero <= 0:
        raise BadRequest(f"{campo} debe ser un número finito mayor que cero")
    return numero


def _detalles_a_datos(detalles) -> list[dict]:
    return [
        {
            "presentacion_id": detalle.presentacion_id,
            "cantidad": detalle.cantidad,
            "precio_unitario": detalle.precio_unitario,
        }
        for detalle in detalles
    ]


def _detalle_id_solicitado(payload) -> int:
    valor = request.args.get('detalle_id')
    if valor is None and isinstance(payload, dict):
        valor = payload.get('detalle_id')
    if valor is None:
        raise BadRequest("detalle_id es requerido")
    return normalizar_ids({'detalle_id': valor})['detalle_id']


class VentaDetalleResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id):
        venta = Venta.query.get_or_404(venta_id)
        autorizar_venta(get_jwt().get('sub'), venta)

        detalles = (
            VentaDetalle.query
            .filter_by(venta_id=venta.id)
            .order_by(VentaDetalle.id.asc())
            .all()
        )
        return ventas_detalle_schema.dump(detalles), 200

    @jwt_required()
    @handle_db_errors
    def post(self, venta_id):
        venta = Venta.query.get_or_404(venta_id)
        autorizar_venta(get_jwt().get('sub'), venta)

        payload, _ = obtener_payload_normalizado()
        if not isinstance(payload, dict):
            return {"error": "Datos inválidos"}, 400

        if 'venta_id' in payload and str(payload['venta_id']) != str(venta.id):
            return {"error": "No se puede mover un detalle a otra venta"}, 400

        detalle_cargado = venta_detalle_schema.load(payload)
        presentacion_id = getattr(detalle_cargado, 'presentacion_id', None)
        if presentacion_id is None:
            raise BadRequest("presentacion_id es requerido")
        presentacion_id = normalizar_ids({'presentacion_id': presentacion_id})['presentacion_id']

        cantidad = _decimal_positivo(getattr(detalle_cargado, 'cantidad', None), "cantidad")
        presentacion = db.session.get(PresentacionProducto, presentacion_id)
        if presentacion is None:
            raise NotFound("Presentación no encontrada")

        precio_unitario = getattr(detalle_cargado, 'precio_unitario', None)
        if precio_unitario is None:
            precio_unitario = presentacion.precio_venta
        precio_unitario = _decimal_positivo(precio_unitario, "precio_unitario")

        actor_id = _actor_id()
        datos = {
            'cliente_id': venta.cliente_id,
            'almacen_id': venta.almacen_id,
            'estado': venta.estado,
            'detalles': _detalles_a_datos(venta.detalles) + [{
                'presentacion_id': presentacion.id,
                'cantidad': cantidad,
                'precio_unitario': precio_unitario,
            }],
        }

        try:
            VentaService.actualizar_venta(
                venta_id=venta.id,
                vendedor_id=actor_id,
                data=datos,
                actor_id=actor_id,
            )
            db.session.commit()
        except HTTPException:
            db.session.rollback()
            raise
        except StockInsuficienteError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except ValueError as e:
            db.session.rollback()
            return {"error": str(e)}, 400

        nuevo_detalle = (
            VentaDetalle.query
            .filter_by(venta_id=venta.id)
            .order_by(VentaDetalle.id.desc())
            .first()
        )
        if nuevo_detalle is None:
            raise NotFound("Detalle no encontrado")
        return venta_detalle_schema.dump(nuevo_detalle), 201

    @jwt_required()
    @handle_db_errors
    def delete(self, venta_id=None, detalle_id=None):
        if venta_id is None:
            payload, _ = obtener_payload_normalizado()
            detalle_id = _detalle_id_solicitado(payload) if detalle_id is None else int(detalle_id)
            detalle = db.session.get(VentaDetalle, detalle_id)
            if detalle is None:
                raise NotFound("Detalle no encontrado")
            venta = detalle.venta
        else:
            venta = Venta.query.get_or_404(venta_id)

        autorizar_venta(get_jwt().get('sub'), venta)

        if detalle_id is None:
            payload, _ = obtener_payload_normalizado()
            detalle_id = _detalle_id_solicitado(payload)
        else:
            detalle_id = int(detalle_id)

        detalle = db.session.get(VentaDetalle, detalle_id)
        if detalle is None:
            raise NotFound("Detalle no encontrado")
        if detalle.venta_id != venta.id:
            raise NotFound("Detalle no encontrado en la venta indicada")

        restantes = [d for d in venta.detalles if d.id != detalle.id]
        if not restantes:
            return {"error": "La venta debe conservar al menos un detalle"}, 400

        actor_id = _actor_id()
        try:
            VentaService.actualizar_venta(
                venta_id=venta.id,
                vendedor_id=actor_id,
                data={
                    'cliente_id': venta.cliente_id,
                    'almacen_id': venta.almacen_id,
                    'estado': venta.estado,
                    'detalles': _detalles_a_datos(restantes),
                },
                actor_id=actor_id,
            )
            db.session.commit()
        except HTTPException:
            db.session.rollback()
            raise
        except StockInsuficienteError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except ValueError as e:
            db.session.rollback()
            return {"error": str(e)}, 400

        return "", 204
