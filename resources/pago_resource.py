# ARCHIVO: resources/pago_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request # Eliminado jsonify
from models import Pago, Venta
from schemas import pago_schema, pagos_schema # Asegúrate que pagos_schema exista y sea correcto
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
from decimal import Decimal
from utils.file_handlers import save_file, delete_file, get_presigned_url
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

        # Construir query con filtros
        query = Pago.query
        if venta_id := request.args.get('venta_id'):
            query = query.filter_by(venta_id=venta_id)
        if metodo := request.args.get('metodo_pago'):
            query = query.filter_by(metodo_pago=metodo)
        if usuario_id := request.args.get('usuario_id'):
            query = query.filter_by(usuario_id=usuario_id)

        # Ordenar (opcional, pero bueno para consistencia)
        query = query.order_by(Pago.fecha.desc())

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