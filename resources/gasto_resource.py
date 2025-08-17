# ARCHIVO: gasto_resource.py
from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required, get_jwt
from flask import request, send_file
from models import Gasto, Almacen, Users
from schemas import gasto_schema, gastos_schema
from extensions import db
from common import handle_db_errors, MAX_ITEMS_PER_PAGE
from sqlalchemy import asc, desc
import pandas as pd
import io
import logging

# Configurar logging
logger = logging.getLogger(__name__)

class GastoResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self, gasto_id=None):

        if gasto_id:
            return gasto_schema.dump(Gasto.query.get_or_404(gasto_id)), 200
        
        # --- Lógica de Ordenación Dinámica ---
        sort_by = request.args.get('sort_by', 'fecha') # Default a fecha
        sort_order = request.args.get('sort_order', 'desc').lower() # Default a desc

        sortable_columns = {
            'fecha': Gasto.fecha,
            'monto': Gasto.monto,
            'categoria': Gasto.categoria,
            'descripcion': Gasto.descripcion,
            'almacen_nombre': Almacen.nombre,     # Relacionado
            'usuario_username': Users.username    # Relacionado
        }

        column_to_sort = sortable_columns.get(sort_by, Gasto.fecha)
        order_func = desc if sort_order == 'desc' else asc
        # --- Fin Lógica de Ordenación ---

        query = Gasto.query

        # --- Aplicar Joins si es necesario para ordenar ---
        if sort_by == 'almacen_nombre':
            query = query.outerjoin(Almacen, Gasto.almacen_id == Almacen.id) # Usar outerjoin por si almacen_id es NULL
        elif sort_by == 'usuario_username':
            query = query.outerjoin(Users, Gasto.usuario_id == Users.id) # Usar outerjoin por si usuario_id es NULL
        # ------------------------------------------------

        # Construir query con filtros
        if categoria := request.args.get('categoria'):
            query = query.filter_by(categoria=categoria)
        if fecha := request.args.get('fecha'):
            query = query.filter_by(fecha=fecha)
        if usuario_id := request.args.get('usuario_id'):
            query = query.filter_by(usuario_id=usuario_id)
        if lote_id := request.args.get('lote_id'):
            query = query.filter_by(lote_id=lote_id)

        # --- APLICAR ORDENACIÓN ---
        query = query.order_by(order_func(column_to_sort))
        # -------------------------

        # Paginación
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), MAX_ITEMS_PER_PAGE)
        gastos = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return {
            "data": gastos_schema.dump(gastos.items),
            "pagination": {
                "total": gastos.total,
                "page": gastos.page,
                "per_page": gastos.per_page,
                "pages": gastos.pages
            }
        }, 200

    @jwt_required()
    @handle_db_errors
    def post(self):
        """Registra nuevo gasto con validación de categoría"""
        json_data = request.get_json()
        if json_data.get('lote_id'):
            Lote.query.get_or_404(json_data['lote_id'])

        data = gasto_schema.load(json_data)
        Almacen.query.get_or_404(data.almacen_id)
        data.usuario_id = get_jwt().get('sub')  # Asignar usuario actual
        
        db.session.add(data)
        db.session.commit()
        return gasto_schema.dump(data), 201

    @jwt_required()
    @handle_db_errors
    def put(self, gasto_id):
        """Actualiza gasto existente con validación de datos"""
        gasto = Gasto.query.get_or_404(gasto_id)
        data = gasto_schema.load(request.get_json(), partial=True)
        
        updated_gasto = gasto_schema.load(
            request.get_json(),
            instance=gasto,
            partial=True
        )
        db.session.commit()

        return gasto_schema.dump(updated_gasto), 200

    @jwt_required()
    @handle_db_errors
    def delete(self, gasto_id):
        """Elimina registro de gasto"""
        gasto = Gasto.query.get_or_404(gasto_id)
        db.session.delete(gasto)
        db.session.commit()
        return "Gasto eliminado correctamente", 200

class GastoExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Exporta todos los gastos a un archivo Excel, opcionalmente filtrado.
        """
        parser = reqparse.RequestParser()
        parser.add_argument('categoria', type=str, location='args')
        parser.add_argument('fecha', type=str, location='args')
        parser.add_argument('usuario_id', type=int, location='args')
        parser.add_argument('lote_id', type=int, location='args')
        args = parser.parse_args()

        try:
            query = Gasto.query.join(Almacen).join(Users)

            if args['categoria']:
                query = query.filter(Gasto.categoria == args['categoria'])
            if args['fecha']:
                query = query.filter(Gasto.fecha == args['fecha'])
            if args['usuario_id']:
                query = query.filter(Gasto.usuario_id == args['usuario_id'])
            if args['lote_id']:
                query = query.filter(Gasto.lote_id == args['lote_id'])

            gastos = query.all()
            if not gastos:
                return {"message": "No hay gastos para exportar con los filtros seleccionados"}, 404

            # Serializar los datos incluyendo relaciones
            data = gastos_schema.dump(gastos)

            df = pd.DataFrame(data)
            
            # Mapeo de campos anidados a columnas planas
            if not df.empty:
                df['almacen_nombre'] = df['almacen'].apply(lambda x: x.get('nombre') if isinstance(x, dict) else None)
                df['usuario_username'] = df['usuario'].apply(lambda x: x.get('username') if isinstance(x, dict) else None)

            columnas_deseadas = {
                'id': 'ID',
                'fecha': 'Fecha',
                'monto': 'Monto',
                'categoria': 'Categoría',
                'descripcion': 'Descripción',
                'almacen_nombre': 'Almacén',
                'usuario_username': 'Usuario'
            }
            
            df_optimizado = df[list(columnas_deseadas.keys())]
            df_optimizado = df_optimizado.rename(columns=columnas_deseadas)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_optimizado.to_excel(writer, index=False, sheet_name='Gastos')
            
            output.seek(0)

            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name='gastos.xlsx'
            )

        except Exception as e:
            logger.error(f"Error al exportar gastos: {str(e)}")
            return {"error": "Error interno al generar el archivo Excel"}, 500