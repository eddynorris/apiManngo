from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash
import pytest

from extensions import db
from models import Almacen, Users


def _limpiar_tablas():
    for tabla in reversed(db.metadata.sorted_tables):
        db.session.execute(tabla.delete())
    db.session.commit()


def _login(client, username, password):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['access_token']


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture()
def telegram_context(test_client):
    _limpiar_tablas()

    almacen = Almacen(nombre='L4 Almacén Telegram')
    db.session.add(almacen)
    db.session.commit()

    objetivo = Users(
        username='l4_objetivo_tg',
        password=generate_password_hash('objetivopass1'),
        rol='usuario',
        almacen_id=almacen.id,
    )
    db.session.add(objetivo)
    db.session.commit()

    sombra = Users(
        username=str(objetivo.id),
        password=generate_password_hash('sombrapass1'),
        rol='usuario',
        almacen_id=almacen.id,
        telegram_linking_code='654321',
        telegram_chat_id=111222333,
    )
    db.session.add(sombra)
    db.session.commit()

    return {
        'objetivo': objetivo.id,
        'sombra': sombra.id,
        'almacen': almacen.id,
    }


def test_vinculacion_resuelve_por_id_y_no_por_username_numerico(telegram_context, test_client):
    token = _login(test_client, 'l4_objetivo_tg', 'objetivopass1')

    response = test_client.post('/telegram/vincular', headers=_headers(token))
    assert response.status_code == 200, response.get_data(as_text=True)

    code = response.get_json()['codigo']
    assert len(code) == 6 and code.isdigit()

    db.session.expire_all()
    objetivo = db.session.get(Users, telegram_context['objetivo'])
    sombra = db.session.get(Users, telegram_context['sombra'])

    assert objetivo.telegram_linking_code == code
    assert sombra.telegram_linking_code == '654321'
    assert sombra.telegram_chat_id == 111222333


def test_vinculacion_rechaza_token_de_usuario_borrado(telegram_context, test_client):
    token = _login(test_client, 'l4_objetivo_tg', 'objetivopass1')

    db.session.delete(db.session.get(Users, telegram_context['objetivo']))
    db.session.commit()

    response = test_client.post('/telegram/vincular', headers=_headers(token))
    assert response.status_code == 401

    db.session.expire_all()
    assert db.session.get(Users, telegram_context['objetivo']) is None


def test_vinculacion_rechaza_subject_no_numerico(telegram_context, test_client):
    token = create_access_token(identity='no-es-un-id')

    response = test_client.post('/telegram/vincular', headers=_headers(token))
    assert response.status_code == 401
