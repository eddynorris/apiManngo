from werkzeug.security import generate_password_hash
import pytest

from extensions import db
from models import Users


LOGIN_PATH = '/auth'
USERNAME = 'l4_http_contract'
PASSWORD = 'contractpass1'


@pytest.fixture(scope='module')
def auth_user(test_client):
    usuario = Users(
        username=USERNAME,
        password=generate_password_hash(PASSWORD),
        rol='usuario',
    )
    db.session.add(usuario)
    db.session.commit()
    return usuario.id


def test_missing_password_returns_400(test_client, auth_user):
    response = test_client.post(LOGIN_PATH, json={'username': USERNAME})

    assert response.status_code == 400
    assert 'Error en el servidor' not in response.get_data(as_text=True)


def test_malformed_json_returns_400(test_client):
    response = test_client.post(
        LOGIN_PATH,
        data='{"username": ',
        content_type='application/json',
    )

    assert response.status_code == 400
    assert 'Error en el servidor' not in response.get_data(as_text=True)


def test_non_json_body_returns_client_error(test_client):
    response = test_client.post(
        LOGIN_PATH,
        data=f'username={USERNAME}',
        content_type='text/plain',
    )

    assert response.status_code in (400, 415)
    assert 'Error en el servidor' not in response.get_data(as_text=True)


def test_invalid_credentials_returns_401_without_raw_exception(test_client, auth_user):
    response = test_client.post(
        LOGIN_PATH,
        json={'username': USERNAME, 'password': 'incorrecta'},
    )

    assert response.status_code == 401
    assert response.get_json()['message'] == 'Credenciales inválidas'

    raw = response.get_data(as_text=True)
    assert 'Traceback' not in raw
    assert 'psycopg' not in raw.lower()
    assert 'IntegrityError' not in raw
    assert 'Error en el servidor' not in raw
