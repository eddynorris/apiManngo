from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Venta, VentaDetalle, Inventario, PresentacionProducto
from schemas import venta_schema, ventas_schema, venta_detalle_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin

class VentaDetalleResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id):
        detalles = VentaDetalle.query.filter_by(venta_id=venta_id).all()
        return venta_detalle_schema.dump(detalles), 200

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self, venta_id):
        venta = Venta.query.get_or_404(venta_id)
        data = venta_detalle_schema.load(request.get_json())
        
        # Validar presentación y stock
        presentacion = PresentacionProducto.query.get_or_404(data["presentacion_id"])
        inventario = Inventario.query.filter_by(
            presentacion_id=presentacion.id,
            almacen_id=venta.almacen_id
        ).first()
        
        if not inventario or inventario.cantidad < data["cantidad"]:
            return {
                "error": f"Stock insuficiente para {presentacion.nombre}",
                "stock_disponible": inventario.cantidad if inventario else 0
            }, 400
        
        # Crear detalle
        nuevo_detalle = VentaDetalle(
            venta_id=venta_id,
            presentacion_id=presentacion.id,
            cantidad=data["cantidad"],
            precio_unitario=presentacion.precio_venta
        )
        
        # Actualizar venta y stock
        venta.total += nuevo_detalle.precio_unitario * nuevo_detalle.cantidad
        inventario.cantidad -= nuevo_detalle.cantidad
        
        db.session.add(nuevo_detalle)
        db.session.commit()
        
        return venta_detalle_schema.dump(nuevo_detalle), 201

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, detalle_id):
        detalle = VentaDetalle.query.get_or_404(detalle_id)
        venta = detalle.venta
        
        # Revertir stock
        inventario = Inventario.query.filter_by(
            presentacion_id=detalle.presentacion_id,
            almacen_id=venta.almacen_id
        ).first()
        inventario.cantidad += detalle.cantidad
        
        # Actualizar total de la venta
        venta.total -= detalle.precio_unitario * detalle.cantidad
        venta.actualizar_estado()
        
        db.session.delete(detalle)
        db.session.commit()
        
        return "", 204