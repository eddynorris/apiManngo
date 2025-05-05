from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Lote, Proveedor, Producto, Merma
from schemas import lote_schema, lotes_schema, merma_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido

class LoteResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, lote_id=None):
        if lote_id:
            lote = Lote.query.get_or_404(lote_id)
            return lote_schema.dump(lote), 200
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        lotes = Lote.query.order_by(Lote.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        
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
