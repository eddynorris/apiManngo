from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Venta, VentaDetalle, Inventario, Cliente, PresentacionProducto, Almacen, Movimiento
from schemas import venta_schema, ventas_schema, venta_detalle_schema, clientes_schema, almacenes_schema, presentacion_schema, inventario_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin
from utils.file_handlers import get_presigned_url
from datetime import datetime, timezone
from decimal import Decimal
import logging # Añadir import para logging

logger = logging.getLogger(__name__) # Configurar logger

class VentaResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, venta_id=None):

        current_user_id = get_jwt().get('sub')
        user_rol = get_jwt().get('rol')
        is_admin = user_rol == 'admin'  # Ajusta esto según tu estructura de roles

        if venta_id:
            venta = Venta.query.get_or_404(venta_id)
            # Si no es admin, verificar que solo pueda ver sus propias ventas
            if not is_admin and str(venta.vendedor_id) != current_user_id:
                return {"error": "No tienes permiso para ver esta venta"}, 403
            
            # Serializar la venta
            result = venta_schema.dump(venta)
            
            # --- GENERAR URLs PRE-FIRMADAS PARA DETALLES ---
            if 'detalles' in result and result['detalles']:
                for detalle in result['detalles']:
                    # Verificar estructura anidada
                    if 'presentacion' in detalle and detalle['presentacion'] and 'url_foto' in detalle['presentacion']:
                        s3_key = detalle['presentacion']['url_foto']
                        if s3_key:
                            # Reemplazar clave S3 con URL pre-firmada
                            detalle['presentacion']['url_foto'] = get_presigned_url(s3_key)
                        # else: url_foto ya es None o vacío, no hacer nada
            # ---------------------------------------------
            
            return result, 200
        
        # Filtros: cliente_id, almacen_id, vendedor_id, fecha_inicio, fecha_fin
        filters = {
            "cliente_id": request.args.get('cliente_id'),
            "almacen_id": request.args.get('almacen_id'),
            "vendedor_id": request.args.get('vendedor_id'),  # Nuevo filtro por vendedor
            "estado_pago": request.args.get('estado_pago'),
            "fecha_inicio": request.args.get('fecha_inicio'),
            "fecha_fin": request.args.get('fecha_fin')
        }
        
        query = Venta.query

        # Si no es admin y se está filtrando por estado_pago, mostrar solo sus ventas
        if not is_admin and filters["estado_pago"]:
            query = query.filter_by(vendedor_id=current_user_id)
        # Si explícitamente pide filtrar por vendedor_id, respetamos ese filtro
        elif filters["vendedor_id"]:
            query = query.filter_by(vendedor_id=filters["vendedor_id"])
        
        # Aplicar otros filtros
        if filters["cliente_id"]:
            query = query.filter_by(cliente_id=filters["cliente_id"])
        if filters["almacen_id"]:
            query = query.filter_by(almacen_id=filters["almacen_id"])
        
        if filters["estado_pago"]:
            statuses = [status.strip() for status in filters["estado_pago"].split(',') if status.strip()]
            if statuses: # Solo aplicar si hay estados válidos
                query = query.filter(Venta.estado_pago.in_(statuses))

        if filters["fecha_inicio"] and filters["fecha_fin"]:
            try:
                # Asegurando formato ISO y manejar zonas horarias
                fecha_inicio = datetime.fromisoformat(filters["fecha_inicio"]).replace(tzinfo=timezone.utc)
                fecha_fin = datetime.fromisoformat(filters["fecha_fin"]).replace(tzinfo=timezone.utc)
                query = query.filter(Venta.fecha.between(fecha_inicio, fecha_fin))
            except ValueError as e:
                # Manejar error de formato inválido
                return {"error": "Formato de fecha inválido. Usa ISO 8601 (ej: '2025-03-05T00:00:00')"}, 400
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        ventas = query.paginate(page=page, per_page=per_page)
        
        return {
            "data": ventas_schema.dump(ventas.items),
            "pagination": {
                "total": ventas.total,
                "page": ventas.page,
                "per_page": ventas.per_page,
                "pages": ventas.pages
            }
        }, 200


    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self):
        data = venta_schema.load(request.get_json())

        cliente = Cliente.query.get_or_404(data.cliente_id)
        almacen = Almacen.query.get_or_404(data.almacen_id)
        
        claims = get_jwt()
        data.vendedor_id = claims.get('sub')

        total = Decimal('0')
        inventarios_a_actualizar = {}
        movimientos = []  # Lista para almacenar los movimientos
        
        # --- Optimización: Obtener todas las presentaciones e inventarios necesarios --- 
        presentacion_ids = [d.presentacion_id for d in data.detalles]
        if not presentacion_ids:
            return {"error": "La venta debe tener al menos un detalle"}, 400

        presentaciones = PresentacionProducto.query.filter(PresentacionProducto.id.in_(presentacion_ids)).all()
        presentaciones_dict = {p.id: p for p in presentaciones}

        inventarios = Inventario.query.filter(
            Inventario.presentacion_id.in_(presentacion_ids),
            Inventario.almacen_id == data.almacen_id
        ).all()
        inventarios_dict = {i.presentacion_id: i for i in inventarios}
        # -----------------------------------------------------------------------------

        for detalle in data.detalles:
            # Usar datos pre-cargados
            presentacion = presentaciones_dict.get(detalle.presentacion_id)
            if not presentacion:
                # Esto no debería ocurrir si get_or_404 funcionaba, pero es una doble verificación
                return {"error": f"Presentación con ID {detalle.presentacion_id} no encontrada"}, 404 

            # Usar datos pre-cargados
            inventario = inventarios_dict.get(detalle.presentacion_id)

            if not inventario or inventario.cantidad < detalle.cantidad:
                stock_disp = inventario.cantidad if inventario else 0
                return {"error": f"Stock insuficiente para {presentacion.nombre} (Disponible: {stock_disp})"}, 400
            if not detalle.precio_unitario:
                detalle.precio_unitario = presentacion.precio_venta

            # Registrar datos para el movimiento
            movimientos.append({
                "presentacion_id": presentacion.id,
                "lote_id": inventario.lote_id,  # Obtenemos el lote del inventario
                "cantidad": detalle.cantidad
            })

            total += detalle.cantidad * detalle.precio_unitario
            inventarios_a_actualizar[presentacion.id] = (inventario, detalle.cantidad)

        nueva_venta = Venta(
            cliente_id=data.cliente_id,
            almacen_id=data.almacen_id,
            vendedor_id=data.vendedor_id,
            total=total,
            tipo_pago=data.tipo_pago,
            consumo_diario_kg=data.consumo_diario_kg,
            detalles=data.detalles
        )

        try:
            db.session.add(nueva_venta)
            db.session.flush()  # Generamos el ID de la venta

            # Crear movimientos después de obtener el ID de la venta
            for movimiento_data in movimientos:
                movimiento = Movimiento(
                    tipo='salida',
                    presentacion_id=movimiento_data["presentacion_id"],
                    lote_id=movimiento_data["lote_id"],
                    cantidad=movimiento_data["cantidad"],
                    usuario_id=claims['sub'],
                    motivo=f"Venta ID: {nueva_venta.id} - Cliente: {cliente.nombre}"
                )
                db.session.add(movimiento)

            # Actualizar inventarios
            for inventario, cantidad in inventarios_a_actualizar.values():
                inventario.cantidad -= cantidad

            # Actualizar proyección del cliente
            if nueva_venta.consumo_diario_kg:
                if Decimal(nueva_venta.consumo_diario_kg) <= 0:
                    raise ValueError("El consumo diario debe ser mayor a 0")

                cliente.ultima_fecha_compra = datetime.utcnow()
                cliente.frecuencia_compra_dias = (total / Decimal(nueva_venta.consumo_diario_kg)).quantize(Decimal('1.00'))

            db.session.commit()
            return venta_schema.dump(nueva_venta), 201

        except Exception as e:
            db.session.rollback()
            return {"error": str(e)}, 500

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def put(self, venta_id):
        venta = Venta.query.get_or_404(venta_id)
        raw_data = request.get_json()
        

        # Validar campos inmutables
        immutable_fields = ["detalles", "almacen_id"]
        for field in immutable_fields:
            if field in raw_data and str(raw_data[field]) != str(getattr(venta, field)):
                return {"error": f"Campo inmutable '{field}' no puede modificarse"}, 400
                # Cargar datos validados sobre la instancia existente

        updated_venta = venta_schema.load(
            raw_data,
            instance=venta,
            partial=True
        )

        db.session.commit()
        return venta_schema.dump(updated_venta), 200

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, venta_id):
        venta = Venta.query.get_or_404(venta_id)
        
        try:
            # Revertir movimientos e inventario
            movimientos = Movimiento.query.filter(
                Movimiento.motivo.like(f"Venta ID: {venta_id}%")
            ).all()
            
            for movimiento in movimientos:
                inventario = Inventario.query.filter_by(
                    presentacion_id=movimiento.presentacion_id,
                    almacen_id=venta.almacen_id
                ).first()
                
                if inventario:
                    inventario.cantidad += movimiento.cantidad
                
                db.session.delete(movimiento)
            
            db.session.delete(venta)
            db.session.commit()
            
            return {"message": "Venta eliminada con éxito"}, 200
            
        except Exception as e:
            db.session.rollback()
            return {"error": str(e)}, 500

# --- RECURSO PARA FORMULARIO DE VENTA (MODIFICADO) ---
class VentaFormDataResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Obtiene los datos necesarios para los formularios de creación/edición de ventas.

        - Si el usuario es Admin:
            - 'almacen_id' es opcional.
            - Si no se provee 'almacen_id', devuelve todas las presentaciones activas con el stock detallado por almacén.
            - Si se provee 'almacen_id', filtra presentaciones con stock >= 0 en ese almacén (comportamiento como no-admin).
        - Si el usuario no es Admin:
            - 'almacen_id' es requerido y debe coincidir con el del usuario.
            - Devuelve presentaciones activas con stock >= 0 solo en su almacén.
        """
        claims = get_jwt()
        is_admin = claims.get('rol') == 'admin'
        user_almacen_id = claims.get('almacen_id')
        almacen_id_str = request.args.get('almacen_id')
        almacen_id_param = None

        # --- Lógica de Validación de almacen_id --- 
        if almacen_id_str:
            try:
                almacen_id_param = int(almacen_id_str)
            except ValueError:
                return {"error": "El parámetro 'almacen_id' debe ser un número entero"}, 400
        
        # Si no es admin, almacen_id es obligatorio y debe coincidir
        if not is_admin:
            if not user_almacen_id:
                 return {"error": "Usuario no tiene almacén asignado."}, 403
            if not almacen_id_param:
                return {"error": "El parámetro 'almacen_id' es requerido para este usuario"}, 400
            if almacen_id_param != user_almacen_id:
                return {"error": "No tiene permisos para acceder a los datos de este almacén"}, 403
            # Para no-admin, siempre usamos su almacen_id
            target_almacen_id = user_almacen_id 
        else: # Si es admin
            # Usa el parámetro si se proporciona, sino es None
            target_almacen_id = almacen_id_param 
        # ----------------------------------------

        try:
            # Obtener Clientes y Almacenes (igual para todos)
            clientes = Cliente.query.order_by(Cliente.nombre).all()
            clientes_data = clientes_schema.dump(clientes, many=True)
            
            todos_almacenes = Almacen.query.order_by(Almacen.nombre).all()
            almacenes_data = almacenes_schema.dump(todos_almacenes, many=True)

            # --- Lógica Diferenciada para Presentaciones --- 
            presentaciones_data = []

            # CASO 1: Admin SIN almacen_id específico (mostrar stock de todos)
            if is_admin and target_almacen_id is None:
                logger.info("Admin sin almacen_id: Obteniendo stock global.")
                presentaciones_activas = PresentacionProducto.query.filter_by(activo=True).order_by(PresentacionProducto.nombre).all()
                # Optimización: Cargar todo el inventario relevante de una vez
                inventario_global = Inventario.query.filter(Inventario.presentacion_id.in_([p.id for p in presentaciones_activas])).all()
                # Crear un mapa para búsqueda rápida: (presentacion_id, almacen_id) -> cantidad
                stock_map = {(inv.presentacion_id, inv.almacen_id): inv.cantidad for inv in inventario_global}
                # Mapa de almacenes para nombres: almacen_id -> nombre
                almacen_map = {alm.id: alm.nombre for alm in todos_almacenes}

                for p in presentaciones_activas:
                    dumped_p = presentacion_schema.dump(p)
                    stock_por_almacen = []
                    for alm in todos_almacenes:
                        cantidad = stock_map.get((p.id, alm.id), 0) # Default 0 si no hay registro
                        stock_por_almacen.append({
                            "almacen_id": alm.id,
                            "nombre": almacen_map.get(alm.id, "Desconocido"),
                            "cantidad": cantidad
                        })
                    dumped_p['stock_por_almacen'] = stock_por_almacen
                    # URL pre-firmada
                    if p.url_foto:
                        dumped_p['url_foto'] = get_presigned_url(p.url_foto)
                    else:
                        dumped_p['url_foto'] = None
                    presentaciones_data.append(dumped_p)
            
            # CASO 2: No Admin O Admin CON almacen_id específico (mostrar stock de ese almacén)
            else:
                logger.info(f"Usuario (Admin: {is_admin}) con target_almacen_id={target_almacen_id}: Obteniendo stock específico.")
                inventario_filtrado = db.session.query(
                    PresentacionProducto,
                    Inventario.cantidad
                ).join(
                    Inventario, PresentacionProducto.id == Inventario.presentacion_id
                ).filter(
                    Inventario.almacen_id == target_almacen_id,
                    Inventario.cantidad >= 0, # Incluir stock 0 si se pide almacen específico
                    PresentacionProducto.activo == True
                ).order_by(PresentacionProducto.nombre).all()
                
                # Crear un conjunto de IDs de presentaciones ya añadidas para evitar duplicados si hay múltiples lotes (aunque la query actual no debería duplicar)
                presentaciones_incluidas = set()
                
                for presentacion, cantidad_stock in inventario_filtrado:
                    if presentacion.id not in presentaciones_incluidas:
                        dumped_presentacion = presentacion_schema.dump(presentacion)
                        dumped_presentacion['stock_disponible'] = cantidad_stock
                        if presentacion.url_foto:
                            dumped_presentacion['url_foto'] = get_presigned_url(presentacion.url_foto)
                        else:
                            dumped_presentacion['url_foto'] = None
                        presentaciones_data.append(dumped_presentacion)
                        presentaciones_incluidas.add(presentacion.id)

            # -------------------------------------------------

            # Determinar qué clave usar para las presentaciones en la respuesta
            presentaciones_key = 'presentaciones_con_stock_global' if (is_admin and target_almacen_id is None) else 'presentaciones_con_stock_local'

            return {
                "clientes": clientes_data,
                "almacenes": almacenes_data,
                presentaciones_key: presentaciones_data
            }, 200

        except Exception as e:
            logger.exception(f"Error en VentaFormDataResource: {e}") # Usar logger.exception para incluir traceback
            return {"error": "Error al obtener datos para el formulario de venta", "details": str(e)}, 500
# --- FIN RECURSO MODIFICADO ---
    