# ARCHIVO: gasto_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Gasto, Almacen, Users
from schemas import gasto_schema, gastos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
from sqlalchemy import asc, desc

class GastoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, gasto_id=None):

        if gasto_id:
            return gasto_schema.dump(Gasto.query.get_or_404(gasto_id)), 200
        
        # --- Lógica de Ordenación Dinámica ---
        sort_by = request.args.get('sort_by', 'fecha') # Default a fecha
        sort_order = request.args.get('sort_order', 'desc').lower() # Default a desc

        sortable_columns = {
            'fecha': Gasto.fecha,
            'monto': Gasto.monto,
            'categoria': Gasto.categoria,
            'descripcion': Gasto.descripcion,
            'almacen_nombre': Almacen.nombre,     # Relacionado
            'usuario_username': Users.username    # Relacionado
        }

        column_to_sort = sortable_columns.get(sort_by, Gasto.fecha)
        order_func = desc if sort_order == 'desc' else asc
        # --- Fin Lógica de Ordenación ---

        query = Gasto.query

        # --- Aplicar Joins si es necesario para ordenar ---
        if sort_by == 'almacen_nombre':
            query = query.outerjoin(Almacen, Gasto.almacen_id == Almacen.id) # Usar outerjoin por si almacen_id es NULL
        elif sort_by == 'usuario_username':
            query = query.outerjoin(Users, Gasto.usuario_id == Users.id) # Usar outerjoin por si usuario_id es NULL
        # ------------------------------------------------

        # Construir query con filtros
        if categoria := request.args.get('categoria'):
            query = query.filter_by(categoria=categoria)
        if fecha := request.args.get('fecha'):
            query = query.filter_by(fecha=fecha)
        if usuario_id := request.args.get('usuario_id'):
            query = query.filter_by(usuario_id=usuario_id)

        # --- APLICAR ORDENACIÓN ---
        query = query.order_by(order_func(column_to_sort))
        # -------------------------

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