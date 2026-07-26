# ARCHIVO: movimiento_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Movimiento, Inventario, PresentacionProducto, Lote, Almacen
from schemas import movimiento_schema, movimientos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
from datetime import datetime
from sqlalchemy.orm import joinedload
import logging

logger = logging.getLogger(__name__)

class MovimientoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, movimiento_id=None):
        """
        Obtiene movimientos de inventario
        - Con ID: Detalle completo con relaciones
        - Sin ID: Lista paginada con filtros (tipo, producto_id, fecha_inicio, fecha_fin, lote_id, presentacion_id)
        """
        if movimiento_id:
            
            movimiento = Movimiento.query.options(
                joinedload(Movimiento.presentacion),
                joinedload(Movimiento.lote),
                joinedload(Movimiento.usuario),
                joinedload(Movimiento.lote_origen)
            ).get_or_404(movimiento_id)
            return movimiento_schema.dump(movimiento), 200
        
        query = Movimiento.query.options(
            joinedload(Movimiento.presentacion),
            joinedload(Movimiento.lote),
            joinedload(Movimiento.usuario),
            joinedload(Movimiento.lote_origen)
        )
        
        if tipo := request.args.get('tipo'):
            query = query.filter_by(tipo=tipo)
        if lote_id := request.args.get('lote_id'):
            try:
                query = query.filter_by(lote_id=int(lote_id))
            except ValueError:
                return {"error": "ID de lote inválido"}, 400
        if presentacion_id := request.args.get('presentacion_id'):
            try:
                query = query.filter_by(presentacion_id=int(presentacion_id))
            except ValueError:
                return {"error": "ID de presentación inválido"}, 400
        # Filtro por rango de fechas
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        if fecha_inicio:
            try:
                fecha_inicio_dt = datetime.strptime(fecha_inicio, "%Y-%m-%d")
                query = query.filter(Movimiento.fecha >= fecha_inicio_dt)
            except ValueError:
                return {"error": "Formato de fecha_inicio inválido. Use YYYY-MM-DD."}, 400
        if fecha_fin:
            try:
                fecha_fin_dt = datetime.strptime(fecha_fin, "%Y-%m-%d")
                # Para incluir todo el día, sumamos un día y filtramos menor a esa fecha
                from datetime import timedelta
                fecha_fin_dt = fecha_fin_dt + timedelta(days=1)
                query = query.filter(Movimiento.fecha < fecha_fin_dt)
            except ValueError:
                return {"error": "Formato de fecha_fin inválido. Use YYYY-MM-DD."}, 400

        # Ordenar por fecha descendente (historial lógico óptimo)
        query = query.order_by(Movimiento.fecha.desc(), Movimiento.id.desc())

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
        json_data = request.get_json()
        
        # Validar que se proporcione almacen_id
        almacen_id = json_data.get('almacen_id')
        if not almacen_id:
            return {"error": "Se requiere almacen_id para registrar un movimiento"}, 400
        
        # Validar permisos de almacén
        claims = get_jwt()
        current_user_id = claims.get('sub')
        rol = claims.get('rol')
        user_almacen_id = claims.get('almacen_id')
        
        # Solo admin puede mover inventario de cualquier almacén
        if rol != 'admin' and user_almacen_id != almacen_id:
            return {"error": "No tienes permiso para mover inventario de este almacén"}, 403
        
        # Validar que el almacén existe
        Almacen.query.get_or_404(almacen_id)
        
        data = movimiento_schema.load(json_data)
        
        # --- Validación Adicional --- 
        # Validar que la presentación existe
        PresentacionProducto.query.get_or_404(data.presentacion_id)
        # Validar que el lote existe si se proporciona
        if data.lote_id:
            Lote.query.get_or_404(data.lote_id)
        
        # Buscar inventario usando presentacion_id, almacen_id y lote_id
        inventario = Inventario.query.filter_by(
            presentacion_id=data.presentacion_id,
            almacen_id=almacen_id,
            lote_id=data.lote_id
        ).first()
        
        # Validar stock para movimientos de salida
        if data.tipo == 'salida' and (not inventario or inventario.cantidad < data.cantidad):
            stock_disp = inventario.cantidad if inventario else 0
            return {"error": "Stock insuficiente para este movimiento", "disponible": stock_disp}, 400
        
        # Asignar usuario actual
        data.usuario_id = current_user_id
        nuevo_movimiento = Movimiento(**data.to_dict())
        db.session.add(nuevo_movimiento)
        
        # Actualizar inventario
        if inventario:
            if data.tipo == 'entrada':
                inventario.cantidad += data.cantidad
            else: # tipo == 'salida'
                inventario.cantidad -= data.cantidad
        else:
            if data.tipo == 'entrada':
                logger.warning(f"Movimiento de entrada para inventario inexistente: Presentación {data.presentacion_id}, Almacén {almacen_id}")
        
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