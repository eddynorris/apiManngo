# ARCHIVO: resources/pago_resource.py
import io
import json
import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal

import pandas as pd
from flask import request, send_file
from flask_jwt_extended import get_jwt, jwt_required
from flask_restful import Resource
from marshmallow import ValidationError
from sqlalchemy import asc, case, desc, func
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import BadRequest, Forbidden, HTTPException, NotFound

from common import MAX_ITEMS_PER_PAGE, handle_db_errors, parse_iso_datetime
from extensions import db
from models import Gasto, Pago, Venta
from schemas import gastos_schema, pago_schema, pagos_schema
from services.authorization import (
    autorizar_pago,
    autorizar_venta_para_pago,
    es_admin,
    normalizar_ids,
    obtener_actor,
)
from services.money import monto_decimal_positivo
from services.pago_service import PagoConflictError, PagoService, PagoValidationError
from services.storage_cleanup_service import (
    commit_with_storage_finalization,
    rollback_and_compensate,
    track_uploaded_key,
)
from utils.file_handlers import get_presigned_url, save_file

# Configuración de Logging
logger = logging.getLogger(__name__)

# Campos que solo puede fijar el servidor. Se ignoran aunque el cliente los
# envíe; nunca autorizan ni modifican estado por sí mismos.
SERVER_ONLY_PAGO_FIELDS = frozenset({
    'usuario_id', 'depositado', 'monto_depositado', 'fecha_deposito',
    'url_comprobante', 'monto_en_gerencia',
})


# --- FUNCIONES AUXILIARES ---
def _parse_request_data():
    """Unifica la obtención de datos de JSON y multipart/form-data."""
    if request.content_type and 'multipart/form-data' in request.content_type:
        data = request.form.to_dict()
        file = request.files.get('comprobante')
        eliminar_comprobante = data.get('eliminar_comprobante', 'false').lower() == 'true'
        return data, file, eliminar_comprobante
    elif request.content_type and 'application/json' in request.content_type:
        return request.get_json(silent=True), None, False
    raise BadRequest("Tipo de contenido no soportado.")


def _get_presigned_url_for_item(item_dump, s3_key):
    """Genera y asigna una URL pre-firmada a un objeto serializado."""
    if s3_key:
        item_dump['url_comprobante'] = get_presigned_url(s3_key)
    return item_dump


def _quitar_campos_de_servidor(datos, *, permitir_venta=False):
    """Elimina campos que el cliente no puede asignar directamente."""
    if not isinstance(datos, dict):
        return {}
    limpio = dict(datos)
    for campo in SERVER_ONLY_PAGO_FIELDS:
        limpio.pop(campo, None)
    if not permitir_venta:
        limpio.pop('venta_id', None)
    return limpio


# --- RESOURCES DE LA API ---
class PagoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, pago_id=None):
        """Obtiene un pago o una lista paginada de pagos."""
        claims = get_jwt()
        actor = obtener_actor(claims.get('sub'))

        if pago_id:
            pago = PagoService.find_pago_by_id(pago_id)
            autorizar_pago(actor.id, pago)
            pago_dump = pago_schema.dump(pago)
            return _get_presigned_url_for_item(pago_dump, pago.url_comprobante), 200

        query = PagoService.get_pagos_query(request.args, actor.id, actor.rol)

        sort_by = request.args.get('sort_by', 'fecha')
        sort_order = request.args.get('sort_order', 'desc').lower()
        sort_column = getattr(Pago, sort_by, Pago.fecha)
        order_func = desc if sort_order == 'desc' else asc
        query = query.order_by(order_func(sort_column))

        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pagos_paginados = query.paginate(page=page, per_page=per_page, error_out=False)
        pagos_dump = pagos_schema.dump(pagos_paginados.items)

        for pago_obj, dump_item in zip(pagos_paginados.items, pagos_dump, strict=False):
             _get_presigned_url_for_item(dump_item, pago_obj.url_comprobante)

        return {
            "data": pagos_dump,
            "pagination": {
                "total": pagos_paginados.total,
                "page": pagos_paginados.page,
                "per_page": pagos_paginados.per_page,
                "pages": pagos_paginados.pages,
            }
        }, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra un nuevo pago."""
        try:
            claims = get_jwt()
            actor_id = claims.get('sub')

            raw_data, file, _ = _parse_request_data()
            if not isinstance(raw_data, dict):
                raise BadRequest("Datos inválidos")

            if raw_data.get('metodo_pago'):
                raw_data['metodo_pago'] = str(raw_data['metodo_pago']).lower()

            raw_data = normalizar_ids(raw_data)

            venta_id = raw_data.get('venta_id')
            if not venta_id:
                raise PagoValidationError("El campo 'venta_id' es requerido.")
            venta = db.session.get(Venta, venta_id)
            if not venta:
                raise NotFound("Venta no encontrada.")

            # Autorizar antes del schema.load y de cualquier subida de archivo.
            autorizar_venta_para_pago(actor_id, venta)

            raw_data = _quitar_campos_de_servidor(raw_data, permitir_venta=True)
            nuevo_pago_instancia = pago_schema.load(raw_data)
            nuevo_pago = PagoService.create_pago(
                nuevo_pago_instancia, file, actor_id, actor_id=actor_id
            )

            commit_with_storage_finalization()

            pago_dump = pago_schema.dump(nuevo_pago)
            return _get_presigned_url_for_item(pago_dump, nuevo_pago.url_comprobante), 201
        except HTTPException:
            rollback_and_compensate()
            raise
        except ValidationError:
            rollback_and_compensate()
            raise
        except PagoConflictError as e:
            rollback_and_compensate()
            return {"error": str(e)}, 409
        except PagoValidationError as e:
            rollback_and_compensate()
            return {"error": str(e)}, 400
        except Exception as e:
            rollback_and_compensate()
            logger.error(f"Error al crear pago: {e}")
            return {"error": "Error interno al procesar el pago."}, 500

    @jwt_required()
    @handle_db_errors
    def put(self, pago_id):
        """Actualiza un pago existente."""
        try:
            claims = get_jwt()
            actor_id = claims.get('sub')

            raw_data, file, eliminar_comprobante = _parse_request_data()
            if not isinstance(raw_data, dict):
                raise BadRequest("Datos inválidos")

            raw_data = normalizar_ids(raw_data)
            if raw_data.get('metodo_pago'):
                raw_data['metodo_pago'] = str(raw_data['metodo_pago']).lower()

            pago_existente = PagoService.find_pago_by_id(pago_id)

            # Autorizar el pago real antes de cargar/mutar el ORM o subir archivos.
            autorizar_pago(actor_id, pago_existente)

            if 'venta_id' in raw_data:
                if str(raw_data['venta_id']) != str(pago_existente.venta_id):
                    raise PagoValidationError(
                        "No está permitido cambiar la venta de un pago existente."
                    )
                raw_data.pop('venta_id', None)

            raw_data = _quitar_campos_de_servidor(raw_data)

            validado = pago_schema.load(raw_data, partial=True)
            datos = {
                campo: getattr(validado, campo)
                for campo in ('monto', 'metodo_pago', 'referencia', 'fecha')
                if campo in raw_data
            }

            pago_actualizado = PagoService.update_pago(
                pago_existente, file, eliminar_comprobante,
                datos=datos, actor_id=actor_id,
            )

            commit_with_storage_finalization()

            pago_dump = pago_schema.dump(pago_actualizado)
            return _get_presigned_url_for_item(pago_dump, pago_actualizado.url_comprobante), 200
        except HTTPException:
            rollback_and_compensate()
            raise
        except ValidationError:
            rollback_and_compensate()
            raise
        except PagoConflictError as e:
            rollback_and_compensate()
            return {"error": str(e)}, 409
        except PagoValidationError as e:
            rollback_and_compensate()
            return {"error": str(e)}, 400
        except Exception as e:
            rollback_and_compensate()
            logger.error(f"Error al actualizar pago {pago_id}: {e}")
            return {"error": "Error interno al actualizar el pago."}, 500

    @jwt_required()
    @handle_db_errors
    def delete(self, pago_id):
        """Elimina un pago."""
        try:
            claims = get_jwt()
            PagoService.delete_pago(pago_id, actor_id=claims.get('sub'))
            commit_with_storage_finalization()
            return {"message": "Pago eliminado exitosamente"}, 200
        except HTTPException:
            rollback_and_compensate()
            raise
        except PagoValidationError as e:
            rollback_and_compensate()
            return {"error": str(e)}, 400
        except Exception as e:
            rollback_and_compensate()
            logger.error(f"Error al eliminar pago {pago_id}: {e}")
            return {"error": "Error interno al eliminar el pago."}, 500


class PagosPorVentaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id):
        """Obtiene todos los pagos de una venta específica."""
        claims = get_jwt()
        actor = obtener_actor(claims.get('sub'))
        venta = Venta.query.get_or_404(venta_id)
        autorizar_venta_para_pago(actor.id, venta)

        query = Pago.query.filter_by(venta_id=venta_id)
        if not es_admin(actor):
            query = query.filter(Pago.usuario_id == actor.id)
        pagos = query.order_by(Pago.fecha.asc()).all()
        pagos_dump = pagos_schema.dump(pagos)
        for i, pago in enumerate(pagos):
            _get_presigned_url_for_item(pagos_dump[i], pago.url_comprobante)
        return pagos_dump, 200


class PagoBatchResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        try:
            if request.is_json:
                json_data = request.get_json(silent=True) or {}
                if not isinstance(json_data, dict):
                    return {"error": "El cuerpo JSON debe ser un objeto"}, 400
                pagos_list = json_data.get('pagos')
                pagos_json_str = json.dumps(pagos_list) if pagos_list is not None else None
                fecha_str = json_data.get('fecha')
                metodo_pago = json_data.get('metodo_pago')
                referencia = json_data.get('referencia')
                file = None
            else:
                if 'multipart/form-data' not in request.content_type and 'application/x-www-form-urlencoded' not in request.content_type:
                    return {"error": "Content-Type no soportado. Se requiere application/json, multipart/form-data o application/x-www-form-urlencoded"}, 415
                pagos_json_str = request.form.get('pagos_json_data')
                fecha_str = request.form.get('fecha')
                metodo_pago = request.form.get('metodo_pago')
                referencia = request.form.get('referencia')
                file = request.files.get('comprobante')

            if metodo_pago:
                metodo_pago = str(metodo_pago).lower()

            if not all([pagos_json_str, fecha_str, metodo_pago]):
                return {"error": "Faltan campos requeridos (pagos/pagos_json_data, fecha, metodo_pago)"}, 400

            claims = get_jwt()
            actor_id = claims.get('sub')
            pagos_creados = PagoService.create_batch_pagos(
                pagos_json_str, file, fecha_str, metodo_pago, referencia,
                usuario_id=actor_id,
                actor_id=actor_id,
            )
            commit_with_storage_finalization()
            created_pagos_dump = pagos_schema.dump(pagos_creados)
            for i, pago in enumerate(pagos_creados):
                _get_presigned_url_for_item(created_pagos_dump[i], pago.url_comprobante)
            return {"message": "Pagos en lote registrados exitosamente.", "pagos_creados": created_pagos_dump}, 201
        except (PagoValidationError, BadRequest) as e:
            rollback_and_compensate()
            return {"error": str(e)}, 400
        except NotFound as e:
            rollback_and_compensate()
            return {"error": str(e)}, 404
        except Forbidden as e:
            rollback_and_compensate()
            return {"error": str(e)}, 403
        except HTTPException:
            rollback_and_compensate()
            raise
        except Exception as e:
            rollback_and_compensate()
            logger.error(f"Error crítico en batch de pagos: {str(e)}")
            return {"error": "Ocurrió un error interno, la operación fue revertida."}, 500


class DepositoBancarioResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra un depósito bancario para uno o múltiples pagos."""
        try:
            claims = get_jwt()
            actor_id = claims.get('sub')

            comprobante_file = None
            if request.content_type and 'multipart/form-data' in request.content_type:
                depositos_json_str = request.form.get('depositos')
                fecha_deposito_str = request.form.get('fecha_deposito')
                comprobante_file = request.files.get('comprobante_deposito')

                if not depositos_json_str:
                    return {"error": "Campo 'depositos' (JSON string) es requerido"}, 400
                try:
                    depositos = json.loads(depositos_json_str)
                except json.JSONDecodeError:
                    return {"error": "Formato JSON inválido en 'depositos'"}, 400
            else:
                data = request.get_json(silent=True)
                if not isinstance(data, dict) or not data:
                    return {"error": "No se proporcionaron datos"}, 400
                depositos = data.get('depositos', [])
                fecha_deposito_str = data.get('fecha_deposito')

            if not isinstance(depositos, list) or not depositos or not fecha_deposito_str:
                return {"error": "Campos requeridos: 'depositos' (lista) y 'fecha_deposito'"}, 400

            try:
                fecha_deposito = parse_iso_datetime(fecha_deposito_str, add_timezone=True)
            except (ValueError, TypeError):
                return {"error": "Formato de fecha inválido"}, 400

            # Validar estructura, IDs únicos y montos antes de tocar filas o
            # subir el comprobante.
            entradas = []
            ids_vistos = set()
            for deposito_data in depositos:
                if not isinstance(deposito_data, dict):
                    return {"error": "Cada depósito debe ser un objeto"}, 400
                try:
                    pago_id = normalizar_ids({'pago_id': deposito_data.get('pago_id')})['pago_id']
                except (BadRequest, KeyError):
                    return {"error": "pago_id inválido"}, 400
                if pago_id is None:
                    return {"error": "pago_id inválido"}, 400
                if pago_id in ids_vistos:
                    return {"error": f"pago_id duplicado: {pago_id}"}, 400
                try:
                    monto = monto_decimal_positivo(
                        deposito_data.get('monto_depositado'), 'monto_depositado'
                    )
                except ValueError as e:
                    return {"error": str(e)}, 400
                ids_vistos.add(pago_id)
                entradas.append((pago_id, monto))

            # Autorizar todos los pagos antes de subir archivos.
            pagos_referencia = Pago.query.filter(Pago.id.in_(sorted(ids_vistos))).all()
            if len(pagos_referencia) != len(ids_vistos):
                return {"error": "Algunos pagos no fueron encontrados"}, 404
            for pago in pagos_referencia:
                autorizar_pago(actor_id, pago)

            s3_key_comprobante = None
            if comprobante_file and comprobante_file.filename:
                s3_key_comprobante = save_file(comprobante_file, 'comprobantes_depositos')
                if not s3_key_comprobante:
                    return {"error": "Error interno al guardar el comprobante"}, 500
                track_uploaded_key(s3_key_comprobante)

            # Bloquear ventas y pagos en orden determinista y revalidar montos.
            venta_ids = sorted({pago.venta_id for pago in pagos_referencia})
            PagoService._lock_ventas(venta_ids)
            pagos_bloqueados = PagoService._lock_pagos(sorted(ids_vistos))
            if len(pagos_bloqueados) != len(ids_vistos):
                rollback_and_compensate()
                return {"error": "Algunos pagos no fueron encontrados"}, 404

            for pago in pagos_bloqueados.values():
                autorizar_pago(actor_id, pago)

            for pago_id, monto in entradas:
                pago = pagos_bloqueados[pago_id]
                monto_disponible = Decimal(str(pago.monto)) - Decimal(
                    str(pago.monto_depositado or 0)
                )
                if monto > monto_disponible:
                    rollback_and_compensate()
                    return {
                        "error": f"Monto para pago {pago.id} excede el disponible {monto_disponible}"
                    }, 400

            pagos_actualizados = []
            for pago_id, monto in entradas:
                pago = pagos_bloqueados[pago_id]
                pago.monto_depositado = Decimal(str(pago.monto_depositado or 0)) + monto
                pago.depositado = True
                pago.fecha_deposito = fecha_deposito

                if s3_key_comprobante:
                    PagoService.schedule_replaced_comprobante(
                        pago.id, pago.url_comprobante, s3_key_comprobante
                    )
                    pago.url_comprobante = s3_key_comprobante

                cliente_nombre = pago.venta.cliente.nombre if pago.venta and pago.venta.cliente else "GENERICO"
                cli_corto = re.sub(r'[^a-zA-Z0-9]', '', cliente_nombre).upper()[:3]
                if not cli_corto:
                    cli_corto = "GEN"
                fecha_str = fecha_deposito.strftime('%Y%m%d')
                monto_str = str(int(monto))
                codigo_unico = uuid.uuid4().hex[:4].upper()

                pago.referencia = f"DEP-{fecha_str}-{cli_corto}-{monto_str}-{codigo_unico}"

                pagos_actualizados.append(pago)

            commit_with_storage_finalization()

            return {
                "message": "Depósito registrado exitosamente.",
                "pagos_actualizados": len(pagos_actualizados),
                "pagos": [pago_schema.dump(p) for p in pagos_actualizados]
            }, 200
        except HTTPException:
            rollback_and_compensate()
            raise
        except PagoValidationError as e:
            rollback_and_compensate()
            return {"error": str(e)}, 400
        except Exception:
            rollback_and_compensate()
            raise


class PagoExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """Exporta pagos a Excel de forma optimizada."""
        try:
            claims = get_jwt()
            actor = obtener_actor(claims.get('sub'))
            query = PagoService.get_pagos_query(request.args.to_dict(), actor.id, actor.rol)
            pagos = query.order_by(desc(Pago.fecha)).all()
            if not pagos:
                return {"message": "No hay pagos para exportar con los filtros seleccionados"}, 404

            data_para_excel = [{
                'ID': p.id, 'Fecha': p.fecha.strftime('%Y-%m-%d') if p.fecha else '',
                'Monto': float(p.monto), 'Método de Pago': p.metodo_pago, 'Referencia': p.referencia,
                'ID Venta': p.venta.id if p.venta else 'N/A',
                'Cliente': p.venta.cliente.nombre if p.venta and p.venta.cliente else 'N/A',
                'Almacén': p.venta.almacen.nombre if p.venta and p.venta.almacen else 'N/A',
                'Usuario': p.usuario.username if p.usuario else 'N/A',
                'Depositado': 'Sí' if p.depositado else 'No',
                'Monto Depositado': float(p.monto_depositado or 0),
                'Fecha Depósito': p.fecha_deposito.strftime('%Y-%m-%d') if p.fecha_deposito else ''
            } for p in pagos]

            df = pd.DataFrame(data_para_excel)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Pagos')
            output.seek(0)
            return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'pagos_{datetime.now().strftime("%Y%m%d")}.xlsx')
        except Exception as e:
            logger.error(f"Error al exportar pagos: {str(e)}")
            return {"error": "Error interno al generar el archivo Excel."}, 500


class CierreCajaResource(Resource):
    @jwt_required()
    def get(self):
        """
        Obtiene datos para el Cierre de Caja de forma optimizada,
        delegando cálculos y filtros a la base de datos.
        """
        try:
            fecha_inicio_str = request.args.get('fecha_inicio')
            fecha_fin_str = request.args.get('fecha_fin')
            if not fecha_inicio_str or not fecha_fin_str:
                return {"error": "Los filtros 'fecha_inicio' y 'fecha_fin' son requeridos."}, 400

            fecha_inicio = parse_iso_datetime(fecha_inicio_str, add_timezone=False)
            fecha_fin = parse_iso_datetime(fecha_fin_str, add_timezone=False)
        except (ValueError, TypeError) as e:
            return {"error": f"Formato de fecha inválido: {e}"}, 400

        almacen_id = request.args.get('almacen_id', type=int)
        usuario_id = request.args.get('usuario_id', type=int)

        monto_en_gerencia_sql = case(
            (
                (Pago.depositado == True) & (Pago.monto_depositado.isnot(None)),
                Pago.monto - Pago.monto_depositado
            ),
            (
                Pago.depositado == False,
                Pago.monto
            ),
            else_=0
        ).label("monto_en_gerencia")

        pagos_pendientes_q = db.session.query(Pago).filter(
            Pago.fecha.between(fecha_inicio, fecha_fin),
            monto_en_gerencia_sql > 0
        )

        gastos_q = db.session.query(Gasto).filter(
            Gasto.fecha.between(fecha_inicio.date(), fecha_fin.date())
        )

        if usuario_id:
            pagos_pendientes_q = pagos_pendientes_q.filter(Pago.usuario_id == usuario_id)
            gastos_q = gastos_q.filter(Gasto.usuario_id == usuario_id)

        if almacen_id:
            pagos_pendientes_q = pagos_pendientes_q.join(Venta).filter(Venta.almacen_id == almacen_id)
            gastos_q = gastos_q.filter(Gasto.almacen_id == almacen_id)

        total_cobrado_pendiente = pagos_pendientes_q.with_entities(
            func.sum(monto_en_gerencia_sql)
        ).scalar() or Decimal('0.0')

        total_gastado = gastos_q.with_entities(
            func.sum(Gasto.monto)
        ).scalar() or Decimal('0.0')

        pagos_pendientes_detalle = pagos_pendientes_q.options(
            joinedload(Pago.venta).joinedload(Venta.cliente),
            joinedload(Pago.usuario)
        ).order_by(Pago.fecha.asc()).all()

        gastos_detalle = gastos_q.options(
            joinedload(Gasto.almacen),
            joinedload(Gasto.usuario)
        ).order_by(Gasto.fecha.asc()).all()

        efectivo_esperado = total_cobrado_pendiente - total_gastado

        return {
            "resumen": {
                "total_cobrado_pendiente": str(total_cobrado_pendiente),
                "total_gastado": str(total_gastado),
                "efectivo_esperado": str(efectivo_esperado)
            },
            "detalles": {
                "pagos_pendientes": pagos_schema.dump(pagos_pendientes_detalle),
                "gastos": gastos_schema.dump(gastos_detalle)
            }
        }, 200
