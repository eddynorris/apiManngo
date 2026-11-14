import pytest
from flask_jwt_extended import create_access_token
from werkzeug.exceptions import Forbidden
from werkzeug.security import generate_password_hash

from app import app
from common import mismo_almacen_o_admin, rol_requerido
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
def auth_context(test_client):
    _limpiar_tablas()

    almacen_uno = Almacen(nombre='L4 Almacén Uno')
    almacen_dos = Almacen(nombre='L4 Almacén Dos')
    db.session.add_all([almacen_uno, almacen_dos])
    db.session.commit()

    root = Users(
        username='l4_root', password=generate_password_hash('rootpass1'),
        rol='admin', almacen_id=almacen_uno.id,
    )
    objetivo = Users(
        username='l4_objetivo', password=generate_password_hash('objetivopass1'),
        rol='admin', almacen_id=almacen_uno.id,
    )
    regular = Users(
        username='l4_regular', password=generate_password_hash('regularpass1'),
        rol='usuario', almacen_id=almacen_uno.id,
    )
    db.session.add_all([root, objetivo, regular])
    db.session.commit()

    return {
        'almacen_uno': almacen_uno.id,
        'almacen_dos': almacen_dos.id,
        'root': root.id,
        'objetivo': objetivo.id,
        'regular': regular.id,
    }


def test_token_admin_revocado_tras_demosion(auth_context, test_client):
    root_token = _login(test_client, 'l4_root', 'rootpass1')
    objetivo_token = _login(test_client, 'l4_objetivo', 'objetivopass1')

    response = test_client.put(
        f"/usuarios/{auth_context['objetivo']}",
        headers=_headers(root_token),
        json={'rol': 'usuario'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Users, auth_context['objetivo']).rol == 'usuario'

    antiguo = test_client.get('/usuarios', headers=_headers(objetivo_token))
    assert antiguo.status_code == 401

    nuevo_token = _login(test_client, 'l4_objetivo', 'objetivopass1')
    vigente = test_client.get('/usuarios', headers=_headers(nuevo_token))
    assert vigente.status_code == 403


def test_token_revocado_tras_cambio_de_password(auth_context, test_client):
    root_token = _login(test_client, 'l4_root', 'rootpass1')
    regular_token = _login(test_client, 'l4_regular', 'regularpass1')

    response = test_client.put(
        f"/usuarios/{auth_context['regular']}",
        headers=_headers(root_token),
        json={'password': 'nuevaclave1'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    antiguo = test_client.get('/usuarios', headers=_headers(regular_token))
    assert antiguo.status_code == 401

    assert test_client.post(
        '/auth', json={'username': 'l4_regular', 'password': 'regularpass1'}
    ).status_code == 401

    nuevo_token = _login(test_client, 'l4_regular', 'nuevaclave1')
    vigente = test_client.get('/usuarios', headers=_headers(nuevo_token))
    assert vigente.status_code == 403


def test_token_revocado_tras_cambio_de_almacen(auth_context, test_client):
    root_token = _login(test_client, 'l4_root', 'rootpass1')
    regular_token = _login(test_client, 'l4_regular', 'regularpass1')

    response = test_client.put(
        f"/usuarios/{auth_context['regular']}",
        headers=_headers(root_token),
        json={'almacen_id': auth_context['almacen_dos']},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Users, auth_context['regular']).almacen_id == auth_context['almacen_dos']

    antiguo = test_client.get('/usuarios', headers=_headers(regular_token))
    assert antiguo.status_code == 401

    nuevo_token = _login(test_client, 'l4_regular', 'regularpass1')
    vigente = test_client.get('/usuarios', headers=_headers(nuevo_token))
    assert vigente.status_code == 403


def test_token_revocado_tras_borrar_usuario(auth_context, test_client):
    root_token = _login(test_client, 'l4_root', 'rootpass1')
    regular_token = _login(test_client, 'l4_regular', 'regularpass1')

    response = test_client.delete(
        f"/usuarios/{auth_context['regular']}", headers=_headers(root_token)
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(Users, auth_context['regular']) is None

    antiguo = test_client.get('/usuarios', headers=_headers(regular_token))
    assert antiguo.status_code == 401


def test_token_legacy_sin_version_se_acepta_solo_con_version_cero(auth_context, test_client):
    regular = db.session.get(Users, auth_context['regular'])
    token_legacy = create_access_token(
        identity=str(regular.id),
        additional_claims={'rol': regular.rol, 'almacen_id': regular.almacen_id},
    )

    assert test_client.post('/telegram/vincular', headers=_headers(token_legacy)).status_code == 200

    root_token = _login(test_client, 'l4_root', 'rootpass1')
    response = test_client.put(
        f"/usuarios/{auth_context['regular']}",
        headers=_headers(root_token),
        json={'password': 'nuevaclave2'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    rechazado = test_client.post('/telegram/vincular', headers=_headers(token_legacy))
    assert rechazado.status_code == 401


def test_token_legacy_con_rol_obsoleto_se_revoca(auth_context, test_client):
    objetivo = db.session.get(Users, auth_context['objetivo'])
    token_legacy = create_access_token(
        identity=str(objetivo.id),
        additional_claims={'rol': 'admin', 'almacen_id': objetivo.almacen_id},
    )

    objetivo.rol = 'usuario'
    db.session.commit()

    response = test_client.get('/usuarios', headers=_headers(token_legacy))
    assert response.status_code == 401


def test_token_legacy_con_almacen_obsoleto_se_revoca(auth_context, test_client):
    regular = db.session.get(Users, auth_context['regular'])
    token_legacy = create_access_token(
        identity=str(regular.id),
        additional_claims={'rol': regular.rol, 'almacen_id': regular.almacen_id},
    )

    regular.almacen_id = auth_context['almacen_dos']
    db.session.commit()

    response = test_client.post('/telegram/vincular', headers=_headers(token_legacy))
    assert response.status_code == 401


def test_claim_admin_falso_no_otorga_acceso(auth_context, test_client):
    regular = db.session.get(Users, auth_context['regular'])
    token_forjado = create_access_token(
        identity=str(regular.id),
        additional_claims={'rol': 'admin'},
    )

    response = test_client.get('/usuarios', headers=_headers(token_forjado))
    assert response.status_code == 401


def test_token_con_username_ajeno_se_revoca(auth_context, test_client):
    regular = db.session.get(Users, auth_context['regular'])
    token = create_access_token(
        identity=str(regular.id),
        additional_claims={'username': 'l4_objetivo'},
    )

    response = test_client.post('/telegram/vincular', headers=_headers(token))
    assert response.status_code == 401


def test_decorador_usa_rol_vigente_de_bd(auth_context, test_client):
    regular = db.session.get(Users, auth_context['regular'])
    token_sin_rol = create_access_token(identity=str(regular.id))

    response = test_client.get('/usuarios', headers=_headers(token_sin_rol))
    assert response.status_code == 403


def test_rol_requerido_propaga_error_de_negocio(auth_context, test_client):
    root_token = _login(test_client, 'l4_root', 'rootpass1')

    @rol_requerido('admin')
    def operacion():
        raise ValueError('fallo de negocio')

    with app.test_request_context('/l4-negocio', headers=_headers(root_token)):
        with pytest.raises(ValueError):
            operacion()


def test_mismo_almacen_propaga_http_exception(auth_context, test_client):
    regular_token = _login(test_client, 'l4_regular', 'regularpass1')

    @mismo_almacen_o_admin
    def operacion():
        raise Forbidden('regla de negocio')

    with app.test_request_context('/l4-negocio', headers=_headers(regular_token)):
        with pytest.raises(Forbidden):
            operacion()
