# ARCHIVO: resources/pago_resource.py
import json
import logging
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from flask import request, send_file
from flask_jwt_extended import jwt_required, get_jwt
from flask_restful import Resource, reqparse
from sqlalchemy import asc, desc
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import BadRequest, NotFound, Forbidden

from common import MAX_ITEMS_PER_PAGE, handle_db_errors
from extensions import db
from models import Almacen, Cliente, Pago, Users, Venta
from schemas import pago_schema, pagos_schema
from utils.file_handlers import delete_file, get_presigned_url, save_file

# Configuración de Logging
logger = logging.getLogger(__name__)

# --- EXCEPCIONES PERSONALIZADAS PARA LA LÓGICA DE NEGOCIO ---
class PagoValidationError(ValueError):
    """Error de validación específico para la lógica de pagos."""
    pass

# --- CAPA DE SERVICIO ---
class PagoService:
    """Contiene toda la lógica de negocio para gestionar pagos."""

    @staticmethod
    def find_pago_by_id(pago_id):
        """Encuentra un pago por su ID o lanza un error 404."""
        pago = Pago.query.get(pago_id)
        if not pago:
            raise NotFound("Pago no encontrado.")
        return pago

    @staticmethod
    def _validate_monto(venta, monto, pago_existente_id=None):
        """Valida que el monto de un pago no exceda el saldo pendiente de la venta."""
        pagos_anteriores = sum(p.monto for p in venta.pagos if p.id != pago_existente_id)
        saldo_pendiente = venta.total - pagos_anteriores
        if monto > saldo_pendiente + Decimal("0.001"):
            raise PagoValidationError(
                f"El monto a pagar ({monto}) excede el saldo pendiente ({saldo_pendiente})."
            )

    @staticmethod
    def get_pagos_query(filters):
        """Construye una consulta de pagos optimizada con filtros y carga ansiosa."""
        query = Pago.query.options(
            joinedload(Pago.venta).joinedload(Venta.cliente),
            joinedload(Pago.venta).joinedload(Venta.almacen),
            joinedload(Pago.usuario)
        )
        if venta_id := filters.get('venta_id'):
            query = query.filter(Pago.venta_id == venta_id)
        if metodo := filters.get('metodo_pago'):
            query = query.filter(Pago.metodo_pago == metodo)
        if usuario_id := filters.get('usuario_id'):
            query = query.filter(Pago.usuario_id == usuario_id)
        if almacen_id := filters.get('almacen_id'):
            query = query.join(Venta).filter(Venta.almacen_id == almacen_id)
        if (depositado_str := filters.get('depositado')) is not None:
            is_depositado = depositado_str.lower() == 'true'
            query = query.filter(Pago.depositado == is_depositado)
        if fecha_inicio := filters.get('fecha_inicio'):
             query = query.filter(Pago.fecha >= fecha_inicio)
        if fecha_fin := filters.get('fecha_fin'):
             query = query.filter(Pago.fecha <= fecha_fin)
        return query

    @staticmethod
    def create_pago(data, file, usuario_id):
        """Crea un nuevo pago, valida y actualiza la venta."""
        venta_id = data.get("venta_id")
        if not venta_id:
            raise PagoValidationError("El campo 'venta_id' es requerido.")
        venta = Venta.query.get_or_404(venta_id)
        monto = data.get("monto", Decimal("0"))
        PagoService._validate_monto(venta, monto)
        s3_key = None
        if file and file.filename:
            s3_key = save_file(file, "comprobantes")
            if not s3_key:
                raise Exception("Ocurrió un error interno al guardar el comprobante.")
        nuevo_pago = Pago(**data)
        nuevo_pago.usuario_id = usuario_id
        nuevo_pago.url_comprobante = s3_key
        db.session.add(nuevo_pago)
        venta.actualizar_estado(nuevo_pago)
        return nuevo_pago

    @staticmethod
    def update_pago(pago_id, data, file, eliminar_comprobante):
        """Actualiza un pago existente, valida y gestiona el comprobante."""
        pago = PagoService.find_pago_by_id(pago_id)
        venta = pago.venta
        if "monto" in data:
            PagoService._validate_monto(venta, data["monto"], pago_existente_id=pago_id)
        for key, value in data.items():
            setattr(pago, key, value)
        if eliminar_comprobante and pago.url_comprobante:
            delete_file(pago.url_comprobante)
            pago.url_comprobante = None
        elif file and file.filename:
            if pago.url_comprobante:
                delete_file(pago.url_comprobante)
            s3_key = save_file(file, "comprobantes")
            if not s3_key:
                raise Exception("Error al subir el nuevo comprobante.")
            pago.url_comprobante = s3_key
        venta.actualizar_estado()
        return pago

    @staticmethod
    def delete_pago(pago_id):
        """Elimina un pago, su comprobante y actualiza la venta."""
        pago = PagoService.find_pago_by_id(pago_id)
        venta = pago.venta
        if pago.url_comprobante:
            delete_file(pago.url_comprobante)
        db.session.delete(pago)
        venta.actualizar_estado()

    @staticmethod
    def create_batch_pagos(pagos_json_str, file, fecha_str, metodo_pago, referencia, claims):
        """
        Crea múltiples pagos en lote desde un único comprobante (opcional).
        Operación transaccional: o todo tiene éxito o todo se revierte.
        """
        s3_key_comprobante = None
        try:
            # Solo guardar el archivo si existe.
            if file and file.filename:
                s3_key_comprobante = save_file(file, 'comprobantes')
                if not s3_key_comprobante:
                    raise Exception("Error al subir el comprobante a S3.")

            try:
                pagos_data_list = json.loads(pagos_json_str)
                if not isinstance(pagos_data_list, list) or not pagos_data_list:
                    raise PagoValidationError("pagos_json_data debe ser una lista no vacía.")
                fecha_pago = datetime.fromisoformat(fecha_str)
            except (json.JSONDecodeError, ValueError):
                raise PagoValidationError("Formato JSON o de fecha inválido.")

            ventas_procesadas = {}
            pagos_a_crear_info = []

            for pago_info in pagos_data_list:
                venta_id = pago_info.get('venta_id')
                monto_str = pago_info.get('monto')
                if venta_id is None or monto_str is None:
                    raise PagoValidationError(f"Cada pago debe tener venta_id y monto. Falló en: {pago_info}")
                monto = Decimal(str(monto_str))
                if monto <= 0:
                    raise PagoValidationError(f"El monto para venta_id {venta_id} debe ser positivo.")
                if venta_id not in ventas_procesadas:
                    venta = Venta.query.get(venta_id)
                    if not venta:
                        raise NotFound(f"Venta con ID {venta_id} no encontrada.")
                    if claims.get('rol') != 'admin' and venta.almacen_id != claims.get('almacen_id'):
                        raise Forbidden(f"No tiene permisos para pagos en el almacén de la venta {venta_id}.")
                    ventas_procesadas[venta_id] = {"venta_obj": venta, "saldo_pendiente": venta.saldo_pendiente}
                
                saldo_actual = ventas_procesadas[venta_id]["saldo_pendiente"]
                if monto > saldo_actual + Decimal('0.001'):
                    raise PagoValidationError(f"Monto {monto} para venta {venta_id} excede el saldo de {saldo_actual}.")
                
                ventas_procesadas[venta_id]["saldo_pendiente"] -= monto
                pagos_a_crear_info.append({"venta_id": venta_id, "monto": monto})

            pagos_creados = []
            usuario_id = claims.get('sub')
            for pago_info in pagos_a_crear_info:
                venta = ventas_procesadas[pago_info['venta_id']]['venta_obj']
                nuevo_pago = Pago(
                    venta_id=pago_info['venta_id'], usuario_id=usuario_id, monto=pago_info['monto'],
                    fecha=fecha_pago, metodo_pago=metodo_pago, referencia=referencia,
                    url_comprobante=s3_key_comprobante
                )
                db.session.add(nuevo_pago)
                venta.actualizar_estado(nuevo_pago)
                pagos_creados.append(nuevo_pago)
            return pagos_creados
        except Exception:
            # Si se subió un archivo y algo falló después, se elimina.
            if s3_key_comprobante:
                delete_file(s3_key_comprobante)
            raise

# --- FUNCIONES AUXILIARES PARA RESOURCES ---
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
    item_dump['url_comprobante'] = get_presigned_url(s3_key) if s3_key else None
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
        
        query = PagoService.get_pagos_query(request.args)
        sort_by = request.args.get('sort_by', 'fecha')
        sort_order = request.args.get('sort_order', 'desc').lower()
        sort_column = getattr(Pago, sort_by, Pago.fecha)
        order_func = desc if sort_order == 'desc' else asc
        query = query.order_by(order_func(sort_column))
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pagos_paginados = query.paginate(page=page, per_page=per_page, error_out=False)
        pagos_dump = pagos_schema.dump(pagos_paginados.items)
        for i, pago in enumerate(pagos_paginados.items):
             _get_presigned_url_for_item(pagos_dump[i], pago.url_comprobante)
        return {"data": pagos_dump, "pagination": {"total": pagos_paginados.total, "page": pagos_paginados.page, "per_page": pagos_paginados.per_page, "pages": pagos_paginados.pages,}}, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra un nuevo pago."""
        try:
            raw_data, file, _ = _parse_request_data()
            if raw_data.get('metodo_pago'):
                raw_data['metodo_pago'] = raw_data['metodo_pago'].lower()
            data = pago_schema.load(raw_data)
            usuario_id = get_jwt().get("sub")
            nuevo_pago = PagoService.create_pago(data, file, usuario_id)
            db.session.commit()  # Cambio de flush() a commit() para guardar en BD
            pago_dump = pago_schema.dump(nuevo_pago)
            return _get_presigned_url_for_item(pago_dump, nuevo_pago.url_comprobante), 201
        except PagoValidationError as e:
            return {"error": str(e)}, 400
        except Exception as e:
            logger.error(f"Error al crear pago: {e}")
            return {"error": "Error interno al procesar el pago."}, 500

    @jwt_required()
    @handle_db_errors
    def put(self, pago_id):
        """Actualiza un pago existente."""
        try:
            raw_data, file, eliminar_comprobante = _parse_request_data()
            data = pago_schema.load(raw_data, partial=True)
            pago_actualizado = PagoService.update_pago(pago_id, data, file, eliminar_comprobante)
            pago_dump = pago_schema.dump(pago_actualizado)
            return _get_presigned_url_for_item(pago_dump, pago_actualizado.url_comprobante), 200
        except PagoValidationError as e:
            return {"error": str(e)}, 400
        except Exception as e:
            logger.error(f"Error al actualizar pago {pago_id}: {e}")
            return {"error": "Error interno al actualizar el pago."}, 500

    @jwt_required()
    @handle_db_errors
    def delete(self, pago_id):
        """Elimina un pago."""
        PagoService.delete_pago(pago_id)
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
        """Registra múltiples pagos para un solo comprobante (pago en lote)."""
        if 'multipart/form-data' not in request.content_type:
            return {"error": "Se requiere contenido multipart/form-data"}, 415
        try:
            pagos_json_str = request.form.get('pagos_json_data')
            fecha_str = request.form.get('fecha')
            metodo_pago = request.form.get('metodo_pago')
            if metodo_pago:
                metodo_pago = metodo_pago.lower()
            referencia = request.form.get('referencia')
            file = request.files.get('comprobante')

            if not all([pagos_json_str, fecha_str, metodo_pago]):
                return {"error": "Faltan campos (pagos_json_data, fecha, metodo_pago)"}, 400
            
            claims = get_jwt()
            pagos_creados = PagoService.create_batch_pagos(
                pagos_json_str, file, fecha_str, metodo_pago, referencia, claims
            )
            db.session.commit()  # Cambio de flush() a commit() para guardar en BD
            created_pagos_dump = pagos_schema.dump(pagos_creados)
            for i, pago in enumerate(pagos_creados):
                _get_presigned_url_for_item(created_pagos_dump[i], pago.url_comprobante)
            return {"message": "Pagos en lote registrados exitosamente.", "pagos_creados": created_pagos_dump}, 201
        except (PagoValidationError, NotFound, BadRequest) as e:
            return {"error": str(e)}, 400
        except Forbidden as e:
            return {"error": str(e)}, 403
        except Exception as e:
            logger.error(f"Error crítico en batch de pagos: {str(e)}")
            return {"error": "Ocurrió un error interno, la operación fue revertida."}, 500

class DepositoBancarioResource(Resource):
    """Endpoint para registrar depósitos bancarios de pagos existentes"""
    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra un depósito bancario para uno o múltiples pagos"""
        data = request.get_json()
        if not data:
            return {"error": "No se proporcionaron datos"}, 400
        pago_ids = data.get('pago_ids', [])
        monto_depositado = data.get('monto_depositado')
        fecha_deposito = data.get('fecha_deposito')
        if not all([pago_ids, monto_depositado, fecha_deposito]):
            return {"error": "Campos requeridos: pago_ids, monto_depositado, fecha_deposito"}, 400
        try:
            monto_depositado = Decimal(str(monto_depositado))
        except (ValueError, InvalidOperation):
            return {"error": "Monto depositado inválido"}, 400
        pagos = Pago.query.filter(Pago.id.in_(pago_ids)).all()
        if len(pagos) != len(pago_ids):
            return {"error": "Algunos pagos no fueron encontrados"}, 404
        monto_total_pagos = sum(p.monto for p in pagos if not p.depositado)
        if monto_depositado > monto_total_pagos:
            return {"error": f"Monto depositado {monto_depositado} excede el total de pagos pendientes {monto_total_pagos}"}, 400
        
        pagos_actualizados = []
        monto_restante = monto_depositado
        for pago in sorted(pagos, key=lambda p: p.fecha):
            if monto_restante <= 0: break
            monto_a_depositar = min(pago.monto - (pago.monto_depositado or 0), monto_restante)
            if monto_a_depositar > 0:
                pago.monto_depositado = (pago.monto_depositado or 0) + monto_a_depositar
                pago.depositado = True
                pago.fecha_deposito = datetime.fromisoformat(fecha_deposito.replace('Z', '+00:00')) if isinstance(fecha_deposito, str) else fecha_deposito
                monto_restante -= monto_a_depositar
                pagos_actualizados.append(pago)
        return {"message": "Depósito registrado exitosamente.", "pagos_actualizados": len(pagos_actualizados), "monto_total_depositado": str(monto_depositado), "pagos": [pago_schema.dump(p) for p in pagos_actualizados]}, 200
        
    @jwt_required()
    @handle_db_errors
    def get(self):
        """Obtiene resumen de depósitos y montos en gerencia"""
        query = db.session.query(Pago).join(Venta)
        if fecha_desde := request.args.get('fecha_desde'):
            query = query.filter(Pago.fecha >= fecha_desde)
        if fecha_hasta := request.args.get('fecha_hasta'):
            query = query.filter(Pago.fecha <= fecha_hasta)
        if almacen_id := request.args.get('almacen_id'):
            query = query.filter(Venta.almacen_id == almacen_id)
        pagos = query.all()
        total_pagos = sum(p.monto for p in pagos)
        total_depositado = sum(p.monto_depositado or 0 for p in pagos)
        total_en_gerencia = total_pagos - total_depositado
        return {"resumen": {"total_pagos": str(total_pagos), "total_depositado": str(total_depositado), "total_en_gerencia": str(total_en_gerencia)}, "pagos_depositados": [pago_schema.dump(p) for p in pagos if p.depositado], "pagos_pendientes_deposito": [pago_schema.dump(p) for p in pagos if not p.depositado]}, 200

class PagoExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """Exporta pagos a Excel de forma optimizada."""
        try:
            query = PagoService.get_pagos_query(request.args.to_dict())
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
