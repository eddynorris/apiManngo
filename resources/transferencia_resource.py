from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Inventario, Almacen, Movimiento
from extensions import db
from common import handle_db_errors
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

class TransferenciaInventarioResource(Resource):
    @jwt_required()
    @handle_db_errors
    def post(self):
        """Transfiere una cantidad de una presentación de un almacén a otro."""
        data = request.get_json()
        required_fields = ['presentacion_id', 'cantidad', 'almacen_origen_id', 'almacen_destino_id']
        for field in required_fields:
            if field not in data:
                return {"error": f"Campo requerido: {field}"}, 400

        try:
            presentacion_id = int(data['presentacion_id'])
            cantidad = Decimal(data['cantidad'])
            almacen_origen_id = int(data['almacen_origen_id'])
            almacen_destino_id = int(data['almacen_destino_id'])
            lote_id = data.get('lote_id')
            if lote_id is not None:
                lote_id = int(lote_id)
            
            if cantidad <= 0:
                return {"error": "La cantidad a transferir debe ser mayor a cero."}, 400
            if almacen_origen_id == almacen_destino_id:
                return {"error": "El almacén de origen y destino no pueden ser el mismo."}, 400
        except (ValueError, TypeError, InvalidOperation):
            return {"error": "Formato de datos inválido. IDs y cantidad deben ser números."}, 400

        almacen_origen = Almacen.query.get_or_404(almacen_origen_id)
        almacen_destino = Almacen.query.get_or_404(almacen_destino_id)
        
        inv_origen = Inventario.query.filter_by(
            presentacion_id=presentacion_id,
            almacen_id=almacen_origen_id,
            lote_id=lote_id
        ).first()

        if not inv_origen or inv_origen.cantidad < cantidad:
            stock_disponible = inv_origen.cantidad if inv_origen else 0
            return {"error": f"Stock insuficiente en el almacén de origen. Cantidad requerida: {cantidad}, disponible: {stock_disponible}"}, 400

        try:
            inv_origen.cantidad -= cantidad

            inv_destino = Inventario.query.filter_by(
                presentacion_id=presentacion_id,
                almacen_id=almacen_destino_id,
                lote_id=lote_id
            ).first()

            if inv_destino:
                inv_destino.cantidad += cantidad
            else:
                inv_destino = Inventario(
                    presentacion_id=presentacion_id,
                    almacen_id=almacen_destino_id,
                    lote_id=lote_id,
                    cantidad=cantidad,
                    stock_minimo=inv_origen.stock_minimo
                )
                db.session.add(inv_destino)

            claims = get_jwt()
            usuario_id = claims.get('sub')
            motivo_salida = f"Transferencia a {almacen_destino.nombre}"
            motivo_entrada = f"Transferencia desde {almacen_origen.nombre}"

            mov_salida = Movimiento(
                tipo='salida',
                presentacion_id=presentacion_id,
                lote_id=lote_id,
                cantidad=cantidad,
                usuario_id=usuario_id,
                motivo=motivo_salida,
                tipo_operacion='transferencia',
                fecha=datetime.now(timezone.utc)
            )
            mov_entrada = Movimiento(
                tipo='entrada',
                presentacion_id=presentacion_id,
                lote_id=lote_id,
                cantidad=cantidad,
                usuario_id=usuario_id,
                motivo=motivo_entrada,
                tipo_operacion='transferencia',
                fecha=datetime.now(timezone.utc)
            )
            db.session.add_all([mov_salida, mov_entrada])
            
            db.session.commit()

            return {"mensaje": "Transferencia realizada con éxito"}, 200

        except Exception as e:
            db.session.rollback()
            # Assuming 'logger' is defined elsewhere, e.g., in a common utility file or app.py
            # If not, this line would cause an error. For now, we'll assume it exists.
            # logger.error(f"Error durante la transferencia de inventario: {str(e)}") 
            return {"error": "Error interno durante la transferencia."}, 500
