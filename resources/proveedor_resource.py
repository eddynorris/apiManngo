# ARCHIVO: proveedor_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required
from flask import request
from models import Proveedor
from schemas import proveedor_schema, proveedores_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido

class ProveedorResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, proveedor_id=None):
        """
        Obtiene proveedor(es)
        - Con ID: Detalle completo con lotes asociados
        - Sin ID: Lista paginada con filtros (nombre, ciudad)
        """
        if proveedor_id:
            return proveedor_schema.dump(Proveedor.query.get_or_404(proveedor_id)), 200
        
        # Construir query con filtros
        query = Proveedor.query
        if nombre := request.args.get('nombre'):
            query = query.filter(Proveedor.nombre.ilike(f'%{nombre}%'))
        if ciudad := request.args.get('ciudad'):
            query = query.filter(Proveedor.direccion.ilike(f'%{ciudad}%'))

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        resultado = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": proveedores_schema.dump(resultado.items),
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
        """Registra nuevo proveedor con validación de datos"""
        nuevo_proveedor = proveedor_schema.load(request.get_json())
        db.session.add(nuevo_proveedor)
        db.session.commit()
        return proveedor_schema.dump(nuevo_proveedor), 201

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, proveedor_id):
        """Actualiza datos de proveedor existente"""
        proveedor = Proveedor.query.get_or_404(proveedor_id)
        proveedor_actualizado = proveedor_schema.load(
            request.get_json(),
            instance=proveedor,
            partial=True
        )
        db.session.commit()
        return proveedor_schema.dump(proveedor_actualizado), 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def delete(self, proveedor_id):
        """Elimina proveedor solo si no tiene lotes asociados"""
        proveedor = Proveedor.query.get_or_404(proveedor_id)
        
        if proveedor.lotes:
            return {"error": "No se puede eliminar proveedor con lotes registrados"}, 400
            
        db.session.delete(proveedor)
        db.session.commit()
        return "", 204