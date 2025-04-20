# ARCHIVO: gasto_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Gasto, Almacen
from schemas import gasto_schema, gastos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE

class GastoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, gasto_id=None):

        if gasto_id:
            return gasto_schema.dump(Gasto.query.get_or_404(gasto_id)), 200
        
        # Construir query con filtros
        query = Gasto.query
        if categoria := request.args.get('categoria'):
            query = query.filter_by(categoria=categoria)
        if fecha := request.args.get('fecha'):
            query = query.filter_by(fecha=fecha)
        if usuario_id := request.args.get('usuario_id'):
            query = query.filter_by(usuario_id=usuario_id)

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        gastos = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": gastos_schema.dump(gastos.items),
            "pagination": {
                "total": gastos.total,
                "page": gastos.page,
                "per_page": gastos.per_page,
                "pages": gastos.pages
            }
        }, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra nuevo gasto con validación de categoría"""
        data = gasto_schema.load(request.get_json())
        Almacen.query.get_or_404(data.almacen_id)
        data.usuario_id = get_jwt().get('sub')  # Asignar usuario actual
        
        db.session.add(data)
        db.session.commit()
        return gasto_schema.dump(data), 201

    @jwt_required()
    @handle_db_errors
    def put(self, gasto_id):
        """Actualiza gasto existente con validación de datos"""
        gasto = Gasto.query.get_or_404(gasto_id)
        data = gasto_schema.load(request.get_json(), partial=True)
        
        updated_gasto = gasto_schema.load(
            request.get_json(),
            instance=gasto,
            partial=True
        )
        db.session.commit()

        return gasto_schema.dump(updated_gasto), 200

    @jwt_required()
    @handle_db_errors
    def delete(self, gasto_id):
        """Elimina registro de gasto"""
        gasto = Gasto.query.get_or_404(gasto_id)
        db.session.delete(gasto)
        db.session.commit()
        return "Gasto eliminado correctamente", 200