
# ARCHIVO: resources/presentacion_resource.py
from flask_restful import Resource
from flask_jwt_extended import jwt_required # get_jwt no usado aquí
from flask import request, jsonify # Eliminado jsonify de nuevo
from models import PresentacionProducto, Inventario, VentaDetalle
from schemas import presentacion_schema, presentaciones_schema # Asegúrate que existan y sean correctos
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE, rol_requerido
from utils.file_handlers import save_file, delete_file, get_presigned_url
# import os # No usado directamente aquí
# from werkzeug.datastructures import FileStorage # No usado directamente aquí
# from flask import current_app # No usado directamente aquí

class PresentacionResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, presentacion_id=None):
        """
        Obtiene presentaciones de productos
        - Con ID: Detalle completo con producto asociado
        - Sin ID: Lista paginada con filtros (producto_id, tipo, activo)
        """
        if presentacion_id:
            presentacion = PresentacionProducto.query.get_or_404(presentacion_id)
            # Serializar datos básicos
            result = presentacion_schema.dump(presentacion)
            # Generar URL pre-firmada si hay clave S3
            # Asume que el campo se llama 'url_foto' y guarda la clave S3
            if presentacion.url_foto:
                presigned_url = get_presigned_url(presentacion.url_foto)
                result['url_foto'] = presigned_url
            else:
                result['foto_url'] = None # Asegurar que el campo exista
            # --- CORRECCIÓN: Devolver diccionario directamente ---
            return result, 200

        # Construir query con filtros
        query = PresentacionProducto.query
        if producto_id := request.args.get('producto_id'):
            query = query.filter_by(producto_id=producto_id)
        if tipo := request.args.get('tipo'):
            query = query.filter_by(tipo=tipo)
        if activo_str := request.args.get('activo'): # Renombrado para claridad
            activo = activo_str.lower() == 'true'
            query = query.filter_by(activo=activo)

        # Ordenar (opcional)
        query = query.order_by(PresentacionProducto.nombre)

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        resultado = query.paginate(page=page, per_page=per_page, error_out=False)

        # Preparar datos para respuesta, incluyendo URLs pre-firmadas para la lista
        items_data = []
        for item in resultado.items:
             # --- CORRECCIÓN: Usar schema singular si presentaciones_schema es para listas ---
             # Si 'presentaciones_schema' es Many=True, usarlo así está bien.
             # Si es igual a 'presentacion_schema', usar presentacion_schema.dump(item)
            dumped_item = presentacion_schema.dump(item) # Asumiendo detalle individual
            if item.url_foto:
                dumped_item['url_foto'] = get_presigned_url(item.url_foto)
            else:
                dumped_item['url_foto'] = None # Asegurar que el campo exista
            items_data.append(dumped_item)

        # --- CORRECCIÓN: Devolver diccionario directamente ---
        return {
            "data": items_data,
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
        """Crea nueva presentación con posibilidad de subir foto"""
        # Procesar datos JSON
        if 'application/json' in request.content_type:
            data = request.get_json()
            # Validar con Marshmallow
            errors = presentacion_schema.validate(data)
            if errors:
                 return {"errors": errors}, 400

            # Cargar datos validados creando una instancia del modelo
            nueva_presentacion = presentacion_schema.load(data, session=db.session)

            # Verificar unicidad (producto_id, nombre)
            existe = PresentacionProducto.query.filter_by(
                producto_id=nueva_presentacion.producto_id,
                nombre=nueva_presentacion.nombre
            ).first()
            if existe:
                return {
                    "error": "Conflicto de unicidad",
                    "mensaje": f"Ya existe una presentación con el nombre '{nueva_presentacion.nombre}' para este producto."
                }, 409

            db.session.add(nueva_presentacion)
            db.session.commit()
            return presentacion_schema.dump(nueva_presentacion), 201

        # Procesar formulario multipart con archivos
        elif 'multipart/form-data' in request.content_type:
            # Obtener datos del formulario
            producto_id = request.form.get('producto_id')
            nombre = request.form.get('nombre')
            capacidad_kg = request.form.get('capacidad_kg')
            tipo = request.form.get('tipo')
            precio_venta = request.form.get('precio_venta')
            activo = request.form.get('activo', 'true').lower() == 'true'

            # Validaciones básicas
            if not all([producto_id, nombre, capacidad_kg, tipo, precio_venta]):
                return {"error": "Faltan campos requeridos"}, 400

            # Verificar unicidad
            existe = PresentacionProducto.query.filter_by(
                producto_id=producto_id,
                nombre=nombre
            ).first()
            if existe:
                return {
                    "error": "Conflicto de unicidad",
                    "mensaje": f"Ya existe una presentación con el nombre '{nombre}' para este producto."
                }, 409

            # Procesar imagen si existe
            s3_key_foto = None
            if 'foto' in request.files:
                file = request.files['foto']
                if file.filename != '':
                     s3_key_foto = save_file(file, 'presentaciones') # save_file devuelve la clave
                     if not s3_key_foto:
                         return {"error": "Error al subir la foto"}, 500

            # Crear presentación
            nueva_presentacion = PresentacionProducto(
                producto_id=producto_id,
                nombre=nombre,
                capacidad_kg=capacidad_kg, # Asegurar conversión a tipo correcto si es necesario
                tipo=tipo,
                precio_venta=precio_venta, # Asegurar conversión a Decimal si es necesario
                activo=activo,
                url_foto=s3_key_foto # Guardar la clave S3
            )

            db.session.add(nueva_presentacion)
            db.session.commit()

            return presentacion_schema.dump(nueva_presentacion), 201

        return {"error": "Tipo de contenido no soportado"}, 415

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, presentacion_id):
        """Actualiza presentación con posibilidad de cambiar foto"""
        presentacion = PresentacionProducto.query.get_or_404(presentacion_id)

        # Actualización JSON
        if 'application/json' in request.content_type:
            data = request.get_json()
            # Validar con Marshmallow (partial=True)
            errors = presentacion_schema.validate(data, partial=True)
            if errors:
                 return {"errors": errors}, 400

            # Validación única adicional si el nombre cambia
            if 'nombre' in data and data['nombre'] != presentacion.nombre:
                if PresentacionProducto.query.filter(
                    PresentacionProducto.producto_id == presentacion.producto_id,
                    PresentacionProducto.nombre == data['nombre'],
                    PresentacionProducto.id != presentacion_id
                ).first():
                    return {"error": "Nombre ya existe para este producto"}, 409

            # Cargar/Actualizar la instancia existente
            presentacion = presentacion_schema.load(data, instance=presentacion, partial=True, session=db.session)

            db.session.commit()
            return presentacion_schema.dump(presentacion), 200

        # Actualización con formulario multipart
        elif 'multipart/form-data' in request.content_type:
            # Actualizar campos si están presentes
            if 'nombre' in request.form:
                nuevo_nombre = request.form.get('nombre')
                if nuevo_nombre != presentacion.nombre:
                    if PresentacionProducto.query.filter(
                        PresentacionProducto.producto_id == presentacion.producto_id,
                        PresentacionProducto.nombre == nuevo_nombre,
                        PresentacionProducto.id != presentacion_id
                    ).first():
                        return {"error": "Nombre ya existe para este producto"}, 409
                    presentacion.nombre = nuevo_nombre

            if 'capacidad_kg' in request.form:
                presentacion.capacidad_kg = request.form.get('capacidad_kg') # Convertir si es necesario
            if 'tipo' in request.form:
                presentacion.tipo = request.form.get('tipo')
            if 'precio_venta' in request.form:
                presentacion.precio_venta = request.form.get('precio_venta') # Convertir a Decimal si es necesario
            if 'activo' in request.form:
                presentacion.activo = request.form.get('activo').lower() == 'true'

            # Procesar imagen si existe
            if 'foto' in request.files:
                file = request.files['foto']
                if file.filename != '':
                    # Eliminar foto anterior si existe (usando la clave S3)
                    if presentacion.url_foto:
                        delete_file(presentacion.url_foto)
                    # Guardar nueva foto y obtener su clave S3
                    s3_key_nueva = save_file(file, 'presentaciones')
                    if s3_key_nueva:
                        presentacion.url_foto = s3_key_nueva # Actualizar la clave S3 en el modelo
                    else:
                        return {"error": "Error al subir la nueva foto"}, 500

            # Si se especifica eliminar la foto (y no se subió una nueva)
            elif request.form.get('eliminar_foto') == 'true' and presentacion.url_foto:
                delete_file(presentacion.url_foto) # Eliminar usando la clave S3
                presentacion.url_foto = None

            db.session.commit()
            return presentacion_schema.dump(presentacion), 200

        return {"error": "Tipo de contenido no soportado"}, 415

    @jwt_required()
    @rol_requerido('admin')
    @handle_db_errors
    def delete(self, presentacion_id):
        """Elimina presentación y su foto asociada"""
        presentacion = PresentacionProducto.query.get_or_404(presentacion_id)

        # Verificar dependencias
        if Inventario.query.filter_by(presentacion_id=presentacion_id).first():
            return {"error": "Existen registros de inventario asociados"}, 400
        if VentaDetalle.query.filter_by(presentacion_id=presentacion_id).first():
            return {"error": "Existen ventas asociadas"}, 400

        # Eliminar foto de S3 si existe (usando la clave)
        if presentacion.url_foto:
            delete_file(presentacion.url_foto)

        db.session.delete(presentacion)
        db.session.commit()
        # --- CORRECCIÓN: Devolver un mensaje JSON ---
        return {'message': "Presentación eliminada exitosamente"}, 200