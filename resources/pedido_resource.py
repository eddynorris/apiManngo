from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Pedido, PedidoDetalle, Cliente, PresentacionProducto, Almacen, Inventario, Movimiento, VentaDetalle, Venta
from schemas import pedido_schema, pedidos_schema, venta_schema, clientes_schema, almacenes_schema, presentacion_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin
from datetime import datetime, timezone
from decimal import Decimal
from utils.file_handlers import get_presigned_url
import logging

logger = logging.getLogger(__name__)

class PedidoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, pedido_id=None):
        """
        Obtiene pedido(s)
        - Con ID: Detalle completo del pedido (con URLs pre-firmadas para detalles)
        - Sin ID: Lista paginada con filtros (cliente_id, almacen_id, fecha_inicio, fecha_fin, estado)
        """
        if pedido_id:
            pedido = Pedido.query.get_or_404(pedido_id)
            
            # Serializar el pedido
            result = pedido_schema.dump(pedido)
            
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
        
        # Construir query con filtros
        query = Pedido.query
        
        # Aplicar filtros
        if cliente_id := request.args.get('cliente_id'):
            query = query.filter_by(cliente_id=cliente_id)
        
        if almacen_id := request.args.get('almacen_id'):
            query = query.filter_by(almacen_id=almacen_id)
        
        if vendedor_id := request.args.get('vendedor_id'):
            query = query.filter_by(vendedor_id=vendedor_id)
            
        if estado := request.args.get('estado'):
            query = query.filter_by(estado=estado)
            
        if fecha_inicio := request.args.get('fecha_inicio'):
            if fecha_fin := request.args.get('fecha_fin'):
                try:
                    fecha_inicio = datetime.fromisoformat(fecha_inicio).replace(tzinfo=timezone.utc)
                    fecha_fin = datetime.fromisoformat(fecha_fin).replace(tzinfo=timezone.utc)
                    
                    # Filtrar por fecha de entrega
                    query = query.filter(Pedido.fecha_entrega.between(fecha_inicio, fecha_fin))
                except ValueError:
                    return {"error": "Formato de fecha inválido. Usa ISO 8601 (ej: '2025-03-05T00:00:00')"}, 400
        
        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        pedidos = query.paginate(page=page, per_page=per_page)
        
        return {
            "data": pedidos_schema.dump(pedidos.items),
            "pagination": {
                "total": pedidos.total,
                "page": pedidos.page,
                "per_page": pedidos.per_page,
                "pages": pedidos.pages
            }
        }, 200

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self):

        data = pedido_schema.load(request.get_json())
        
        # Validaciones
        Cliente.query.get_or_404(data.cliente_id)
        Almacen.query.get_or_404(data.almacen_id)
        
        # Asignar vendedor automáticamente desde JWT
        claims = get_jwt()
        data.vendedor_id = claims.get('sub')
        
        # Validar detalles del pedido
        for detalle in data.detalles:
            presentacion = PresentacionProducto.query.get_or_404(detalle.presentacion_id)
            # El precio estimado usualmente es el de venta actual, pero podría ser diferente
            if not detalle.precio_estimado:
                detalle.precio_estimado = presentacion.precio_venta
        
        db.session.add(data)
        db.session.commit()
        
        return pedido_schema.dump(data), 201

    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def put(self, pedido_id):
        """
        Actualiza un pedido existente
        """
        pedido = Pedido.query.get_or_404(pedido_id)
        
        # Validar estados - no permitir actualizar pedidos entregados
        if pedido.estado == 'entregado':
            return {"error": "No se puede modificar un pedido ya entregado"}, 400
        
        updated_pedido = pedido_schema.load(
            request.get_json(),
            instance=pedido,
            partial=True
        )
        
        db.session.commit()
        return pedido_schema.dump(updated_pedido), 200
    
    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def delete(self, pedido_id):
        """
        Elimina un pedido (o lo marca como cancelado)
        """
        pedido = Pedido.query.get_or_404(pedido_id)
        
        # Si ya está entregado, no permite eliminar
        if pedido.estado == 'entregado':
            return {"error": "No se puede eliminar un pedido ya entregado"}, 400
        
        # Opción 1: Eliminar
        db.session.delete(pedido)
        
        # Opción 2: Marcar como cancelado (alternativa)
        # pedido.estado = 'cancelado'
        
        db.session.commit()
        return "Pedido eliminado correctamente", 200

class PedidoConversionResource(Resource):
    @jwt_required()
    @mismo_almacen_o_admin
    @handle_db_errors
    def post(self, pedido_id):
        """
        Convierte un pedido en una venta real
        """
        pedido = Pedido.query.get_or_404(pedido_id)
        
        # Validaciones previas
        if pedido.estado == 'entregado':
            return {"error": "Este pedido ya fue entregado"}, 400
            
        if pedido.estado == 'cancelado':
            return {"error": "No se puede convertir un pedido cancelado"}, 400
        
        # --- Optimización: Obtener inventarios necesarios --- 
        presentacion_ids = [d.presentacion_id for d in pedido.detalles]
        if not presentacion_ids:
            return {"error": "El pedido no tiene detalles para convertir"}, 400

        inventarios = Inventario.query.filter(
            Inventario.presentacion_id.in_(presentacion_ids),
            Inventario.almacen_id == pedido.almacen_id
        ).all()
        inventarios_dict = {i.presentacion_id: i for i in inventarios}
        # ----------------------------------------------------

        # Verificar stock antes de proceder
        inventarios_insuficientes = []
        for detalle in pedido.detalles:
            inventario = inventarios_dict.get(detalle.presentacion_id)
            
            if not inventario or inventario.cantidad < detalle.cantidad:
                inventarios_insuficientes.append({
                    "presentacion": detalle.presentacion.nombre, # Asume relación cargada
                    "solicitado": detalle.cantidad,
                    "disponible": inventario.cantidad if inventario else 0
                })
        
        if inventarios_insuficientes:
            return {
                "error": "Stock insuficiente para completar el pedido",
                "detalles": inventarios_insuficientes
            }, 400
        
        # Crear nueva venta desde el pedido
        venta = Venta(
            cliente_id=pedido.cliente_id,
            almacen_id=pedido.almacen_id,
            tipo_pago=request.json.get('tipo_pago', 'contado'),
            estado_pago='pendiente'
        )
        
        # Agregar detalles y calcular total
        total = 0
        for detalle_pedido in pedido.detalles:
            precio_actual = detalle_pedido.presentacion.precio_venta
            
            # Usar precio actual o el estimado, según configuración
            usar_precio_actual = request.json.get('usar_precio_actual', True)
            precio_final = precio_actual if usar_precio_actual else detalle_pedido.precio_estimado
            
            detalle_venta = VentaDetalle(
                presentacion_id=detalle_pedido.presentacion_id,
                cantidad=detalle_pedido.cantidad,
                precio_unitario=precio_final
            )
            venta.detalles.append(detalle_venta)
            total += detalle_venta.cantidad * detalle_venta.precio_unitario
        
        venta.total = total
        
        # Actualizar inventario y crear movimientos de salida
        claims = get_jwt()
        for detalle in venta.detalles:
            inventario = Inventario.query.filter_by(
                presentacion_id=detalle.presentacion_id,
                almacen_id=venta.almacen_id
            ).first()
            
            inventario.cantidad -= detalle.cantidad
            
            # Registrar movimiento
            movimiento = Movimiento(
                tipo='salida',
                presentacion_id=detalle.presentacion_id,
                lote_id=inventario.lote_id,
                cantidad=detalle.cantidad,
                usuario_id=claims.get('sub'),
                motivo=f"Venta ID: {venta.id} - Cliente: {pedido.cliente.nombre} (desde pedido {pedido.id})"
            )
            db.session.add(movimiento)
        
        # Actualizar cliente si es necesario
        if venta.consumo_diario_kg:
            cliente = Cliente.query.get(venta.cliente_id)
            cliente.ultima_fecha_compra = datetime.now(timezone.utc)
            cliente.frecuencia_compra_dias = (venta.total / Decimal(venta.consumo_diario_kg)).quantize(Decimal('1.00'))
        
        # Marcar pedido como entregado
        pedido.estado = 'entregado'
        
        db.session.add(venta)
        db.session.commit()
        
        return {
            "message": "Pedido convertido a venta exitosamente",
            "venta": venta_schema.dump(venta)
        }, 201
    
# --- RECURSO PARA FORMULARIO DE PEDIDO (MODIFICADO) ---
class PedidoFormDataResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Obtiene los datos necesarios para los formularios de creación/edición de pedidos.
        Adapta la respuesta según el rol del usuario.

        - Si el usuario es Admin:
            - Devuelve todos los clientes, almacenes y presentaciones activas.
            - Cada presentación incluirá el campo 'stock_por_almacen' con el detalle de stock en cada almacén.
        - Si el usuario no es Admin:
            - Devuelve todos los clientes y almacenes.
            - Devuelve solo las presentaciones activas.
            - No incluye información de stock detallada (ya que el stock real se valida al convertir el pedido a venta).
        """
        claims = get_jwt()
        is_admin = claims.get('rol') == 'admin'
        # No necesitamos user_almacen_id aquí ya que no filtramos por él directamente en este endpoint

        try:
            # Obtener Clientes y Almacenes (igual para todos)
            clientes = Cliente.query.order_by(Cliente.nombre).all()
            clientes_data = clientes_schema.dump(clientes, many=True)
            
            todos_almacenes = Almacen.query.order_by(Almacen.nombre).all()
            almacenes_data = almacenes_schema.dump(todos_almacenes, many=True)

            # --- Lógica Diferenciada para Presentaciones --- 
            presentaciones_data = []
            presentaciones_activas = PresentacionProducto.query.filter_by(activo=True).order_by(PresentacionProducto.nombre).all()

            # Si es Admin, obtener y añadir el stock global
            if is_admin:
                logger.info("Admin en PedidoFormData: Obteniendo stock global.")
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
            
            # Si no es Admin, solo devolver las presentaciones sin stock detallado
            else:
                logger.info("No Admin en PedidoFormData: Obteniendo solo presentaciones activas.")
                for p in presentaciones_activas:
                    dumped_p = presentacion_schema.dump(p)
                     # URL pre-firmada
                    if p.url_foto:
                        dumped_p['url_foto'] = get_presigned_url(p.url_foto)
                    else:
                        dumped_p['url_foto'] = None
                    presentaciones_data.append(dumped_p)
            # -------------------------------------------------

            # Determinar la clave de respuesta para presentaciones
            presentaciones_key = 'presentaciones_con_stock_global' if is_admin else 'presentaciones_activas'

            return {
                "clientes": clientes_data,
                "almacenes": almacenes_data,
                presentaciones_key: presentaciones_data
            }, 200

        except Exception as e:
            logger.exception(f"Error en PedidoFormDataResource: {e}")
            return {"error": "Error al obtener datos para el formulario de pedido", "details": str(e)}, 500
# --- FIN RECURSO MODIFICADO ---
    
    