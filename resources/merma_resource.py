from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Merma, Lote, Inventario, PresentacionProducto
from schemas import merma_schema, mermas_schema, lote_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido
from decimal import Decimal

class MermaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, merma_id=None):
        if merma_id:
            merma = Merma.query.get_or_404(merma_id)
            return merma_schema.dump(merma), 200
        
        # Filtros: lote_id, convertido_a_briquetas, fecha_registro
        lote_id = request.args.get('lote_id')
        convertido = request.args.get('convertido_a_briquetas', type=bool)
        
        query = Merma.query
        
        if lote_id:
            query = query.filter_by(lote_id=lote_id)
        if convertido is not None:
            query = query.filter_by(convertido_a_briquetas=convertido)
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        mermas = query.paginate(page=page, per_page=per_page)
        
        return {
            "data": mermas_schema.dump(mermas.items),
            "pagination": {
                "total": mermas.total,
                "page": mermas.page,
                "per_page": mermas.per_page,
                "pages": mermas.pages
            }
        }, 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def post(self):
        data = merma_schema.load(request.get_json())
        lote = Lote.query.get_or_404(data.lote_id)
        
        # Validar cantidad
        if Decimal(data.cantidad_kg) > lote.cantidad_disponible_kg:
            return {
                "error": "Merma excede stock disponible",
                "stock_disponible": str(lote.cantidad_disponible_kg)
            }, 400
        
        # Crear merma básica
        nueva_merma = Merma(
            lote_id=lote.id,
            cantidad_kg=data.cantidad_kg,
            usuario_id=get_jwt().get('sub')
        )

        try:
            # Actualizar lote
            lote.cantidad_disponible_kg -= nueva_merma.cantidad_kg
            
            db.session.add(nueva_merma)
            db.session.commit()
            
            return merma_schema.dump(nueva_merma), 201
        
        except Exception as e:
            db.session.rollback()
            return {"error": str(e)}, 500
       

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, merma_id):
        merma = Merma.query.get_or_404(merma_id)
        data = merma_schema.load(request.get_json(), partial=True)
        lote = merma.lote
        
        if data.cantidad_kg:
            nueva_cantidad = Decimal(data.cantidad_kg)
            diferencia = nueva_cantidad - merma.cantidad_kg
            
            # Validar nueva cantidad
            if (lote.cantidad_disponible_kg - diferencia) < 0:
                return {"error": "Nueva cantidad inválida"}, 400
            
            # Actualizar lote
            lote.cantidad_disponible_kg -= diferencia

        ##if data.convertido_a_briquetas: falta agregar

        updated_merma = merma_schema.load(
        request.get_json(),
        instance=merma,
        partial=True
        )
        db.session.commit()
        return merma_schema.dump(updated_merma), 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def delete(self, merma_id):
        merma = Merma.query.get_or_404(merma_id)
        lote = merma.lote
        try:
            # Revertir cantidad en lote
            lote.cantidad_disponible_kg += merma.cantidad_kg
            
            db.session.delete(merma)
            db.session.commit()
            return "Eliminado correctamente", 200        
        except Exception as e:
            db.session.rollback()
            return {"error": str(e)}, 500