# ARCHIVO: resources/pago_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request # Eliminado jsonify
from models import Pago, Venta, Users
from schemas import pago_schema, pagos_schema # Asegúrate que pagos_schema exista y sea correcto
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
from decimal import Decimal
from utils.file_handlers import save_file, delete_file, get_presigned_url
from sqlalchemy import asc, desc # Importar asc y desc
import json
import logging # <--- AÑADE ESTA LÍNEA
logger = logging.getLogger(__name__) # <--- Y ESTA LÍNEA
# from werkzeug.datastructures import FileStorage # No usado directamente aquí
# from flask import current_app # No usado directamente aquí

class PagoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, pago_id=None):
        """
        Obtiene pagos registrados
        - Con ID: Detalle completo del pago con relación a la venta
        - Sin ID: Lista paginada con filtros (venta_id, método_pago)
        """
        if pago_id:
            pago = Pago.query.get_or_404(pago_id)
            # Serializar datos básicos
            result = pago_schema.dump(pago)
            # Generar URL pre-firmada si hay clave S3 y SOBRESCRIBIR el campo original
            if pago.url_comprobante:
                # --- CORRECCIÓN: Sobrescribir 'url_comprobante' ---
                presigned_url = get_presigned_url(pago.url_comprobante)
                result['url_comprobante'] = presigned_url
            else:
                 # Asegurar que el campo exista como None si no hay clave
                 result['url_comprobante'] = None
            return result, 200

        # --- Lógica de Ordenación Dinámica ---
        sort_by = request.args.get('sort_by', 'fecha') # Default a fecha
        sort_order = request.args.get('sort_order', 'desc').lower() # Default a desc

        sortable_columns = {
            'fecha': Pago.fecha,
            'monto': Pago.monto,
            'metodo_pago': Pago.metodo_pago,
            'referencia': Pago.referencia,
            'venta_id': Pago.venta_id, # Ordenar por ID de venta
            'usuario_username': Users.username # Relacionado
        }

        column_to_sort = sortable_columns.get(sort_by, Pago.fecha)
        order_func = desc if sort_order == 'desc' else asc
        # --- Fin Lógica de Ordenación ---

        query = Pago.query

        # --- Aplicar Joins si es necesario para ordenar ---
        if sort_by == 'usuario_username':
             # Outerjoin porque usuario_id puede ser NULL
            query = query.outerjoin(Users, Pago.usuario_id == Users.id)
        # ------------------------------------------------

        # Construir query con filtros
        if venta_id := request.args.get('venta_id'):
            query = query.filter_by(venta_id=venta_id)
        if metodo := request.args.get('metodo_pago'):
            query = query.filter_by(metodo_pago=metodo)
        if usuario_id := request.args.get('usuario_id'):
            query = query.filter_by(usuario_id=usuario_id)

        # --- APLICAR ORDENACIÓN ---
        # Quitar la ordenación fija anterior y aplicar la nueva
        query = query.order_by(order_func(column_to_sort))
        # -------------------------

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pagos = query.paginate(page=page, per_page=per_page, error_out=False)

        # Preparar datos para respuesta, incluyendo URLs pre-firmadas para la lista
        items_data = []
        for item in pagos.items:
            dumped_item = pago_schema.dump(item) # Asumiendo que quieres el detalle de cada pago
            if item.url_comprobante:
                # --- CORRECCIÓN: Sobrescribir 'url_comprobante' ---
                dumped_item['url_comprobante'] = get_presigned_url(item.url_comprobante)
            else:
                dumped_item['url_comprobante'] = None # Asegurar que el campo exista
            items_data.append(dumped_item)

        return {
            "data": items_data,
            "pagination": {
                "total": pagos.total,
                "page": pagos.page,
                "per_page": pagos.per_page,
                "pages": pagos.pages
            }
        }, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra nuevo pago con posibilidad de adjuntar comprobante"""
        # Procesar datos JSON
        if 'application/json' in request.content_type:
            data = request.get_json() # Obtener el diccionario directamente
            # Validar con Marshmallow
            errors = pago_schema.validate(data)
            if errors:
                 return {"errors": errors}, 400

            # Cargar datos validados (sin crear instancia aún si necesitamos lógica compleja)
            venta_id = data.get('venta_id')
            monto_str = data.get('monto')
            metodo_pago = data.get('metodo_pago')
            referencia = data.get('referencia')
            fecha = data.get('fecha') # Asegúrate que el schema maneje la conversión de fecha

            if not all([venta_id, monto_str, metodo_pago]):
                 return {"error": "Faltan campos requeridos (venta_id, monto, metodo_pago)"}, 400

            venta = Venta.query.get_or_404(venta_id)
            monto = Decimal(monto_str) # Convertir a Decimal

            # Calcular saldo pendiente ANTES de añadir el nuevo pago
            saldo_pendiente_venta = venta.total - sum(pago.monto for pago in venta.pagos)

            if monto > saldo_pendiente_venta:
                return {"error": f"Monto {monto} excede el saldo pendiente {saldo_pendiente_venta}"}, 400

            nuevo_pago = Pago(
                venta_id=venta.id,
                monto=monto,
                metodo_pago=metodo_pago,
                referencia=referencia,
                fecha=fecha, # Asumiendo que el schema ya lo convirtió a objeto date/datetime
                usuario_id=get_jwt().get('sub')
                # url_comprobante se maneja en multipart
            )

            db.session.add(nuevo_pago)
            venta.actualizar_estado(nuevo_pago) # Pasar el objeto pago nuevo
            db.session.commit()

            return pago_schema.dump(nuevo_pago), 201

        # Procesar formulario multipart con archivos
        elif 'multipart/form-data' in request.content_type:
            # Obtener datos del formulario
            venta_id = request.form.get('venta_id')
            monto_str = request.form.get('monto')
            metodo_pago = request.form.get('metodo_pago')
            referencia = request.form.get('referencia')
            fecha = request.form.get('fecha') # Considerar parsear fecha si viene como string

            # Validaciones básicas
            if not all([venta_id, monto_str, metodo_pago]):
                return {"error": "Faltan campos requeridos (venta_id, monto, metodo_pago)"}, 400

            try:
                 monto = Decimal(monto_str)
            except Exception:
                 return {"error": "Monto inválido"}, 400

            venta = Venta.query.get_or_404(venta_id)
            # Calcular saldo pendiente ANTES de añadir el nuevo pago
            saldo_pendiente_venta = venta.total - sum(pago.monto for pago in venta.pagos)

            if monto > saldo_pendiente_venta:
                 return {"error": f"Monto {monto} excede el saldo pendiente {saldo_pendiente_venta}"}, 400

            # Procesar comprobante si existe
            s3_key_comprobante = None
            if 'comprobante' in request.files:
                file = request.files['comprobante']
                if file.filename == '': # Chequeo si se envió el campo pero sin archivo
                     pass # No hacer nada si no hay archivo real
                else:
                     s3_key_comprobante = save_file(file, 'comprobantes') # save_file devuelve la clave
                     if not s3_key_comprobante:
                         return {"error": "Error al subir el comprobante"}, 500

            # Crear pago
            nuevo_pago = Pago(
                venta_id=venta_id,
                monto=monto,
                metodo_pago=metodo_pago,
                referencia=referencia,
                fecha=fecha, # Parsear si es necesario: datetime.strptime(fecha, '%Y-%m-%d').date()
                usuario_id=get_jwt().get('sub'),
                url_comprobante=s3_key_comprobante # Guardar la clave S3
            )

            db.session.add(nuevo_pago)
            venta.actualizar_estado(nuevo_pago) # Pasar el objeto pago nuevo
            db.session.commit()

            return pago_schema.dump(nuevo_pago), 201

        return {"error": "Tipo de contenido no soportado"}, 415

    @jwt_required()
    @handle_db_errors
    def put(self, pago_id):
        """Actualiza pago con posibilidad de cambiar comprobante"""
        pago = Pago.query.get_or_404(pago_id)
        venta = pago.venta
        monto_original = pago.monto

        # Actualización JSON
        if 'application/json' in request.content_type:
            data = request.get_json()
            # Validar con Marshmallow (partial=True)
            errors = pago_schema.validate(data, partial=True)
            if errors:
                 return {"errors": errors}, 400

            # Calcular ajuste si el monto cambia
            ajuste_monto = Decimal(0)
            if 'monto' in data:
                nuevo_monto = Decimal(data['monto'])
                # Calcular saldo disponible EXCLUYENDO el pago actual
                saldo_actual_sin_pago = venta.total - sum(p.monto for p in venta.pagos if p.id != pago_id)

                if nuevo_monto > saldo_actual_sin_pago:
                    return {"error": f"Nuevo monto {nuevo_monto} excede saldo pendiente {saldo_actual_sin_pago}"}, 400
                ajuste_monto = nuevo_monto - monto_original

            # Usar Marshmallow para cargar los datos en la instancia existente
            # Esto actualiza los campos de 'pago' con los valores de 'data'
            pago = pago_schema.load(data, instance=pago, partial=True, session=db.session)

            # Crear objeto temporal para actualizar estado de venta si hubo ajuste
            if ajuste_monto != Decimal(0):
                 class AjustePago:
                     def __init__(self, monto): self.monto = monto
                 venta.actualizar_estado(AjustePago(ajuste_monto))

            db.session.commit()
            return pago_schema.dump(pago), 200

        # Actualización con formulario multipart
        elif 'multipart/form-data' in request.content_type:
            ajuste_monto = Decimal(0)
            # Actualizar monto si se proporciona
            if 'monto' in request.form:
                try:
                    nuevo_monto = Decimal(request.form.get('monto'))
                except Exception:
                    return {"error": "Monto inválido"}, 400

                saldo_actual_sin_pago = venta.total - sum(p.monto for p in venta.pagos if p.id != pago_id)
                if nuevo_monto > saldo_actual_sin_pago:
                    return {"error": f"Nuevo monto {nuevo_monto} excede saldo pendiente {saldo_actual_sin_pago}"}, 400

                ajuste_monto = nuevo_monto - monto_original
                pago.monto = nuevo_monto

            # Actualizar otros campos
            if 'metodo_pago' in request.form:
                pago.metodo_pago = request.form.get('metodo_pago')
            if 'referencia' in request.form:
                pago.referencia = request.form.get('referencia')
            if 'fecha' in request.form:
                 # Parsear si es necesario
                 pago.fecha = request.form.get('fecha')

            # Procesar comprobante si existe (al actualizar)
            if 'comprobante' in request.files:
                file = request.files['comprobante']
                if file.filename != '': # Solo procesar si se subió un archivo nuevo
                    # Eliminar comprobante anterior si existe (usando la clave S3)
                    if pago.url_comprobante:
                        delete_file(pago.url_comprobante)
                    # Guardar nuevo comprobante y obtener su clave S3
                    s3_key_nuevo = save_file(file, 'comprobantes')
                    if s3_key_nuevo:
                        pago.url_comprobante = s3_key_nuevo # Actualizar clave en el modelo
                    else:
                        return {"error": "Error al subir el nuevo comprobante"}, 500
                # Si 'comprobante' está en files pero filename es '', no hacer nada (no se subió archivo)

            # Si se especifica eliminar el comprobante (y no se subió uno nuevo)
            elif request.form.get('eliminar_comprobante') == 'true' and pago.url_comprobante:
                delete_file(pago.url_comprobante) # Eliminar usando la clave S3
                pago.url_comprobante = None

            # Actualizar estado de la venta si hubo ajuste
            if ajuste_monto != Decimal(0):
                 class AjustePago:
                     def __init__(self, monto): self.monto = monto
                 venta.actualizar_estado(AjustePago(ajuste_monto))

            db.session.commit()
            return pago_schema.dump(pago), 200

        return {"error": "Tipo de contenido no soportado"}, 415

    @jwt_required()
    @handle_db_errors
    def delete(self, pago_id):
        """Elimina pago y su comprobante asociado"""
        pago = Pago.query.get_or_404(pago_id)
        venta = pago.venta
        # El ajuste es el monto del pago que se elimina (negativo para la venta)
        ajuste_monto = -pago.monto

        # Eliminar comprobante de S3 si existe (usando la clave)
        if pago.url_comprobante:
            delete_file(pago.url_comprobante)

        # Crear un objeto temporal para actualizar estado de venta
        class AjustePago:
            def __init__(self, monto): self.monto = monto
        venta.actualizar_estado(AjustePago(ajuste_monto))

        db.session.delete(pago)
        db.session.commit()

        # --- CORRECCIÓN: Devolver un mensaje JSON ---
        return {'message': "Pago eliminado exitosamente"}, 200


# --- NUEVO RECURSO PARA OBTENER PAGOS POR VENTA --- 
class PagosPorVentaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id):
        """
        Obtiene todos los pagos asociados a una venta específica, sin paginación.
        Incluye URLs pre-firmadas para los comprobantes.
        """
        # Validar que la venta exista (opcional pero recomendado)
        Venta.query.get_or_404(venta_id)
        
        pagos = Pago.query.filter_by(venta_id=venta_id).order_by(Pago.fecha.asc()).all()
        
        # Preparar datos con URLs pre-firmadas
        pagos_data = []
        for pago in pagos:
            dumped_pago = pago_schema.dump(pago)
            if pago.url_comprobante:
                # Reemplazar clave S3 con URL pre-firmada
                dumped_pago['url_comprobante'] = get_presigned_url(pago.url_comprobante)
            else:
                 # Asegurar que el campo exista como None si no hay clave
                 dumped_pago['url_comprobante'] = None
            pagos_data.append(dumped_pago)
            
        # Devolver la lista directamente
        return pagos_data, 200
# --- FIN NUEVO RECURSO ---
class PagoBatchResource(Resource):
    @jwt_required()
    @handle_db_errors # Decorator to handle DB session commit/rollback and errors
    def post(self):
        """
        Registra múltiples pagos para múltiples ventas asociados a un solo comprobante (depósito).
        Espera datos en formato multipart/form-data:
        - 'pagos_json_data': Un string JSON con una lista de objetos, cada uno con "venta_id" y "monto".
                           Ej: '[{"venta_id": 1, "monto": "50.75"}, {"venta_id": 2, "monto": "100.20"}]'
        - 'fecha': Fecha del depósito/pago (ISO format, e.g., "YYYY-MM-DDTHH:MM:SS").
        - 'metodo_pago': Método de pago (e.g., "transferencia", "deposito").
        - 'referencia': Referencia bancaria o del depósito (opcional).
        - 'comprobante': El archivo del voucher/comprobante.
        """
        if 'multipart/form-data' not in request.content_type:
            return {"error": "Se requiere contenido multipart/form-data"}, 415

        try:
            pagos_json_data_str = request.form.get('pagos_json_data')
            fecha_str = request.form.get('fecha')
            metodo_pago = request.form.get('metodo_pago')
            referencia = request.form.get('referencia') # Puede ser None

            if not all([pagos_json_data_str, fecha_str, metodo_pago]):
                return {"error": "Faltan campos requeridos (pagos_json_data, fecha, metodo_pago)"}, 400

            pagos_data_list = json.loads(pagos_json_data_str)
            if not isinstance(pagos_data_list, list) or not pagos_data_list:
                return {"error": "pagos_json_data debe ser una lista no vacía de información de pagos"}, 400

            fecha_pago = datetime.fromisoformat(fecha_str)

        except json.JSONDecodeError:
            logger.error("Error decodificando pagos_json_data")
            return {"error": "Formato JSON inválido en pagos_json_data"}, 400
        except ValueError:
            logger.error(f"Formato de fecha inválido: {fecha_str}")
            return {"error": "Formato de fecha inválido. Usar ISO 8601 (ej: YYYY-MM-DDTHH:MM:SS)"}, 400
        except Exception as e:
            logger.error(f"Error procesando datos del formulario (batch pagos): {e}")
            return {"error": "Datos de formulario inválidos"}, 400

        s3_key_comprobante = None
        if 'comprobante' not in request.files or not request.files['comprobante'].filename:
            return {"error": "Se requiere un archivo de comprobante"}, 400
        
        file = request.files['comprobante']
        s3_key_comprobante = save_file(file, 'comprobantes') # Using 'comprobantes' subfolder
        if not s3_key_comprobante:
            # save_file logs its own errors, so we just return
            return {"error": "Error al subir el comprobante"}, 500

        claims = get_jwt()
        usuario_id = claims.get('sub')
        created_pagos_response = []
        
        pagos_a_crear = [] # Para añadir a la sesión al final si todo va bien

        for pago_info in pagos_data_list:
            venta_id = pago_info.get('venta_id')
            monto_str = pago_info.get('monto')

            if venta_id is None or monto_str is None: # Check for None explicitly
                # No need to rollback here as nothing is added to session yet in loop
                return {"error": f"Cada pago en la lista debe tener venta_id y monto. Falló en: {pago_info}"}, 400

            try:
                monto = Decimal(str(monto_str))
                if monto <= Decimal('0'): # Pagos deben ser positivos
                    return {"error": f"El monto del pago para venta_id {venta_id} debe ser positivo."}, 400
            except InvalidOperation:
                return {"error": f"Monto inválido '{monto_str}' para venta_id {venta_id}."}, 400

            venta = Venta.query.get(venta_id)
            if not venta:
                return {"error": f"Venta con ID {venta_id} no encontrada."}, 404
            
            # Verificar que el almacén de la venta sea accesible si aplica la restricción
            # (mismo_almacen_o_admin ya protege el endpoint en general, pero una verificación
            #  a nivel de venta individual puede ser útil si un admin puede operar en varios almacenes
            #  y se le pasa una venta de un almacén no intencionado en el JSON)
            if claims.get('rol') != 'admin' and venta.almacen_id != claims.get('almacen_id'):
                return {"error": f"No tiene permisos para registrar un pago para la venta {venta_id} en el almacén {venta.almacen_id}"}, 403


            # Es importante usar la propiedad saldo_pendiente que calcula correctamente
            saldo_pendiente_venta = venta.saldo_pendiente #
            if monto > saldo_pendiente_venta:
                return {
                    "error": f"Monto {monto} para venta_id {venta_id} excede el saldo pendiente de {saldo_pendiente_venta}"
                }, 400

            nuevo_pago_obj = Pago(
                venta_id=venta.id,
                usuario_id=usuario_id,
                monto=monto,
                fecha=fecha_pago,
                metodo_pago=metodo_pago,
                referencia=referencia,
                url_comprobante=s3_key_comprobante # Clave S3 del comprobante único
            )
            pagos_a_crear.append({'pago_obj': nuevo_pago_obj, 'venta_obj': venta})

        # Si todas las validaciones individuales pasan, añadir a la sesión y actualizar estado
        for item in pagos_a_crear:
            pago_obj = item['pago_obj']
            venta_obj = item['venta_obj']
            db.session.add(pago_obj)
            venta_obj.actualizar_estado(pago_obj) # Actualizar estado de la venta
            # Se genera URL pre-firmada al serializar si la clave S3 existe
            dumped_pago = pago_schema.dump(pago_obj)
            if pago_obj.url_comprobante and 'url_comprobante' in dumped_pago: # El schema debe generar la url pre-firmada
                 dumped_pago['comprobante_url'] = get_presigned_url(pago_obj.url_comprobante) #
            else:
                 dumped_pago['comprobante_url'] = None
            created_pagos_response.append(dumped_pago)
            
        # El decorador @handle_db_errors se encargará de db.session.commit()
        # o db.session.rollback() en caso de excepción durante el commit.
        logger.info(f"Batch de {len(created_pagos_response)} pagos procesados por usuario {usuario_id} con comprobante {s3_key_comprobante}")
        return {
            "message": "Pagos registrados en batch exitosamente.",
            "pagos_creados": created_pagos_response
        }, 201
