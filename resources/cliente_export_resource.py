# ARCHIVO: resources/cliente_export_resource.py
from flask import send_file
from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required
from models import Cliente
from schemas import ClienteSchema
from common import handle_db_errors
import pandas as pd
import io
import logging

# Configurar logging
logger = logging.getLogger(__name__)

class ClienteExportResource(Resource):
    @jwt_required()
    @handle_db_errors
    def get(self):
        """
        Exporta todos los clientes a un archivo Excel, opcionalmente filtrado por ciudad.
        """
        parser = reqparse.RequestParser()
        parser.add_argument('ciudad', type=str, location='args', help='Filtra clientes por ciudad')
        args = parser.parse_args()
        ciudad = args.get('ciudad')

        try:
            # 1. Obtener clientes, aplicando filtro si se proporciona
            if ciudad:
                clientes = Cliente.query.filter_by(ciudad=ciudad).all()
            else:
                clientes = Cliente.query.all()
            if not clientes:
                return {"message": "No hay clientes para exportar"}, 404

            # 2. Serializar los datos con el esquema
            cliente_schema = ClienteSchema(many=True)
            data = cliente_schema.dump(clientes)

            # 3. Crear un DataFrame de pandas
            df = pd.DataFrame(data)

            # 4. Optimizar el DataFrame para el reporte
            columnas_deseadas = {
                'id': 'ID',
                'nombre': 'Nombre',
                'telefono': 'Teléfono',
                'direccion': 'Dirección',
                'ciudad': 'Ciudad',
                'saldo_pendiente': 'Saldo Pendiente',
                'ultima_fecha_compra': 'Última Compra',
                'frecuencia_compra_dias': 'Frecuencia de Compra'
            }
            
            # Filtrar y renombrar columnas
            df_optimizado = df[list(columnas_deseadas.keys())]
            df_optimizado = df_optimizado.rename(columns=columnas_deseadas)


            # 5. Crear un archivo Excel en memoria
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_optimizado.to_excel(writer, index=False, sheet_name='Clientes')
            
            output.seek(0)

            # 5. Enviar el archivo como respuesta
            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name='clientes.xlsx'
            )

        except Exception as e:
            logger.error(f"Error al exportar clientes: {str(e)}")
            return {"error": "Error interno al generar el archivo Excel"}, 500