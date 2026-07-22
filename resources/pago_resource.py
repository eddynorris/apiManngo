# ARCHIVO: resources/pago_resource.py
import json
import logging
import io
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
from flask import request, send_file
from flask_jwt_extended import jwt_required, get_jwt
from flask_restful import Resource
from sqlalchemy import asc, desc, func, case
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import BadRequest, NotFound, Forbidden

from common import MAX_ITEMS_PER_PAGE, handle_db_errors, parse_iso_datetime
from extensions import db
from models import Almacen, Cliente, Pago, Users, Venta, Gasto
from schemas import pago_schema, pagos_schema, gastos_schema
from utils.file_handlers import delete_file, get_presigned_url, save_file
from services.pago_service import PagoService, PagoValidationError

# Configuración de Logging
logger = logging.getLogger(__name__)

# --- FUNCIONES AUXILIARES ---
def _parse_request_data():
    """Unifica la obtención de datos de JSON y multipart/form-data."""
    if 'multipart/form-data' in request.content_type:
        data = request.form.to_dict()
        file = request.files.get('comprobante')
        eliminar_comprobante = data.get('eliminar_comprobante', 'false').lower() == 'true'
        return data, file, eliminar_comprobante
    elif 'application/json' in request.content_type:
        return request.get_json(), None, False
    raise BadRequest("Tipo de contenido no soportado.")

def _get_presigned_url_for_item(item_dump, s3_key):
    """Genera y asigna una URL pre-firmada a un objeto serializado."""
    if s3_key:
        item_dump['url_comprobante'] = get_presigned_url(s3_key)
    return item_dump

# --- RESOURCES DE LA API ---
class PagoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, pago_id=None):
        """Obtiene un pago o una lista paginada de pagos."""
        if pago_id:
            pago = PagoService.find_pago_by_id(pago_id)
            pago_dump = pago_schema.dump(pago)
            return _get_presigned_url_for_item(pago_dump, pago.url_comprobante), 200
        
        claims = get_jwt()
        query = PagoService.get_pagos_query(request.args, claims.get('sub'), claims.get('rol'))
        
        sort_by = request.args.get('sort_by', 'fecha')
        sort_order = request.args.get('sort_order', 'desc').lower()
        sort_column = getattr(Pago, sort_by, Pago.fecha)
        order_func = desc if sort_order == 'desc' else asc
        query = query.order_by(order_func(sort_column))
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pagos_paginados = query.paginate(page=page, per_page=per_page, error_out=False)
        pagos_dump = pagos_schema.dump(pagos_paginados.items)
        
        # MEJORA: Usar zip para una iteración más segura y pitónica.
        for pago_obj, dump_item in zip(pagos_paginados.items, pagos_dump):
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
            raw_data, file, _ = _parse_request_data()
            if raw_data.get('metodo_pago'):
                raw_data['metodo_pago'] = raw_data['metodo_pago'].lower()
                
            nuevo_pago_instancia = pago_schema.load(raw_data)
            usuario_id = get_jwt().get("sub")
            nuevo_pago = PagoService.create_pago(nuevo_pago_instancia, file, usuario_id)
            
            db.session.commit()
            
            pago_dump = pago_schema.dump(nuevo_pago)
            return _get_presigned_url_for_item(pago_dump, nuevo_pago.url_comprobante), 201
        except PagoValidationError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al crear pago: {e}")
            return {"error": "Error interno al procesar el pago."}, 500

    @jwt_required()
    @handle_db_errors
    def put(self, pago_id):
        """Actualiza un pago existente."""
        try:
            raw_data, file, eliminar_comprobante = _parse_request_data()
            pago_existente = PagoService.find_pago_by_id(pago_id)
            pago_modificado = pago_schema.load(raw_data, instance=pago_existente, partial=True)
            pago_actualizado = PagoService.update_pago(pago_modificado, file, eliminar_comprobante)

            db.session.commit()
            
            pago_dump = pago_schema.dump(pago_actualizado)
            return _get_presigned_url_for_item(pago_dump, pago_actualizado.url_comprobante), 200
        except PagoValidationError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al actualizar pago {pago_id}: {e}")
            return {"error": "Error interno al actualizar el pago."}, 500

    @jwt_required()
    @handle_db_errors
    def delete(self, pago_id):
        """Elimina un pago."""
        PagoService.delete_pago(pago_id)
        db.session.commit()
        return {"message": "Pago eliminado exitosamente"}, 200


class PagosPorVentaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id):
        """Obtiene todos los pagos de una venta específica."""
        Venta.query.get_or_404(venta_id)
        pagos = Pago.query.filter_by(venta_id=venta_id).order_by(Pago.fecha.asc()).all()
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
                json_data = request.get_json()
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
                metodo_pago = metodo_pago.lower()

            if not all([pagos_json_str, fecha_str, metodo_pago]):
                return {"error": "Faltan campos requeridos (pagos/pagos_json_data, fecha, metodo_pago)"}, 400
            
            claims = get_jwt()
            pagos_creados = PagoService.create_batch_pagos(
                pagos_json_str, file, fecha_str, metodo_pago, referencia,
                usuario_id=claims.get('sub'),
                rol=claims.get('rol'),
                almacen_id=claims.get('almacen_id')
            )
            db.session.commit()  # Cambio de flush() a commit() para guardar en BD
            created_pagos_dump = pagos_schema.dump(pagos_creados)
            for i, pago in enumerate(pagos_creados):
                _get_presigned_url_for_item(created_pagos_dump[i], pago.url_comprobante)
            return {"message": "Pagos en lote registrados exitosamente.", "pagos_creados": created_pagos_dump}, 201
        except (PagoValidationError, BadRequest) as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        except NotFound as e:
            db.session.rollback()
            return {"error": str(e)}, 404
        except Forbidden as e:
            db.session.rollback()
            return {"error": str(e)}, 403
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error crítico en batch de pagos: {str(e)}")
            return {"error": "Ocurrió un error interno, la operación fue revertida."}, 500

class DepositoBancarioResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra un depósito bancario para uno o múltiples pagos y asocia un comprobante común."""
        comprobante_file = None
        if 'multipart/form-data' in request.content_type:
            depositos_json_str = request.form.get('depositos')
            fecha_deposito_str = request.form.get('fecha_deposito')
            comprobante_file = request.files.get('comprobante_deposito') # Nombre más específico
            
            if not depositos_json_str:
                return {"error": "Campo 'depositos' (JSON string) es requerido"}, 400
            try:
                depositos = json.loads(depositos_json_str)
            except json.JSONDecodeError:
                return {"error": "Formato JSON inválido en 'depositos'"}, 400
        else:
            data = request.get_json()
            if not data: return {"error": "No se proporcionaron datos"}, 400
            depositos = data.get('depositos', [])
            fecha_deposito_str = data.get('fecha_deposito')

        if not depositos or not fecha_deposito_str:
            return {"error": "Campos requeridos: 'depositos' (lista) y 'fecha_deposito'"}, 400
        
        try:
            fecha_deposito = parse_iso_datetime(fecha_deposito_str, add_timezone=True)
        except ValueError:
            return {"error": "Formato de fecha inválido"}, 400

        s3_key_comprobante = None
        if comprobante_file and comprobante_file.filename:
            s3_key_comprobante = save_file(comprobante_file, 'comprobantes_depositos')
            if not s3_key_comprobante:
                return {"error": "Error interno al guardar el comprobante"}, 500

        pago_ids = [d.get('pago_id') for d in depositos]
        pagos = Pago.query.filter(Pago.id.in_(pago_ids)).all()
        pagos_map = {p.id: p for p in pagos}

        if len(pagos) != len(set(pago_ids)):
            if s3_key_comprobante: delete_file(s3_key_comprobante)
            return {"error": "Algunos pagos no fueron encontrados"}, 404

        pagos_actualizados = []
        monto_total_depositado = Decimal('0')

        for deposito_data in depositos:
            pago_id = deposito_data['pago_id']
            monto_a_depositar = Decimal(str(deposito_data.get('monto_depositado', '0')))
            pago = pagos_map[pago_id]
            
            monto_disponible = pago.monto - (pago.monto_depositado or Decimal('0'))
            if monto_a_depositar > monto_disponible + Decimal('0.001'):
                if s3_key_comprobante: delete_file(s3_key_comprobante)
                return {"error": f"Monto para pago {pago.id} excede el disponible {monto_disponible}"}, 400

            if monto_a_depositar > 0:
                pago.monto_depositado = (pago.monto_depositado or Decimal('0')) + monto_a_depositar
                pago.depositado = True
                pago.fecha_deposito = fecha_deposito
                
                # Asigna la URL del comprobante a cada pago
                if s3_key_comprobante:
                    pago.url_comprobante = s3_key_comprobante
                
                # --- GENERAR REFERENCIA AUTOMATICA ---
                # Formato: DEP-YYYYMMDD-CLI-MONTO-UUID
                cliente_nombre = pago.venta.cliente.nombre if pago.venta and pago.venta.cliente else "GENERICO"
                cli_corto = re.sub(r'[^a-zA-Z0-9]', '', cliente_nombre).upper()[:3]
                if not cli_corto:
                    cli_corto = "GEN"
                fecha_str = fecha_deposito.strftime('%Y%m%d')
                monto_str = str(int(monto_a_depositar))
                codigo_unico = uuid.uuid4().hex[:4].upper()
                
                pago.referencia = f"DEP-{fecha_str}-{cli_corto}-{monto_str}-{codigo_unico}"
                # ------------------------------------------
                
                pagos_actualizados.append(pago)
                monto_total_depositado += monto_a_depositar
        
        db.session.commit()

        return {
            "message": "Depósito registrado exitosamente.",
            "pagos_actualizados": len(pagos_actualizados),
            "pagos": [pago_schema.dump(p) for p in pagos_actualizados]
        }, 200


class PagoExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """Exporta pagos a Excel de forma optimizada."""
        try:
            claims = get_jwt()
            query = PagoService.get_pagos_query(request.args.to_dict(), claims.get('sub'), claims.get('rol'))
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
    # @handle_db_errors # Descomenta si tienes este decorador
    def get(self):
        """
        Obtiene datos para el Cierre de Caja de forma optimizada,
        delegando cálculos y filtros a la base de datos.
        """
        # ... (Las secciones 1, 2, 3 y 4 no cambian y siguen siendo correctas) ...
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
                (Pago.depositado == True) & (Pago.monto_depositado != None),
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

        # 5. Ejecutar consultas de agregación y detalle por separado
        
        # --- LÍNEA CORREGIDA ---
        # La forma anterior con .subquery() era el problema.
        # Esta nueva forma es más directa y garantiza que se usan los filtros de pagos_pendientes_q.
        total_cobrado_pendiente = pagos_pendientes_q.with_entities(
            func.sum(monto_en_gerencia_sql)
        ).scalar() or Decimal('0.0')

        # Consulta #2 (Agregación): Calcula el total gastado. Devuelve un solo número.
        total_gastado = gastos_q.with_entities(
            func.sum(Gasto.monto)
        ).scalar() or Decimal('0.0')

        # Consulta #3 (Detalle): Obtiene la lista de pagos pendientes para el reporte.
        pagos_pendientes_detalle = pagos_pendientes_q.options(
            db.joinedload(Pago.venta).joinedload(Venta.cliente),
            db.joinedload(Pago.usuario)
        ).order_by(Pago.fecha.asc()).all()

        # Consulta #4 (Detalle): Obtiene la lista de gastos para el reporte.
        gastos_detalle = gastos_q.options(
            db.joinedload(Gasto.almacen),
            db.joinedload(Gasto.usuario)
        ).order_by(Gasto.fecha.asc()).all()

        # 6. Calcular el resultado y serializar la respuesta
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