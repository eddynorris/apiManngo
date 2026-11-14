import pytest
from flask import jsonify
from werkzeug.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    RequestEntityTooLarge,
    TooManyRequests,
    Unauthorized,
)
from marshmallow import ValidationError

from app import app
from common import handle_db_errors
from extensions import db
from models import Almacen

SENTINEL_TUPLE = 'SENTINEL_SECRETO_TUPLE_500'
SENTINEL_RESPONSE = 'SENTINEL_SECRETO_RESPONSE_503'
SENTINEL_UNHANDLED = 'SENTINEL_SECRETO_EXCEPCION_500'

_HTTP_EXCEPTIONS = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
    404: NotFound,
    409: Conflict,
    413: RequestEntityTooLarge,
    429: TooManyRequests,
}


@app.route('/__tests/http/tuple-500', methods=['GET'], endpoint='tests_tuple_500')
@handle_db_errors
def _tests_tuple_500():
    db.session.add(Almacen(nombre=SENTINEL_TUPLE))
    db.session.flush()
    return {
        'error': SENTINEL_TUPLE,
        'detalle': SENTINEL_TUPLE,
    }, 500, {'Retry-After': '9', 'X-Test-Keep': 'ok', 'Content-Length': '999'}


@app.route('/__tests/http/response-503', methods=['GET'], endpoint='tests_response_503')
@handle_db_errors
def _tests_response_503():
    db.session.add(Almacen(nombre=SENTINEL_RESPONSE))
    db.session.flush()
    respuesta = jsonify({'error': SENTINEL_RESPONSE, 'details': SENTINEL_RESPONSE})
    respuesta.status_code = 503
    respuesta.headers['Retry-After'] = '11'
    respuesta.headers['X-Test-Keep'] = 'ok'
    return respuesta


@app.route('/__tests/http/unhandled-500', methods=['GET'], endpoint='tests_unhandled_500')
@handle_db_errors
def _tests_unhandled_500():
    db.session.add(Almacen(nombre=SENTINEL_UNHANDLED))
    db.session.flush()
    raise RuntimeError(SENTINEL_UNHANDLED)


@app.route('/__tests/http/validation-400', methods=['GET'], endpoint='tests_validation_400')
@handle_db_errors
def _tests_validation_400():
    raise ValidationError({'cantidad': ['Debe ser mayor que cero']})


@app.route('/__tests/http/tuple-400', methods=['GET'], endpoint='tests_tuple_400')
@handle_db_errors
def _tests_tuple_400():
    return {'message': 'Datos inválidos', 'errors': {'cantidad': ['Requerido']}}, 400


@app.route('/__tests/http/tuple-409', methods=['GET'], endpoint='tests_tuple_409')
@handle_db_errors
def _tests_tuple_409():
    return {'message': 'Conflicto de negocio', 'error': 'conflicto_seguro'}, 409


@app.route('/__tests/http/http-error/<int:code>', methods=['GET'], endpoint='tests_http_error')
@handle_db_errors
def _tests_http_error(code):
    excepcion = _HTTP_EXCEPTIONS.get(code)
    if excepcion is None:
        raise NotFound('Código no soportado')
    raise excepcion(f'Error HTTP controlado {code}')


def _assert_sanitized(response, status):
    body = response.get_json()
    assert response.status_code == status
    assert set(body) == {'message', 'error_id'}
    assert body['message'] == 'Error interno del servidor'
    assert isinstance(body['error_id'], str) and body['error_id']
    assert SENTINEL_TUPLE not in response.get_data(as_text=True)
    assert SENTINEL_RESPONSE not in response.get_data(as_text=True)
    assert SENTINEL_UNHANDLED not in response.get_data(as_text=True)


def test_tuple_500_is_sanitized_with_correlation_and_headers(test_client):
    response = test_client.get('/__tests/http/tuple-500')

    _assert_sanitized(response, 500)
    assert response.headers.get('Retry-After') == '9'
    assert response.headers.get('X-Test-Keep') == 'ok'
    assert response.headers.get('Content-Length') != '999'
    assert Almacen.query.filter_by(nombre=SENTINEL_TUPLE).count() == 0


def test_response_5xx_is_sanitized_preserving_status_and_headers(test_client):
    response = test_client.get('/__tests/http/response-503')

    _assert_sanitized(response, 503)
    assert response.headers.get('Retry-After') == '11'
    assert response.headers.get('X-Test-Keep') == 'ok'
    assert Almacen.query.filter_by(nombre=SENTINEL_RESPONSE).count() == 0


def test_unhandled_500_sanitized_and_rolled_back(test_client):
    response = test_client.get('/__tests/http/unhandled-500')

    _assert_sanitized(response, 500)
    assert Almacen.query.filter_by(nombre=SENTINEL_UNHANDLED).count() == 0


def test_validation_error_400_keeps_safe_details(test_client):
    response = test_client.get('/__tests/http/validation-400')
    body = response.get_json()

    assert response.status_code == 400
    assert body['message'] == 'Datos inválidos'
    assert body['errors'] == {'cantidad': ['Debe ser mayor que cero']}
    assert 'error_id' not in body


def test_returned_400_is_not_sanitized(test_client):
    response = test_client.get('/__tests/http/tuple-400')
    body = response.get_json()

    assert response.status_code == 400
    assert body == {
        'message': 'Datos inválidos',
        'errors': {'cantidad': ['Requerido']},
    }


def test_returned_409_is_not_sanitized(test_client):
    response = test_client.get('/__tests/http/tuple-409')
    body = response.get_json()

    assert response.status_code == 409
    assert body == {
        'message': 'Conflicto de negocio',
        'error': 'conflicto_seguro',
    }


@pytest.mark.parametrize('code', [400, 401, 403, 404, 409, 413, 429])
def test_http_exception_statuses_are_preserved(test_client, code):
    response = test_client.get(f'/__tests/http/http-error/{code}')

    assert response.status_code == code
    assert 'error_id' not in response.get_data(as_text=True)
