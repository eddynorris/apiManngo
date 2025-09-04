from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Lote, Proveedor, Producto, Merma, Inventario, Movimiento, PresentacionProducto
from schemas import lote_schema, lotes_schema, merma_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido
from sqlalchemy import asc, desc
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

class LoteResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, lote_id=None):
        if lote_id:
            lote = Lote.query.get_or_404(lote_id)
            return lote_schema.dump(lote), 200
        
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc').lower()

        sortable_columns = {
            'created_at': Lote.created_at,
            'fecha_ingreso': Lote.fecha_ingreso,
            'descripcion': Lote.descripcion,
            'peso_humedo_kg': Lote.peso_humedo_kg,
            'peso_seco_kg': Lote.peso_seco_kg,
            'cantidad_disponible_kg': Lote.cantidad_disponible_kg,
            'producto_nombre': Producto.nombre,
            'proveedor_nombre': Proveedor.nombre
        }

        column_to_sort = sortable_columns.get(sort_by, Lote.created_at)
        order_func = desc if sort_order == 'desc' else asc

        query = Lote.query

        if sort_by == 'producto_nombre':
            query = query.join(Producto, Lote.producto_id == Producto.id)
        elif sort_by == 'proveedor_nombre':
            query = query.outerjoin(Proveedor, Lote.proveedor_id == Proveedor.id)

        query = query.order_by(order_func(column_to_sort))

        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        lotes = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": lotes_schema.dump(lotes.items),
            "pagination": {
                "total": lotes.total,
                "page": lotes.page,
                "per_page": lotes.per_page,
                "pages": lotes.pages
            }
        }, 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def post(self):
        """
        Crea un nuevo lote de materia prima de proveedor.
        Los lotes representan carbón bruto comprado a proveedores, no productos finales.
        """
        json_data = request.get_json()
        if not json_data:
            return {"error": "No se proporcionaron datos"}, 400

        # 1. Validar campos requeridos
        required_fields = ['producto_id', 'proveedor_id', 'peso_humedo_kg']
        for field in required_fields:
            if field not in json_data:
                return {"error": f"El campo '{field}' es requerido."}, 400
        
        try:
            peso_humedo = Decimal(json_data['peso_humedo_kg'])
            if peso_humedo <= 0:
                return {"error": "El peso húmedo debe ser mayor a cero"}, 400
        except (ValueError, TypeError, InvalidOperation):
            return {"error": "El peso húmedo debe ser un número válido."}, 400

        # 2. Validar existencia de entidades relacionadas
        Producto.query.get_or_404(json_data['producto_id'])
        Proveedor.query.get_or_404(json_data['proveedor_id'])
        
        # 3. Crear el Lote
        nuevo_lote = lote_schema.load(json_data)
        
        # Establecer valores por defecto si no se proporcionan
        if not nuevo_lote.fecha_ingreso:
            nuevo_lote.fecha_ingreso = datetime.now(timezone.utc)
        
        # Si no se proporciona peso seco, usar el húmedo como inicial
        if not nuevo_lote.peso_seco_kg:
            nuevo_lote.peso_seco_kg = peso_humedo
        
        # La cantidad disponible inicialmente es el peso húmedo
        nuevo_lote.cantidad_disponible_kg = peso_humedo
        nuevo_lote.cantidad_inicial_kg = peso_humedo

        # 4. Guardar el lote
        db.session.add(nuevo_lote)
        db.session.commit()

        return lote_schema.dump(nuevo_lote), 201

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, lote_id):
        lote = Lote.query.get_or_404(lote_id)
        updated_lote = lote_schema.load(
            request.get_json(),
            instance=lote,
            partial=True
        )
        db.session.commit()
        return lote_schema.dump(updated_lote), 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def delete(self, lote_id):
        lote = Lote.query.get_or_404(lote_id)
        db.session.delete(lote)
        db.session.commit()
        return "Lote eliminado exitosamente!", 200