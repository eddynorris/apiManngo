# ARCHIVO: movimiento_resource.py
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException, NotFound

from common import handle_db_errors, MAX_ITEMS_PER_PAGE, parse_iso_datetime
from extensions import db
from models import Movimiento, Inventario, PresentacionProducto, Lote, Almacen
from models_stock_audit import MovimientoScope
from schemas import movimiento_schema, movimientos_schema
from services.authorization import autorizar_almacen, normalizar_ids
import logging

logger = logging.getLogger(__name__)

TIPOS_OPERACION_DERIVADOS = {
    'venta': 'Anule o edite la venta por su ruta de ventas; no borre una sola pierna del kardex.',
    'produccion': 'Registre el ajuste inverso mediante el flujo de producción.',
    'ensamblaje': 'Registre el ajuste inverso mediante el flujo de ensamblaje.',
    'transferencia': 'Ejecute la transferencia inversa entre los almacenes implicados.',
    'compra': 'Use el flujo de compra o un ajuste manual explícito, no el borrado de una pierna.',
    'merma': 'Registre el ajuste inverso por el flujo de mermas.',
}


def _id_opcional(valor, alias):
    if valor is None or valor == '':
        return None
    return normalizar_ids({alias: valor})[alias]


def _decimal_positivo_finito(valor):
    if valor is None or isinstance(valor, bool):
        raise ValueError('cantidad inválida')
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('cantidad inválida')
    if not numero.is_finite() or numero <= 0:
        raise ValueError('cantidad inválida')
    return numero


def _fecha_movimiento(valor):
    if valor is None or valor == '':
        return datetime.now(timezone.utc)
    if not isinstance(valor, str):
        raise ValueError('fecha inválida')
    return parse_iso_datetime(valor)


class MovimientoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, movimiento_id=None):
        """
        Obtiene movimientos de inventario
        - Con ID: Detalle completo con relaciones
        - Sin ID: Lista paginada con filtros (tipo, producto_id, fecha_inicio, fecha_fin, lote_id, presentacion_id)
        """
        if movimiento_id:
            
            movimiento = Movimiento.query.options(
                joinedload(Movimiento.presentacion),
                joinedload(Movimiento.lote),
                joinedload(Movimiento.usuario),
                joinedload(Movimiento.lote_origen)
            ).get_or_404(movimiento_id)
            return movimiento_schema.dump(movimiento), 200
        
        query = Movimiento.query.options(
            joinedload(Movimiento.presentacion),
            joinedload(Movimiento.lote),
            joinedload(Movimiento.usuario),
            joinedload(Movimiento.lote_origen)
        )
        
        if tipo := request.args.get('tipo'):
            query = query.filter_by(tipo=tipo)
        if lote_id := request.args.get('lote_id'):
            try:
                query = query.filter_by(lote_id=int(lote_id))
            except ValueError:
                return {"error": "ID de lote inválido"}, 400
        if presentacion_id := request.args.get('presentacion_id'):
            try:
                query = query.filter_by(presentacion_id=int(presentacion_id))
            except ValueError:
                return {"error": "ID de presentación inválido"}, 400
        # Filtro por rango de fechas
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        if fecha_inicio:
            try:
                fecha_inicio_dt = datetime.strptime(fecha_inicio.strip(), "%Y-%m-%d")
                query = query.filter(Movimiento.fecha >= fecha_inicio_dt)
            except (ValueError, TypeError):
                return {"error": "Formato de fecha_inicio inválido. Use YYYY-MM-DD."}, 400
        if fecha_fin:
            try:
                from datetime import timedelta
                fecha_fin_dt = datetime.strptime(fecha_fin.strip(), "%Y-%m-%d") + timedelta(days=1)
                query = query.filter(Movimiento.fecha < fecha_fin_dt)
            except (ValueError, TypeError):
                return {"error": "Formato de fecha_fin inválido. Use YYYY-MM-DD."}, 400

        # Ordenar por fecha descendente (historial lógico óptimo)
        query = query.order_by(Movimiento.fecha.desc(), Movimiento.id.desc())

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        movimientos = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": movimientos_schema.dump(movimientos.items),
            "pagination": {
                "total": movimientos.total,
                "page": movimientos.page,
                "per_page": movimientos.per_page,
                "pages": movimientos.pages
            }
        }, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra un movimiento manual contra un inventario real existente."""
        try:
            json_data = request.get_json()
            if not isinstance(json_data, dict):
                return {"error": "Se requiere un cuerpo JSON con el movimiento"}, 400

            datos = normalizar_ids(json_data)

            almacen_id = datos.get('almacen_id')
            presentacion_id = datos.get('presentacion_id')
            if almacen_id in (None, ''):
                return {"error": "Se requiere almacen_id para registrar un movimiento"}, 400
            if presentacion_id in (None, ''):
                return {"error": "Se requiere presentacion_id para registrar un movimiento"}, 400

            tipo = datos.get('tipo')
            if tipo not in ('entrada', 'salida'):
                return {"error": "El tipo debe ser 'entrada' o 'salida'"}, 400

            lote_id = _id_opcional(datos.get('lote_id'), 'lote_id')

            tipo_operacion = datos.get('tipo_operacion')
            if tipo_operacion in (None, ''):
                tipo_operacion = None
            elif tipo_operacion != 'ajuste':
                return {
                    "error": "Los movimientos derivados deben registrarse en su operación de origen",
                    "workflow": "No se permite crear manualmente una pierna de venta, producción, transferencia o compra.",
                }, 400

            try:
                cantidad = _decimal_positivo_finito(datos.get('cantidad'))
                fecha = _fecha_movimiento(datos.get('fecha'))
            except ValueError as exc:
                return {"error": str(exc)}, 400

            motivo = datos.get('motivo')
            if motivo is not None and not isinstance(motivo, str):
                return {"error": "motivo inválido"}, 400

            actor = autorizar_almacen(get_jwt().get('sub'), almacen_id)

            Almacen.query.get_or_404(almacen_id)
            PresentacionProducto.query.get_or_404(presentacion_id)
            if lote_id is not None:
                Lote.query.get_or_404(lote_id)

            inventario = Inventario.query.filter_by(
                presentacion_id=presentacion_id,
                almacen_id=almacen_id,
                lote_id=lote_id,
            ).with_for_update().one_or_none()

            if inventario is None:
                db.session.rollback()
                return {
                    "error": "No existe el inventario exacto para este movimiento; no se mutó stock",
                }, 409

            if tipo == 'salida' and inventario.cantidad < cantidad:
                db.session.rollback()
                return {
                    "error": "Stock insuficiente para este movimiento",
                    "disponible": str(inventario.cantidad),
                }, 400

            if tipo == 'entrada':
                maximo_inventario = Decimal('99999999.9999')
                if cantidad > maximo_inventario or (
                    inventario.cantidad + cantidad
                ) > maximo_inventario:
                    db.session.rollback()
                    return {
                        "error": "La cantidad excede el rango soportado por el inventario",
                    }, 400

            nuevo_movimiento = Movimiento(
                tipo=tipo,
                presentacion_id=presentacion_id,
                lote_id=lote_id,
                cantidad=cantidad,
                fecha=fecha,
                motivo=motivo,
                usuario_id=actor.id,
                tipo_operacion=tipo_operacion,
            )
            db.session.add(nuevo_movimiento)
            db.session.flush()

            db.session.add(MovimientoScope(
                movimiento_id=nuevo_movimiento.id,
                inventario_id=inventario.id,
                almacen_id=inventario.almacen_id,
            ))

            if tipo == 'entrada':
                inventario.cantidad += cantidad
            else:
                inventario.cantidad -= cantidad
            inventario.ultima_actualizacion = datetime.now(timezone.utc)

            db.session.commit()
            return movimiento_schema.dump(nuevo_movimiento), 201
        except HTTPException:
            db.session.rollback()
            raise

    @jwt_required()
    @handle_db_errors
    def delete(self, movimiento_id):
        """Revierte un movimiento manual solo si su procedencia exacta es verificable."""
        try:
            movimiento = db.session.get(Movimiento, movimiento_id)
            if movimiento is None:
                raise NotFound("Movimiento no encontrado")

            workflow = TIPOS_OPERACION_DERIVADOS.get(movimiento.tipo_operacion)
            if workflow is not None:
                db.session.rollback()
                return {
                    "error": "No se puede revertir un movimiento derivado borrando una sola pierna",
                    "workflow": workflow,
                }, 409

            scope_inicial = db.session.get(MovimientoScope, movimiento_id)
            if scope_inicial is None:
                db.session.rollback()
                return {
                    "error": "El movimiento no tiene procedencia de inventario verificable",
                    "codigo": "MOVIMIENTO_SIN_SCOPE",
                }, 409

            autorizar_almacen(get_jwt().get('sub'), scope_inicial.almacen_id)

            movimiento = (
                Movimiento.query.filter_by(id=movimiento_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if movimiento is None:
                raise NotFound("Movimiento no encontrado")

            scope = (
                MovimientoScope.query.filter_by(movimiento_id=movimiento_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if (
                scope is None
                or scope.almacen_id != scope_inicial.almacen_id
                or scope.inventario_id != scope_inicial.inventario_id
            ):
                db.session.rollback()
                return {
                    "error": "La procedencia del movimiento cambió durante la operación",
                    "codigo": "MOVIMIENTO_SCOPE_INCONSISTENTE",
                }, 409

            if movimiento.tipo_operacion in TIPOS_OPERACION_DERIVADOS:
                db.session.rollback()
                return {
                    "error": "No se puede revertir un movimiento derivado borrando una sola pierna",
                    "workflow": TIPOS_OPERACION_DERIVADOS[movimiento.tipo_operacion],
                }, 409

            inventario = Inventario.query.filter_by(
                id=scope.inventario_id
            ).with_for_update().one_or_none()
            if inventario is None:
                db.session.rollback()
                return {
                    "error": "El inventario original del movimiento ya no existe",
                    "codigo": "MOVIMIENTO_INVENTARIO_AUSENTE",
                }, 409

            if (
                inventario.almacen_id != scope.almacen_id
                or inventario.presentacion_id != movimiento.presentacion_id
                or inventario.lote_id != movimiento.lote_id
            ):
                db.session.rollback()
                return {
                    "error": "El inventario no coincide con la presentación, lote y almacén del movimiento",
                    "codigo": "MOVIMIENTO_SCOPE_INCONSISTENTE",
                }, 409

            try:
                cantidad = Decimal(str(movimiento.cantidad))
            except (InvalidOperation, TypeError, ValueError):
                cantidad = None
            if cantidad is None or not cantidad.is_finite() or cantidad <= 0:
                db.session.rollback()
                return {
                    "error": "La cantidad del movimiento no permite una reversión segura",
                    "codigo": "MOVIMIENTO_CANTIDAD_INVALIDA",
                }, 409

            if movimiento.tipo == 'entrada':
                if inventario.cantidad < cantidad:
                    db.session.rollback()
                    return {
                        "error": "La reversión dejaría stock negativo; no se truncó el inventario",
                        "codigo": "MOVIMIENTO_REVERSION_INSUFICIENTE",
                    }, 409
                inventario.cantidad -= cantidad
            elif movimiento.tipo == 'salida':
                inventario.cantidad += cantidad
            else:
                db.session.rollback()
                return {
                    "error": "Tipo de movimiento no reversible",
                    "codigo": "MOVIMIENTO_TIPO_INVALIDO",
                }, 409

            inventario.ultima_actualizacion = datetime.now(timezone.utc)
            db.session.delete(scope)
            db.session.delete(movimiento)
            db.session.commit()
            return "", 204
        except HTTPException:
            db.session.rollback()
            raise
