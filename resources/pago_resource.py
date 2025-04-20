# ARCHIVO: pago_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Pago, Venta
from schemas import pago_schema, pagos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
from decimal import Decimal
from utils.file_handlers import save_file, delete_file
from werkzeug.datastructures import FileStorage
from flask import request, current_app

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
            return pago_schema.dump(Pago.query.get_or_404(pago_id)), 200
        
        # Construir query con filtros
        query = Pago.query
        if venta_id := request.args.get('venta_id'):
            query = query.filter_by(venta_id=venta_id)
        if metodo := request.args.get('metodo_pago'):
            query = query.filter_by(metodo_pago=metodo)
        if usuario_id := request.args.get('usuario_id'):
            query = query.filter_by(usuario_id=usuario_id)
        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pagos = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": pagos_schema.dump(pagos.items),
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
            data = pago_schema.load(request.get_json())

            venta = Venta.query.get_or_404(data.venta_id)
            saldo_pendiente_venta = venta.total - sum(pago.monto for pago in venta.pagos)
            if Decimal(data.monto) > saldo_pendiente_venta:
                return {"error": "Monto excede el saldo pendiente"}, 400
            
            nuevo_pago = Pago(
                venta_id=venta.id,
                monto=data.monto,
                metodo_pago=data.metodo_pago,
                referencia=data.referencia,
                fecha = data.fecha,
                usuario_id=get_jwt().get('sub')
            )

            db.session.add(nuevo_pago)
            venta.actualizar_estado(nuevo_pago)
            db.session.commit()
            
            return pago_schema.dump(nuevo_pago), 201
        
        # Procesar formulario multipart con archivos
        elif 'multipart/form-data' in request.content_type:
            # Obtener datos del formulario
            venta_id = request.form.get('venta_id')
            monto = request.form.get('monto')
            metodo_pago = request.form.get('metodo_pago')
            referencia = request.form.get('referencia')
            fecha = request.form.get('fecha')
            
            
            # Validaciones básicas
            if not all([venta_id, monto, metodo_pago]):
                return {"error": "Faltan campos requeridos"}, 400
            
            venta = Venta.query.get_or_404(venta_id)
            saldo_pendiente_venta = venta.total - sum(pago.monto for pago in venta.pagos)
            print(saldo_pendiente_venta)
            if Decimal(monto) > saldo_pendiente_venta:
                return {"error": "Monto excede el saldo pendiente"}, 400
            
            # Procesar comprobante si existe
            url_comprobante = None
            if 'comprobante' in request.files:
                file = request.files['comprobante']
                url_comprobante = save_file(file, 'comprobantes')
            
            # Crear pago
            nuevo_pago = Pago(
                venta_id=venta_id,
                monto=Decimal(monto),
                metodo_pago=metodo_pago,
                referencia=referencia,
                fecha= fecha,
                usuario_id=get_jwt().get('sub'),
                url_comprobante=url_comprobante
            )
            
            db.session.add(nuevo_pago)
            venta.actualizar_estado(nuevo_pago)
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
            data = pago_schema.load(request.get_json(), partial=True)
            
            if data.monto:
                nuevo_monto = Decimal(data.monto)
                saldo_actual = venta.total - sum(p.monto for p in venta.pagos if p.id != pago_id)
                
                if nuevo_monto > saldo_actual:
                    return {"error": "Nuevo monto excede saldo pendiente"}, 400
            
            updated_pago = pago_schema.load(
                request.get_json(),
                instance=pago,
                partial=True
            )
            
            ajuste_pago = None
            if hasattr(data, 'monto') and data.monto is not None:
                # Crear un objeto temporal para representar el cambio en el monto
                class AjustePago:
                    def __init__(self, monto):
                        self.monto = monto
                # El ajuste es la diferencia entre el nuevo monto y el original
                ajuste_pago = AjustePago(Decimal(data.monto) - monto_original)
            
            venta.actualizar_estado(ajuste_pago)
            db.session.commit()
            
            return pago_schema.dump(updated_pago), 200
        
        # Actualización con formulario multipart
        elif 'multipart/form-data' in request.content_type:
            ajuste_pago = None
            # Actualizar monto si se proporciona
            if 'monto' in request.form:
                nuevo_monto = Decimal(request.form.get('monto'))
                saldo_actual = venta.total - sum(p.monto for p in venta.pagos if p.id != pago_id)
                
                if nuevo_monto > saldo_actual:
                    return {"error": "Nuevo monto excede saldo pendiente"}, 400
                
                            # Crear ajuste temporal
                class AjustePago:
                    def __init__(self, monto):
                        self.monto = monto
                
                # El ajuste es la diferencia entre el nuevo monto y el original
                ajuste_pago = AjustePago(nuevo_monto - monto_original)
                pago.monto = nuevo_monto
            
            # Actualizar otros campos
            if 'metodo_pago' in request.form:
                pago.metodo_pago = request.form.get('metodo_pago')
            if 'referencia' in request.form:
                pago.referencia = request.form.get('referencia')
            
            # Procesar comprobante si existe
            if 'comprobante' in request.files:
                file = request.files['comprobante']
                # Eliminar comprobante anterior si existe
                if pago.url_comprobante:
                    delete_file(pago.url_comprobante)
                # Guardar nuevo comprobante
                pago.url_comprobante = save_file(file, 'comprobantes')
            
            # Si se especifica eliminar el comprobante
            if request.form.get('eliminar_comprobante') == 'true' and pago.url_comprobante:
                delete_file(pago.url_comprobante)
                pago.url_comprobante = None
            
            venta.actualizar_estado(ajuste_pago)
            db.session.commit()
            
            return pago_schema.dump(pago), 200
        
        return {"error": "Tipo de contenido no soportado"}, 415

    @jwt_required()
    @handle_db_errors
    def delete(self, pago_id):
        """Elimina pago y su comprobante asociado"""
        pago = Pago.query.get_or_404(pago_id)
        venta = pago.venta
        monto_eliminado = -pago.monto

        # Eliminar comprobante si existe
        if pago.url_comprobante:
            delete_file(pago.url_comprobante)
        
        # Crear un objeto temporal para representar el pago que se eliminará
        class PagoEliminado:
            def __init__(self, monto):
                self.monto = monto
        
        # El monto es negativo porque estamos eliminando un pago
        pago_eliminado = PagoEliminado(monto_eliminado)

        db.session.delete(pago)
        venta.actualizar_estado(pago_eliminado)
        db.session.commit()
        
        return "Pago eliminado", 200