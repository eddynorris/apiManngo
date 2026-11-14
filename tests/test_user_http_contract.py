"""A19: los endpoints de usuarios no convierten 404/400 en 500."""
import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import Users

ID_INEXISTENTE = 987654


def _limpiar_tablas():
    for tabla in reversed(db.metadata.sorted_tables):
        db.session.execute(tabla.delete())
    db.session.commit()


@pytest.fixture()
def admin_headers(test_client):
    _limpiar_tablas()
    admin = Users(username='a19_admin', password=generate_password_hash('adminpass1'), rol='admin')
    otro = Users(username='a19_otro', password=generate_password_hash('otropass1'), rol='usuario')
    db.session.add_all([admin, otro])
    db.session.commit()
    resp = test_client.post('/auth', json={'username': 'a19_admin', 'password': 'adminpass1'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    token = resp.get_json()['access_token']
    yield {'Authorization': f'Bearer {token}'}, otro.id
    _limpiar_tablas()


@pytest.mark.parametrize('metodo', ['get', 'put', 'delete'])
def test_usuario_inexistente_devuelve_404(test_client, admin_headers, metodo):
    headers, _ = admin_headers
    kwargs = {'headers': headers}
    if metodo == 'put':
        kwargs['json'] = {'rol': 'usuario'}
    resp = getattr(test_client, metodo)(f'/usuarios/{ID_INEXISTENTE}', **kwargs)
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_put_con_datos_invalidos_para_el_schema_devuelve_400_sin_cambios(test_client, admin_headers):
    headers, otro_id = admin_headers
    resp = test_client.put(f'/usuarios/{otro_id}', headers=headers, json={'almacen': 'no-es-un-objeto'})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Users, otro_id).almacen_id is None
