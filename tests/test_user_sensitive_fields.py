import json

from werkzeug.security import check_password_hash, generate_password_hash
import pytest

from extensions import db
from models import Almacen, Users


CAMPOS_SENSIBLES = {
    'password',
    'auth_version',
    'telegram_chat_id',
    'telegram_context',
    'telegram_history',
    'telegram_linking_code',
    'telegram_linking_expires',
}


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


def _assert_sin_datos_sensibles(payload, hash_guardado, texto_plano=None):
    assert CAMPOS_SENSIBLES.isdisjoint(payload.keys())
    serializado = json.dumps(payload)
    assert hash_guardado not in serializado
    assert 'pbkdf2:' not in serializado
    assert 'scrypt:' not in serializado
    if texto_plano:
        assert texto_plano not in serializado


@pytest.fixture()
def users_context(test_client):
    _limpiar_tablas()

    almacen = Almacen(nombre='L4 Almacén Sensibles')
    db.session.add(almacen)
    db.session.commit()

    hash_admin = generate_password_hash('adminpass1')
    hash_usuario = generate_password_hash('userpass1')
    admin = Users(username='l4_admin_fields', password=hash_admin, rol='admin', almacen_id=almacen.id)
    usuario = Users(
        username='l4_user_fields',
        password=hash_usuario,
        rol='usuario',
        almacen_id=almacen.id,
        telegram_chat_id=987654321,
        telegram_context={'estado': 'privado'},
        telegram_history=[{'texto': 'privado'}],
        telegram_linking_code='L4PIN98765',
    )
    db.session.add_all([admin, usuario])
    db.session.commit()

    return {
        'almacen': almacen.id,
        'admin': admin.id,
        'usuario': usuario.id,
        'hash_admin': hash_admin,
        'hash_usuario': hash_usuario,
    }


def test_get_lista_usuarios_no_expone_campos_sensibles(users_context, test_client):
    token = _login(test_client, 'l4_admin_fields', 'adminpass1')
    response = test_client.get('/usuarios', headers=_headers(token))

    assert response.status_code == 200, response.get_data(as_text=True)
    for item in response.get_json()['data']:
        _assert_sin_datos_sensibles(item, users_context['hash_usuario'])
        _assert_sin_datos_sensibles(item, users_context['hash_admin'])


def test_get_usuario_individual_no_expone_campos_sensibles(users_context, test_client):
    token = _login(test_client, 'l4_admin_fields', 'adminpass1')
    response = test_client.get(
        f"/usuarios/{users_context['usuario']}", headers=_headers(token)
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    _assert_sin_datos_sensibles(response.get_json(), users_context['hash_usuario'])
    assert 'L4PIN98765' not in response.get_data(as_text=True)


def test_post_usuario_no_expone_password_ni_campos_internos(users_context, test_client):
    token = _login(test_client, 'l4_admin_fields', 'adminpass1')
    response = test_client.post(
        '/usuarios',
        headers=_headers(token),
        json={
            'username': 'l4_nuevo_fields',
            'password': 'nuevopass1',
            'rol': 'usuario',
            'almacen_id': users_context['almacen'],
        },
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    _assert_sin_datos_sensibles(response.get_json(), users_context['hash_admin'], 'nuevopass1')

    db.session.expire_all()
    creado = Users.query.filter_by(username='l4_nuevo_fields').first()
    assert creado is not None
    assert check_password_hash(creado.password, 'nuevopass1')


def test_put_usuario_no_expone_password_ni_campos_internos(users_context, test_client):
    token = _login(test_client, 'l4_admin_fields', 'adminpass1')
    response = test_client.put(
        f"/usuarios/{users_context['usuario']}",
        headers=_headers(token),
        json={'password': 'otraclave1', 'rol': 'gerente'},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    _assert_sin_datos_sensibles(response.get_json(), users_context['hash_usuario'], 'otraclave1')

    db.session.expire_all()
    actualizado = db.session.get(Users, users_context['usuario'])
    assert actualizado.rol == 'gerente'
    assert actualizado.auth_version == 1
    assert check_password_hash(actualizado.password, 'otraclave1')

    posterior = test_client.get(
        f"/usuarios/{users_context['usuario']}", headers=_headers(token)
    )
    assert posterior.status_code == 200
    _assert_sin_datos_sensibles(posterior.get_json(), actualizado.password)
