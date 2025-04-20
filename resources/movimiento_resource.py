# ARCHIVO: movimiento_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Movimiento, Inventario, PresentacionProducto, Lote, Almacen
from schemas import movimiento_schema, movimientos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
import logging # Importar el módulo estándar

# Configurar logging para este módulo
logger = logging.getLogger(__name__)

class MovimientoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, movimiento_id=None):
        """
        Obtiene movimientos de inventario
        - Con ID: Detalle completo con relaciones
        - Sin ID: Lista paginada con filtros (tipo, producto_id)
        """
        if movimiento_id:
            return movimiento_schema.dump(Movimiento.query.get_or_404(movimiento_id)), 200
        
        # Construir query con filtros
        query = Movimiento.query
        if tipo := request.args.get('tipo'):
            query = query.filter_by(tipo=tipo)
        if producto_id := request.args.get('producto_id'):
            query = query.filter_by(producto_id=producto_id)

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        movimientos = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": movimientos_schema.dump(movimientos.items),
            "pagination": {
                "total": movimientos.total,
                "page": movimientos.page,
                "per_page": movimientos.per_page,
                "pages": movimientos.pages
            }
        }, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra movimiento y actualiza inventario correspondiente"""
        data = movimiento_schema.load(request.get_json())
        
        # --- Validación Adicional --- 
        # Validar que la presentación existe
        PresentacionProducto.query.get_or_404(data.presentacion_id)
        # Validar que el lote existe si se proporciona
        if data.lote_id:
            Lote.query.get_or_404(data.lote_id)
        # Determinar almacen_id (podría venir en data o inferirse del lote/presentación? Asumimos que se necesita para buscar Inventario)
        # Necesitamos saber el almacen_id para buscar el inventario
        # Si no viene en data, ¿cómo se obtiene? ¿Quizás de la presentación o lote? 
        # Por ahora, asumimos que el inventario se buscará por presentacion_id y lote_id si es necesario, o que almacen_id está implícito.
        # Si Movimiento requiere almacen_id, añadir: Almacen.query.get_or_404(data.almacen_id)
        # --------------------------
        
        inventario = Inventario.query.filter_by(
            # Se necesita buscar inventario por presentacion_id y almacen_id.
            # ¿Cómo obtener almacen_id aquí de forma segura?
            # Asumiendo que se puede obtener de alguna forma o no es necesario para la lógica directa aquí
            presentacion_id=data.presentacion_id 
            # , almacen_id=obtenido_almacen_id 
        ).first()
        
        # Validar stock para movimientos de salida
        if data.tipo == 'salida' and (not inventario or inventario.cantidad < data.cantidad):
            stock_disp = inventario.cantidad if inventario else 0
            return {"error": "Stock insuficiente para este movimiento", "disponible": stock_disp}, 400
        
        # Asignar usuario actual
        data.usuario_id = get_jwt().get('sub')
        nuevo_movimiento = Movimiento(**data.to_dict()) # Asumiendo que data es un objeto con .to_dict() o similar tras load
        db.session.add(nuevo_movimiento)
        
        # Actualizar inventario
        if inventario: # Solo actualizar si el inventario existe
            if data.tipo == 'entrada':
                inventario.cantidad += data.cantidad
            else: # tipo == 'salida'
                inventario.cantidad -= data.cantidad
        else:
            # Si es una entrada y no hay inventario, ¿debería crearse? 
            # La lógica actual requiere que el inventario exista para salidas
            # y no hace nada con él para entradas si no existe.
            if data.tipo == 'entrada':
                 logger.warning(f"Movimiento de entrada para inventario inexistente: Presentación {data.presentacion_id}")
                 # Considerar crear inventario aquí si es la lógica deseada
        
        db.session.commit()
        return movimiento_schema.dump(nuevo_movimiento), 201

    @jwt_required()
    @handle_db_errors
    def delete(self, movimiento_id):
        """Elimina movimiento y revierte el inventario"""
        movimiento = Movimiento.query.get_or_404(movimiento_id)
        
        # --- Validación Adicional --- 
        PresentacionProducto.query.get_or_404(movimiento.presentacion_id) # Verificar consistencia
        # --------------------------
        
        # Necesitamos el almacen_id para buscar el inventario.
        # ¿De dónde lo obtenemos? ¿Del movimiento? ¿Del lote? ¿Presentación?
        # Asumiendo que podemos obtenerlo:
        # almacen_id_obtenido = ... 
        inventario = Inventario.query.filter_by(
            presentacion_id=movimiento.presentacion_id
            # , almacen_id=almacen_id_obtenido 
        ).first()
        
        # Revertir movimiento
        if inventario: # Solo revertir si el inventario existe
            if movimiento.tipo == 'entrada':
                # Asegurarse de no dejar stock negativo al revertir entrada
                if inventario.cantidad >= movimiento.cantidad:
                    inventario.cantidad -= movimiento.cantidad
                else:
                    logger.warning(f"Reversión de entrada resultaría en stock negativo. Estableciendo a 0. Movimiento ID: {movimiento_id}")
                    inventario.cantidad = 0 
            else: # tipo == 'salida'
                inventario.cantidad += movimiento.cantidad
        else:
            logger.warning(f"Inventario no encontrado al intentar revertir movimiento {movimiento_id}")
        
        db.session.delete(movimiento)
        db.session.commit()
        return "", 204