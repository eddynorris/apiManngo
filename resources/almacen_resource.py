# ARCHIVO: almacen_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from flask import request
from models import Almacen
from schemas import almacen_schema, almacenes_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido

class AlmacenResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, almacen_id=None):
        """
        Obtiene almacén(es)
        - Si se provee ID: Retorna un solo almacén
        - Sin ID: Retorna lista paginada con todos los almacenes
        - Filtros disponibles: ciudad, nombre (vía query params)
        """
        if almacen_id:
            return almacen_schema.dump(Almacen.query.get_or_404(almacen_id)), 200
        
        # Construir query con filtros
        query = Almacen.query
        if nombre := request.args.get('nombre'):
            query = query.filter(Almacen.nombre.ilike(f'%{nombre}%'))
        if ciudad := request.args.get('ciudad'):
            query = query.filter(Almacen.ciudad.ilike(f'%{ciudad}%'))

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        resultado = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": almacenes_schema.dump(resultado.items),
            "pagination": {
                "total": resultado.total,
                "page": resultado.page,
                "per_page": resultado.per_page,
                "pages": resultado.pages
            }
        }, 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def post(self):
        """Crea un nuevo almacén con datos validados"""
        nuevo_almacen = almacen_schema.load(request.get_json())
        db.session.add(nuevo_almacen)
        db.session.commit()
        return almacen_schema.dump(nuevo_almacen), 201

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, almacen_id):
        """Actualiza un almacén existente mediante PATCH parcial"""
        almacen = Almacen.query.get_or_404(almacen_id)
        almacen_actualizado = almacen_schema.load(
            request.get_json(),
            instance=almacen,
            partial=True
        )
        db.session.commit()
        return almacen_schema.dump(almacen_actualizado), 200

    @jwt_required()
    @rol_requerido('admin')
    @handle_db_errors
    def delete(self, almacen_id):
        """Elimina un almacén solo si no tiene usuarios asociados"""
        almacen = Almacen.query.get_or_404(almacen_id)
        
        if almacen.usuarios:
            return {"error": "No se puede eliminar un almacén con usuarios asignados"}, 400
            
        db.session.delete(almacen)
        db.session.commit()
        return "", 204