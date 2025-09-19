# resources/deposito_bancario_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import DepositoBancario, Almacen, Users
from schemas import deposito_bancario_schema, depositos_bancarios_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido, validate_pagination_params, create_pagination_response, parse_iso_datetime
from utils.file_handlers import save_file, delete_file, get_presigned_url
from datetime import datetime
from decimal import Decimal
import logging
from sqlalchemy import asc, desc

logger = logging.getLogger(__name__)

class DepositoBancarioResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, deposito_id=None):
        """
        Obtiene depósitos bancarios.
        - Con ID: Detalle del depósito.
        - Sin ID: Lista paginada con filtros (almacen_id, fecha_desde, fecha_hasta).
        """
        claims = get_jwt()
        user_almacen_id = claims.get('almacen_id')
        is_admin_or_gerente = claims.get('rol') in ['admin', 'gerente']

        if deposito_id:
            deposito = DepositoBancario.query.get_or_404(deposito_id)
            # Permiso: Admin/Gerente o usuario del mismo almacén
            if not is_admin_or_gerente and deposito.almacen_id != user_almacen_id:
                return {"error": "No tiene permiso para ver este depósito"}, 403
                
            result = deposito_bancario_schema.dump(deposito)
            if deposito.url_comprobante_deposito:
                result['comprobante_url'] = get_presigned_url(deposito.url_comprobante_deposito)
            else:
                result['comprobante_url'] = None # Asegurar que el campo exista
            return result, 200

        # --- Lógica de Ordenación Dinámica ---
        sort_by = request.args.get('sort_by', 'fecha_deposito') # Default a fecha_deposito
        sort_order = request.args.get('sort_order', 'desc').lower() # Default a desc

        # Mapeo de nombres de frontend a columnas SQLAlchemy (incluyendo relaciones)
        sortable_columns = {
            'fecha_deposito': DepositoBancario.fecha_deposito,
            'monto_depositado': DepositoBancario.monto_depositado,
            'referencia_bancaria': DepositoBancario.referencia_bancaria,
            'almacen_nombre': Almacen.nombre,
            'usuario_username': Users.username,
            'created_at': DepositoBancario.created_at
        }

        # Validar sort_by y obtener columna, usar default si es inválido
        column_to_sort = sortable_columns.get(sort_by, DepositoBancario.fecha_deposito)

        # Validar sort_order, usar desc si es inválido
        order_func = desc if sort_order == 'desc' else asc

        # --- Fin Lógica de Ordenación ---

        # Lista paginada y filtrada
        query = DepositoBancario.query

        # --- Aplicar Joins si es necesario para ordenar ---
        if sort_by == 'almacen_nombre':
            query = query.join(Almacen, DepositoBancario.almacen_id == Almacen.id)
        elif sort_by == 'usuario_username':
            query = query.join(Users, DepositoBancario.usuario_id == Users.id)
        # ------------------------------------------------

        # Filtrar por almacén si no es admin/gerente
        if not is_admin_or_gerente:
            query = query.filter(DepositoBancario.almacen_id == user_almacen_id)
        elif request.args.get('almacen_id'): # Admin/Gerente pueden filtrar por almacén
            query = query.filter(DepositoBancario.almacen_id == request.args.get('almacen_id'))

        # Filtrar por fecha
        if fecha_desde := request.args.get('fecha_desde'):
            try:
                query = query.filter(DepositoBancario.fecha_deposito >= parse_iso_datetime(fecha_desde, add_timezone=False))
            except ValueError:
                return {"error": "Formato de fecha_desde inválido (YYYY-MM-DD)"}, 400
        if fecha_hasta := request.args.get('fecha_hasta'):
            try:
                 # Asegurarse de incluir todo el día hasta las 23:59:59.999999
                 fecha_hasta_dt = parse_iso_datetime(fecha_hasta, add_timezone=False).replace(hour=23, minute=59, second=59, microsecond=999999)
                 query = query.filter(DepositoBancario.fecha_deposito <= fecha_hasta_dt)
            except ValueError:
                return {"error": "Formato de fecha_hasta inválido (YYYY-MM-DD)"}, 400

        # --- APLICAR ORDENACIÓN ---
        # Aplicar la ordenación determinada antes de paginar
        query = query.order_by(order_func(column_to_sort))
        # -------------------------

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', MAX_ITEMS_PER_PAGE, type=int)
        paginated_depositos = query.paginate(page=page, per_page=per_page, error_out=False)

        # Generar URLs pre-firmadas para la lista
        dumped_data = depositos_bancarios_schema.dump(paginated_depositos.items)
        for item in dumped_data:
            if item.get('url_comprobante_deposito'):
                item['comprobante_url'] = get_presigned_url(item['url_comprobante_deposito'])
            else:
                 item['comprobante_url'] = None
        # Respuesta estandarizada
        return create_pagination_response(dumped_data, paginated_depositos), 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Crea un nuevo registro de depósito bancario."""
        if 'multipart/form-data' not in request.content_type:
            return {"error": "Se requiere contenido multipart/form-data"}, 415

        # Obtener datos del formulario
        try:
            fecha_deposito_str = request.form.get('fecha_deposito')
            monto_depositado_str = request.form.get('monto_depositado')
            almacen_id_str = request.form.get('almacen_id')
            referencia_bancaria = request.form.get('referencia_bancaria')
            notas = request.form.get('notas')

            if not all([fecha_deposito_str, monto_depositado_str, almacen_id_str]):
                return {"error": "Faltan campos requeridos (fecha_deposito, monto_depositado, almacen_id)"}, 400

            fecha_deposito = parse_iso_datetime(fecha_deposito_str, add_timezone=False)
            monto_depositado = Decimal(monto_depositado_str)
            almacen_id = int(almacen_id_str)
            
            if monto_depositado <= 0:
                 return {"error": "El monto depositado debe ser positivo"}, 400

            # Verificar que el almacén existe
            Almacen.query.get_or_404(almacen_id)

        except (ValueError, TypeError) as e:
            logger.error(f"Error procesando datos del formulario: {e}")
            return {"error": "Datos de formulario inválidos", "details": str(e)}, 400

        claims = get_jwt()
        usuario_id = claims.get('sub')

        # Procesar archivo si existe
        s3_key_comprobante = None
        if 'comprobante' in request.files:
            file = request.files['comprobante']
            if file.filename != '':
                s3_key_comprobante = save_file(file, 'depositos')
                if not s3_key_comprobante:
                    return {"error": "Error al subir el comprobante"}, 500

        # Crear el nuevo depósito
        nuevo_deposito = DepositoBancario(
            fecha_deposito=fecha_deposito,
            monto_depositado=monto_depositado,
            almacen_id=almacen_id,
            usuario_id=usuario_id,
            referencia_bancaria=referencia_bancaria,
            notas=notas,
            url_comprobante_deposito=s3_key_comprobante
        )

        db.session.add(nuevo_deposito)
        db.session.commit()
        logger.info(f"Depósito bancario creado ID: {nuevo_deposito.id} por Usuario ID: {usuario_id}")
        
        # Devolver con URL pre-firmada si aplica
        result = deposito_bancario_schema.dump(nuevo_deposito)
        if nuevo_deposito.url_comprobante_deposito:
             result['comprobante_url'] = get_presigned_url(nuevo_deposito.url_comprobante_deposito)
        else:
             result['comprobante_url'] = None

        return result, 201

    @jwt_required()
    @handle_db_errors
    def put(self, deposito_id):
        """Actualiza un depósito bancario existente."""
        if 'multipart/form-data' not in request.content_type:
            return {"error": "Se requiere contenido multipart/form-data"}, 415

        deposito = DepositoBancario.query.get_or_404(deposito_id)

        try:
            # Actualizar campos si están presentes en el form
            if fecha_str := request.form.get('fecha_deposito'):
                deposito.fecha_deposito = parse_iso_datetime(fecha_str, add_timezone=False)
            if monto_str := request.form.get('monto_depositado'):
                 monto = Decimal(monto_str)
                 if monto <= 0:
                      return {"error": "El monto debe ser positivo"}, 400
                 deposito.monto_depositado = monto
            if almacen_id_str := request.form.get('almacen_id'):
                 almacen_id = int(almacen_id_str)
                 Almacen.query.get_or_404(almacen_id) # Verificar existencia
                 deposito.almacen_id = almacen_id
            if 'referencia_bancaria' in request.form: # Permitir vaciar la referencia
                deposito.referencia_bancaria = request.form.get('referencia_bancaria')
            if 'notas' in request.form: # Permitir vaciar notas
                 deposito.notas = request.form.get('notas')

        except (ValueError, TypeError) as e:
             logger.error(f"Error procesando datos del formulario en PUT: {e}")
             return {"error": "Datos de formulario inválidos", "details": str(e)}, 400

        # Procesar archivo de comprobante (actualizar o eliminar)
        s3_key_anterior = deposito.url_comprobante_deposito
        nueva_key_s3 = None
        eliminar_existente = False

        if 'comprobante' in request.files:
            file = request.files['comprobante']
            if file.filename != '': # Se subió un archivo nuevo
                nueva_key_s3 = save_file(file, 'depositos')
                if not nueva_key_s3:
                    return {"error": "Error al subir el nuevo comprobante"}, 500
                deposito.url_comprobante_deposito = nueva_key_s3
                eliminar_existente = True # Marcar para borrar el anterior después del commit
            # Si 'comprobante' está pero filename es '', no hacer nada con el archivo

        elif request.form.get('eliminar_comprobante') == 'true':
            if s3_key_anterior:
                deposito.url_comprobante_deposito = None
                eliminar_existente = True # Marcar para borrar

        try:
            db.session.commit()
            logger.info(f"Depósito bancario ID: {deposito.id} actualizado.")

            # Eliminar archivo anterior de S3 DESPUÉS de confirmar en DB
            if eliminar_existente and s3_key_anterior:
                delete_file(s3_key_anterior)
                logger.info(f"Comprobante anterior S3 eliminado: {s3_key_anterior}")
        
            # Devolver con URL pre-firmada si aplica
            result = deposito_bancario_schema.dump(deposito)
            if deposito.url_comprobante_deposito:
                 result['comprobante_url'] = get_presigned_url(deposito.url_comprobante_deposito)
            else:
                 result['comprobante_url'] = None
                 
            return result, 200

        except Exception as e:
            db.session.rollback()
            # Si hubo error al guardar, intentar borrar el nuevo archivo S3 si se subió
            if nueva_key_s3:
                delete_file(nueva_key_s3)
                logger.warning(f"Rollback: Nuevo comprobante S3 eliminado: {nueva_key_s3}")
            raise e # Relanzar para que handle_db_errors lo capture

    @jwt_required()
    @rol_requerido('admin', 'gerente') # Solo admin/gerente pueden eliminar
    @handle_db_errors
    def delete(self, deposito_id):
        """Elimina un depósito bancario."""
        deposito = DepositoBancario.query.get_or_404(deposito_id)
        s3_key_a_eliminar = deposito.url_comprobante_deposito

        try:
            db.session.delete(deposito)
            db.session.commit()
            logger.info(f"Depósito bancario ID: {deposito_id} eliminado.")

            # Eliminar archivo de S3 si existía
            if s3_key_a_eliminar:
                if delete_file(s3_key_a_eliminar):
                    logger.info(f"Comprobante S3 asociado eliminado: {s3_key_a_eliminar}")
                else:
                     logger.warning(f"No se pudo eliminar el comprobante S3 asociado: {s3_key_a_eliminar}")
                     # Considerar si esto debe ser un error 500 o solo un warning

            return '', 204 # No content

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al eliminar depósito ID {deposito_id}: {e}")
            # No reintentar la eliminación del archivo S3 en caso de error de DB
            raise e # Relanzar para handle_db_errors