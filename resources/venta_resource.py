from flask_restful import Resource
from flask_jwt_extended import jwt_required, get_jwt
from flask import request
from models import Venta, VentaDetalle, Inventario, Cliente, PresentacionProducto, Almacen, Movimiento
from schemas import venta_schema, ventas_schema, venta_detalle_schema, clientes_schema, almacenes_schema, presentacion_schema, inventario_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, mismo_almacen_o_admin
from datetime import datetime, timezone
from decimal import Decimal

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
            return venta_schema.dump(venta), 200
        
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

# --- NUEVO RECURSO PARA FORMULARIO DE VENTA ---
class VentaFormDataResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Obtiene los datos necesarios para los formularios de creación/edición de ventas.
        Requiere 'almacen_id' como query param.
        Incluye listas de clientes, almacenes y presentaciones con stock disponible.
        """
        # Obtener y validar almacen_id
        almacen_id_str = request.args.get('almacen_id')
        if not almacen_id_str:
            return {"error": "El parámetro 'almacen_id' es requerido"}, 400
        try:
            almacen_id = int(almacen_id_str)
        except ValueError:
            return {"error": "El parámetro 'almacen_id' debe ser un número entero"}, 400

        # Verificar permisos sobre el almacén
        claims = get_jwt()
        if claims.get('rol') != 'admin' and claims.get('almacen_id') != almacen_id:
            return {"error": "No tiene permisos para acceder a los datos de este almacén"}, 403

        try:
            # Obtener Clientes
            clientes = Cliente.query.order_by(Cliente.nombre).all()
            clientes_data = clientes_schema.dump(clientes, many=True) # Asume many=True
            
            # Obtener Almacenes
            almacenes = Almacen.query.order_by(Almacen.nombre).all()
            almacenes_data = almacenes_schema.dump(almacenes, many=True) # Asume many=True

            # Obtener Presentaciones con Inventario disponible en el almacén especificado
            # Seleccionamos campos de PresentacionProducto y la cantidad de Inventario
            inventario_con_presentaciones = db.session.query(
                PresentacionProducto, 
                Inventario.cantidad
            ).join(
                Inventario, PresentacionProducto.id == Inventario.presentacion_id
            ).filter(
                Inventario.almacen_id == almacen_id,
                Inventario.cantidad > 0, # Solo incluir si hay stock
                PresentacionProducto.activo == True # Solo presentaciones activas
            ).order_by(PresentacionProducto.nombre).all()
            
            presentaciones_con_stock_data = []
            for presentacion, cantidad_stock in inventario_con_presentaciones:
                dumped_presentacion = presentacion_schema.dump(presentacion)
                # Añadir la cantidad disponible a la información de la presentación
                dumped_presentacion['stock_disponible'] = cantidad_stock 
                # Generar URL pre-firmada si hay foto
                if presentacion.url_foto:
                    from utils.file_handlers import get_presigned_url
                    dumped_presentacion['url_foto'] = get_presigned_url(presentacion.url_foto)
                else:
                    dumped_presentacion['url_foto'] = None
                presentaciones_con_stock_data.append(dumped_presentacion)

            return {
                "clientes": clientes_data,
                "almacenes": almacenes_data,
                "presentaciones_con_stock": presentaciones_con_stock_data
            }, 200

        except Exception as e:
            # Considerar usar logger.exception(e) para más detalles en logs
            return {"error": "Error al obtener datos para el formulario de venta", "details": str(e)}, 500
# --- FIN NUEVO RECURSO ---
    