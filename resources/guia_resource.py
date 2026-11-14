# ARCHIVO: guia_resource.py
"""Datos maestros de la guía de remisión (vehículos, conductores) y consulta de guías."""
import re

from flask import request
from flask_jwt_extended import jwt_required
from flask_restful import Resource
from sqlalchemy.exc import IntegrityError

from common import MAX_ITEMS_PER_PAGE, handle_db_errors, rol_requerido
from extensions import db
from models import Conductor, GuiaRemision, Vehiculo
from schemas import (
    conductor_schema,
    conductores_schema,
    guia_remision_schema,
    guias_remision_schema,
    vehiculo_schema,
    vehiculos_schema,
)

_PLACA_RE = re.compile(r"[A-Z0-9]{6,8}")


def _paginado(query, schema):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), MAX_ITEMS_PER_PAGE)
    resultado = query.paginate(page=page, per_page=per_page, error_out=False)
    return {
        "data": schema.dump(resultado.items),
        "pagination": {
            "total": resultado.total,
            "page": resultado.page,
            "per_page": resultado.per_page,
            "pages": resultado.pages,
        },
    }, 200


def _cuerpo():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def normalizar_placa(valor):
    if not isinstance(valor, str):
        return None
    placa = re.sub(r"[\s-]", "", valor).upper()
    return placa if _PLACA_RE.fullmatch(placa) else None


def _unico_predeterminado(modelo, registro):
    if registro.predeterminado:
        modelo.query.filter(modelo.id != registro.id).update(
            {modelo.predeterminado: False}, synchronize_session=False
        )


class _MaestroResource(Resource):
    modelo = None
    schema = None
    schema_lista = None
    nombre = ""

    def _normalizar(self, data):
        """Devuelve (data_normalizada, error) para crear/actualizar."""
        return data, None

    @jwt_required()
    @handle_db_errors
    def get(self, registro_id=None):
        if registro_id:
            return self.schema.dump(self.modelo.query.get_or_404(registro_id)), 200
        query = self.modelo.query.order_by(self.modelo.predeterminado.desc(), self.modelo.id.asc())
        if request.args.get('activos') in ('1', 'true'):
            query = query.filter(self.modelo.activo.is_(True))
        return _paginado(query, self.schema_lista)

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def post(self):
        data = _cuerpo()
        if data is None:
            return {"error": "Se requiere un cuerpo JSON"}, 400
        data, error = self._normalizar(data)
        if error:
            return {"error": error}, 400
        registro = self.schema.load(data)
        db.session.add(registro)
        try:
            db.session.flush()
            _unico_predeterminado(self.modelo, registro)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return {"error": f"Ya existe un {self.nombre} con ese identificador"}, 409
        return self.schema.dump(registro), 201

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def put(self, registro_id):
        registro = self.modelo.query.get_or_404(registro_id)
        data = _cuerpo()
        if data is None:
            return {"error": "Se requiere un cuerpo JSON"}, 400
        data, error = self._normalizar(data, parcial=True)
        if error:
            return {"error": error}, 400
        self.schema.load(data, instance=registro, partial=True)
        try:
            db.session.flush()
            _unico_predeterminado(self.modelo, registro)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return {"error": f"Ya existe un {self.nombre} con ese identificador"}, 409
        return self.schema.dump(registro), 200

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def delete(self, registro_id):
        """Baja lógica: las guías emitidas conservan la referencia."""
        registro = self.modelo.query.get_or_404(registro_id)
        registro.activo = False
        registro.predeterminado = False
        db.session.commit()
        return "", 204


class VehiculoResource(_MaestroResource):
    modelo = Vehiculo
    schema = vehiculo_schema
    schema_lista = vehiculos_schema
    nombre = "vehículo"

    def _normalizar(self, data, parcial=False):
        data = dict(data)
        if 'placa' in data or not parcial:
            placa = normalizar_placa(data.get('placa'))
            if not placa:
                return None, "Placa inválida: 6 a 8 letras o números, sin guiones (p. ej. D8M790)"
            data['placa'] = placa
        return data, None


class ConductorResource(_MaestroResource):
    modelo = Conductor
    schema = conductor_schema
    schema_lista = conductores_schema
    nombre = "conductor"

    def _normalizar(self, data, parcial=False):
        data = dict(data)
        requeridos = ('numero_documento', 'nombres', 'apellidos', 'licencia')
        for campo in requeridos:
            if campo in data or not parcial:
                valor = data.get(campo)
                if not isinstance(valor, str) or not valor.strip():
                    return None, f"El campo '{campo}' es obligatorio"
                data[campo] = valor.strip().upper() if campo == 'licencia' else valor.strip()
        if 'tipo_documento' in data and data['tipo_documento'] not in ('1', '4', '7'):
            return None, "tipo_documento debe ser '1' (DNI), '4' (CE) o '7' (pasaporte)"
        if data.get('tipo_documento', '1') == '1' and 'numero_documento' in data:
            if not re.fullmatch(r"\d{8}", data['numero_documento']):
                return None, "El DNI del conductor debe tener 8 dígitos"
        return data, None


class GuiaRemisionResource(Resource):
    """Consulta de guías (estado, número, QR). La emisión va por Telegram/servicio."""

    @jwt_required()
    @rol_requerido('admin', 'gerente')
    @handle_db_errors
    def get(self, guia_id=None):
        if guia_id:
            return guia_remision_schema.dump(GuiaRemision.query.get_or_404(guia_id)), 200
        query = GuiaRemision.query.order_by(GuiaRemision.id.desc())
        if estado := request.args.get('estado'):
            query = query.filter(GuiaRemision.estado == estado)
        return _paginado(query, guias_remision_schema)
