# ARCHIVO: resources/pago_resource.py
import json
import logging
import io
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

# Configuración de Logging
logger = logging.getLogger(__name__)

# --- EXCEPCIONES PERSONALIZADAS ---
class PagoValidationError(ValueError):
    """Error de validación específico para la lógica de pagos."""
    pass

# --- CAPA DE SERVICIO ---
class PagoService:
    """Contiene toda la lógica de negocio para gestionar pagos."""

    @staticmethod
    def find_pago_by_id(pago_id):
        """Encuentra un pago por su ID o lanza un error 404 si no se encuentra."""
        # MEJORA: Usar db.session.get() y get_or_404 para consistencia y simplicidad.
        pago = db.session.get(Pago, pago_id)
        if not pago:
            raise NotFound("Pago no encontrado.")
        return pago

    @staticmethod
    def _validate_monto(venta, monto, pago_existente_id=None):
        """Valida que el monto de un pago no exceda el saldo pendiente de la venta."""
        pagos_anteriores = sum(p.monto for p in venta.pagos if p.id != pago_existente_id)
        saldo_pendiente = venta.total - pagos_anteriores
        # Se usa una pequeña tolerancia para evitar errores de punto flotante con Decimal
        if monto > saldo_pendiente + Decimal("0.001"):
            raise PagoValidationError(
                f"El monto a pagar ({monto}) excede el saldo pendiente ({saldo_pendiente})."
            )

    @staticmethod
    def get_pagos_query(filters):
        """Construye una consulta de pagos optimizada con filtros y carga ansiosa (eager loading)."""
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
        venta.actualizar_estado() # La actualización ahora se basa en el estado de la sesión
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
        """Elimina un pago, su comprobante (solo si no es usado por otros pagos) y actualiza la venta."""
        pago = PagoService.find_pago_by_id(pago_id)
        venta = pago.venta
        
        if pago.url_comprobante:
            # --- LÓGICA DE SEGURIDAD AÑADIDA ---
            # Contar cuántos otros pagos usan el mismo comprobante.
            otros_pagos_con_mismo_comprobante = db.session.query(Pago.id).filter(
                Pago.url_comprobante == pago.url_comprobante,
                Pago.id != pago.id
            ).count()

            # Solo borrar el archivo si ningún otro pago lo está usando.
            if otros_pagos_con_mismo_comprobante == 0:
                delete_file(pago.url_comprobante)
        
        db.session.delete(pago)
        venta.actualizar_estado()

    @staticmethod
    def create_batch_pagos(pagos_json_str, file, fecha_str, metodo_pago, referencia, claims):
        """Crea múltiples pagos en lote. Operación transaccional."""
        s3_key_comprobante = None
        try:
            if file and file.filename:
                s3_key_comprobante = save_file(file, 'comprobantes')
                if not s3_key_comprobante:
                    raise Exception("Error al subir el comprobante a S3.")

            try:
                pagos_data_list = json.loads(pagos_json_str)
                if not isinstance(pagos_data_list, list) or not pagos_data_list:
                    raise PagoValidationError("pagos_json_data debe ser una lista no vacía.")
                fecha_pago = parse_iso_datetime(fecha_str, add_timezone=False)
            except (json.JSONDecodeError, ValueError):
                raise PagoValidationError("Formato JSON o de fecha inválido.")

            venta_ids = {p.get('venta_id') for p in pagos_data_list if p.get('venta_id') is not None}
            if not venta_ids:
                raise PagoValidationError("No se proporcionaron IDs de venta en los datos de pagos.")

            # OPTIMIZACIÓN: Realizar una sola consulta para todas las ventas.
            ventas = Venta.query.filter(Venta.id.in_(venta_ids)).all()
            ventas_map = {v.id: v for v in ventas}
            
            if len(ventas_map) != len(venta_ids):
                raise NotFound("Una o más ventas no fueron encontradas.")

            pagos_a_crear_info = []
            saldos_provisionales = {vid: v.saldo_pendiente for vid, v in ventas_map.items()}

            for pago_info in pagos_data_list:
                venta_id = pago_info.get('venta_id')
                monto_str = pago_info.get('monto')

                if venta_id is None or monto_str is None:
                    raise PagoValidationError(f"Cada pago debe tener venta_id y monto. Falló en: {pago_info}")

                venta = ventas_map.get(venta_id)
                if not venta:
                    raise NotFound(f"Venta con ID {venta_id} no encontrada (esto no debería ocurrir).")
                
                if claims.get('rol') != 'admin' and venta.almacen_id != claims.get('almacen_id'):
                    raise Forbidden(f"No tiene permisos para pagos en el almacén de la venta {venta_id}.")
                
                monto = Decimal(str(monto_str))
                if monto <= 0:
                    raise PagoValidationError(f"El monto para venta_id {venta_id} debe ser positivo.")

                saldo_actual = saldos_provisionales[venta_id]
                if monto > saldo_actual + Decimal('0.001'):
                    raise PagoValidationError(f"Monto {monto} para venta {venta_id} excede el saldo de {saldo_actual}.")
                
                saldos_provisionales[venta_id] -= monto
                pagos_a_crear_info.append({"venta_id": venta_id, "monto": monto})

            pagos_creados = []
            usuario_id = claims.get('sub')
            for pago_info in pagos_a_crear_info:
                nuevo_pago = Pago(
                    venta_id=pago_info['venta_id'], usuario_id=usuario_id, monto=pago_info['monto'],
                    fecha=fecha_pago, metodo_pago=metodo_pago, referencia=referencia,
                    url_comprobante=s3_key_comprobante
                )
                db.session.add(nuevo_pago)
                pagos_creados.append(nuevo_pago)
            
            db.session.flush()
            # Actualizar el estado de todas las ventas afectadas al final
            for venta in ventas_map.values():
                venta.actualizar_estado()

            return pagos_creados
        except Exception:
            if s3_key_comprobante:
                delete_file(s3_key_comprobante)
            raise

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
                
            data = pago_schema.load(raw_data)
            usuario_id = get_jwt().get("sub")
            nuevo_pago = PagoService.create_pago(data, file, usuario_id)
            
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
            data = pago_schema.load(raw_data, partial=True)
            pago_actualizado = PagoService.update_pago(pago_id, data, file, eliminar_comprobante)

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

            fecha_inicio = datetime.fromisoformat(fecha_inicio_str)
            fecha_fin = datetime.fromisoformat(fecha_fin_str)
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