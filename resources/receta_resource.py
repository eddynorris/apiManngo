from flask_restful import Resource, reqparse
from flask import request
from models import Receta, ComponenteReceta, PresentacionProducto
from schemas import receta_schema, recetas_schema
from extensions import db
from common import handle_db_errors, rol_requerido, MAX_ITEMS_PER_PAGE
from flask_jwt_extended import jwt_required
import logging

logger = logging.getLogger(__name__)

class RecetaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, id=None):
        """Obtiene una o todas las recetas."""
        if id:
            receta = Receta.query.get_or_404(id)
            return receta_schema.dump(receta), 200
        
        # Permitir filtrar por la presentación final
        presentacion_id = request.args.get('presentacion_id')
        if presentacion_id:
            receta = Receta.query.filter_by(presentacion_id=presentacion_id).first()
            if not receta:
                return {"error": "No se encontró una receta para la presentación especificada"}, 404
            return receta_schema.dump(receta), 200

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        
        query = Receta.query.order_by(Receta.id.asc())
        
        recetas_paginadas = query.paginate(page=page, per_page=per_page, error_out=False)

        return {
            "data": recetas_schema.dump(recetas_paginadas.items),
            "pagination": {
                "total": recetas_paginadas.total,
                "page": recetas_paginadas.page,
                "per_page": recetas_paginadas.per_page,
                "pages": recetas_paginadas.pages
            }
        }, 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def post(self):
        """
        Crea una nueva receta con sus componentes.
        El JSON debe incluir la presentación final y una lista de componentes.
        """
        data = request.get_json()
        if not data or 'presentacion_id' not in data or 'componentes' not in data:
            return {"error": "Se requiere presentacion_id y una lista de componentes"}, 400

        # Verificar que no exista ya una receta para esta presentación
        if Receta.query.filter_by(presentacion_id=data['presentacion_id']).first():
            return {"error": "Ya existe una receta para esta presentación de producto"}, 409

        nueva_receta = Receta(
            presentacion_id=data['presentacion_id'],
            nombre=data.get('nombre', 'Receta para ...'), # Se puede mejorar para tomar el nombre de la presentación
            descripcion=data.get('descripcion', '')
        )
        db.session.add(nueva_receta)

        # Crear componentes
        for comp_data in data['componentes']:
            componente = ComponenteReceta(
                receta=nueva_receta,
                componente_presentacion_id=comp_data['componente_presentacion_id'],
                cantidad_necesaria=comp_data['cantidad_necesaria'],
                tipo_consumo=comp_data['tipo_consumo']
            )
            db.session.add(componente)
        
        db.session.commit()
        logger.info(f"Receta creada para la presentación ID: {nueva_receta.presentacion_id}")
        return receta_schema.dump(nueva_receta), 201

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, id):
        """
        Actualiza una receta existente. Reemplaza los componentes antiguos con los nuevos.
        """
        receta = Receta.query.get_or_404(id)
        data = request.get_json()

        # Actualizar campos de la receta principal
        receta.nombre = data.get('nombre', receta.nombre)
        receta.descripcion = data.get('descripcion', receta.descripcion)
        # No se debería cambiar la presentacion_id, ya que define la receta.

        # Eliminar componentes antiguos
        ComponenteReceta.query.filter_by(receta_id=id).delete()

        # Agregar nuevos componentes
        if 'componentes' in data:
            for comp_data in data['componentes']:
                componente = ComponenteReceta(
                    receta_id=id,
                    componente_presentacion_id=comp_data['componente_presentacion_id'],
                    cantidad_necesaria=comp_data['cantidad_necesaria'],
                    tipo_consumo=comp_data['tipo_consumo']
                )
                db.session.add(componente)

        db.session.commit()
        logger.info(f"Receta ID: {id} actualizada.")
        return receta_schema.dump(receta), 200

    @jwt_required()
    @rol_requerido('admin')
    @handle_db_errors
    def delete(self, id):
        """
        Elimina una receta y todos sus componentes (por cascade).
        """
        receta = Receta.query.get_or_404(id)
        db.session.delete(receta)
        db.session.commit()
        logger.warning(f"Receta ID: {id} eliminada.")
        return {"mensaje": "Receta eliminada exitosamente"}, 200
