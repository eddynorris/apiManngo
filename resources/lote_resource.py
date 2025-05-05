from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Lote, Proveedor, Producto, Merma
from schemas import lote_schema, lotes_schema, merma_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido
from sqlalchemy import asc, desc

class LoteResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, lote_id=None):
        if lote_id:
            lote = Lote.query.get_or_404(lote_id)
            return lote_schema.dump(lote), 200
        
        # --- Lógica de Ordenación Dinámica ---
        sort_by = request.args.get('sort_by', 'created_at') # Default a created_at
        sort_order = request.args.get('sort_order', 'desc').lower() # Default a desc

        sortable_columns = {
            'created_at': Lote.created_at,
            'fecha_ingreso': Lote.fecha_ingreso,
            'descripcion': Lote.descripcion,
            'peso_humedo_kg': Lote.peso_humedo_kg,
            'peso_seco_kg': Lote.peso_seco_kg,
            'cantidad_disponible_kg': Lote.cantidad_disponible_kg,
            'producto_nombre': Producto.nombre,  # Relacionado
            'proveedor_nombre': Proveedor.nombre # Relacionado
        }

        column_to_sort = sortable_columns.get(sort_by, Lote.created_at)
        order_func = desc if sort_order == 'desc' else asc
        # --- Fin Lógica de Ordenación ---

        query = Lote.query

        # --- Aplicar Joins si es necesario para ordenar ---
        if sort_by == 'producto_nombre':
            query = query.join(Producto, Lote.producto_id == Producto.id)
        elif sort_by == 'proveedor_nombre':
             # Outerjoin porque proveedor_id puede ser NULL
            query = query.outerjoin(Proveedor, Lote.proveedor_id == Proveedor.id)
        # ------------------------------------------------

        # TODO: Añadir filtros si son necesarios para la vista de lista de lotes

        # --- APLICAR ORDENACIÓN ---
        query = query.order_by(order_func(column_to_sort))
        # -------------------------

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

        data = lote_schema.load(request.get_json())

        # Validar proveedor y producto
        Proveedor.query.get_or_404(data.proveedor_id)
        Producto.query.get_or_404(data.producto_id)
        
        db.session.add(data)
        db.session.commit()
        return lote_schema.dump(data), 201

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
