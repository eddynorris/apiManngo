from datetime import datetime, timezone
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import Almacen, Cliente, Movimiento, Pago, Users, Venta, VentaDetalle


def _token(client, username, password):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['access_token']


def _headers(client, username, password):
    return {'Authorization': f'Bearer {_token(client, username, password)}'}


@pytest.fixture(scope='function')
def setup_pagos_auth(test_client):
    db.session.query(Pago).delete()
    db.session.query(Movimiento).delete()
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.query(Cliente).delete()
    db.session.query(Users).delete()
    db.session.query(Almacen).delete()
    db.session.commit()

    almacen1 = Almacen(nombre='Almacén Pagos Uno')
    almacen2 = Almacen(nombre='Almacén Pagos Dos')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    admin = Users(
        username='admin_pagos', password=generate_password_hash('adminpass'),
        rol='admin', almacen_id=almacen1.id,
    )
    user_a = Users(
        username='user_pagos_a', password=generate_password_hash('userpass'),
        rol='usuario', almacen_id=almacen1.id,
    )
    user_b = Users(
        username='user_pagos_b', password=generate_password_hash('userpass'),
        rol='usuario', almacen_id=almacen1.id,
    )
    user_c = Users(
        username='user_pagos_c', password=generate_password_hash('userpass'),
        rol='usuario', almacen_id=almacen2.id,
    )
    db.session.add_all([admin, user_a, user_b, user_c])
    db.session.commit()

    cliente = Cliente(nombre='Cliente Pagos')
    db.session.add(cliente)
    db.session.commit()

    venta_a = Venta(
        cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_a.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='parcial',
        estado='completado', fecha=datetime.now(timezone.utc),
    )
    venta_b = Venta(
        cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_b.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='parcial',
        estado='completado', fecha=datetime.now(timezone.utc),
    )
    venta_c = Venta(
        cliente_id=cliente.id, almacen_id=almacen2.id, vendedor_id=user_c.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='parcial',
        estado='completado', fecha=datetime.now(timezone.utc),
    )
    venta_libre = Venta(
        cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_a.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='pendiente',
        estado='completado', fecha=datetime.now(timezone.utc),
    )
    db.session.add_all([venta_a, venta_b, venta_c, venta_libre])
    db.session.flush()

    pago_a = Pago(
        venta_id=venta_a.id, usuario_id=user_a.id, monto=Decimal('10.00'),
        metodo_pago='efectivo', fecha=datetime.now(timezone.utc),
    )
    pago_b = Pago(
        venta_id=venta_b.id, usuario_id=user_b.id, monto=Decimal('20.00'),
        metodo_pago='efectivo', fecha=datetime.now(timezone.utc),
    )
    pago_c = Pago(
        venta_id=venta_c.id, usuario_id=user_c.id, monto=Decimal('30.00'),
        metodo_pago='efectivo', fecha=datetime.now(timezone.utc),
    )
    db.session.add_all([pago_a, pago_b, pago_c])
    db.session.commit()

    return {
        'almacen1': almacen1, 'almacen2': almacen2,
        'admin': admin, 'user_a': user_a, 'user_b': user_b, 'user_c': user_c,
        'venta_a': venta_a, 'venta_b': venta_b, 'venta_c': venta_c,
        'venta_libre': venta_libre,
        'pago_a': pago_a, 'pago_b': pago_b, 'pago_c': pago_c,
    }


class TestPagoAuthorization:
    def test_get_foreign_payment_same_warehouse_rejected(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.get(
            f"/pagos/{data['pago_b'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 403

    def test_get_foreign_warehouse_payment_rejected(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.get(
            f"/pagos/{data['pago_c'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 403

    def test_get_own_payment_allowed(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.get(
            f"/pagos/{data['pago_a'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 200
        assert Decimal(response.get_json()['monto']) == Decimal('10.00')

    def test_admin_can_read_foreign_payment(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.get(
            f"/pagos/{data['pago_c'].id}",
            headers=_headers(test_client, 'admin_pagos', 'adminpass'),
        )
        assert response.status_code == 200

    def test_list_keeps_owner_filter_for_non_admin(self, test_client, setup_pagos_auth):
        response = test_client.get(
            '/pagos', headers=_headers(test_client, 'user_pagos_a', 'userpass')
        )
        assert response.status_code == 200
        pagos = response.get_json()['data']
        assert pagos
        assert all(pago['usuario_id'] == setup_pagos_auth['user_a'].id for pago in pagos)

    def test_post_payment_foreign_warehouse_rejected_without_effect(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        before = Pago.query.filter_by(venta_id=data['venta_c'].id).count()
        response = test_client.post(
            '/pagos',
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={
                'venta_id': data['venta_c'].id,
                'monto': '10.00',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 403
        assert Pago.query.filter_by(venta_id=data['venta_c'].id).count() == before

    def test_post_payment_same_warehouse_other_seller_allowed(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.post(
            '/pagos',
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={
                'venta_id': data['venta_b'].id,
                'monto': '10.00',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        creado = response.get_json()
        assert creado['usuario_id'] == data['user_a'].id
        assert creado['depositado'] is False
        assert creado['monto_depositado'] is None

    def test_post_payment_ignores_server_only_fields(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.post(
            '/pagos',
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={
                'venta_id': data['venta_libre'].id,
                'monto': '10.00',
                'metodo_pago': 'efectivo',
                'usuario_id': data['admin'].id,
                'depositado': True,
                'monto_depositado': '999.00',
                'fecha_deposito': '2024-01-01T00:00:00',
                'url_comprobante': 'comprobantes/ajeno.jpg',
            },
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        creado = response.get_json()
        assert creado['usuario_id'] == data['user_a'].id
        assert creado['depositado'] is False
        assert creado['monto_depositado'] is None
        assert creado['url_comprobante'] is None

    def test_put_foreign_payment_rejected_without_effect(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.put(
            f"/pagos/{data['pago_b'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={'monto': '99.00'},
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert Decimal(str(db.session.get(Pago, data['pago_b'].id).monto)) == Decimal('20.00')

    def test_put_foreign_warehouse_payment_rejected(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.put(
            f"/pagos/{data['pago_c'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={'monto': '40.00'},
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert Decimal(str(db.session.get(Pago, data['pago_c'].id).monto)) == Decimal('30.00')

    def test_put_own_payment_allowed(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.put(
            f"/pagos/{data['pago_a'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={'monto': '15.00'},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        db.session.expire_all()
        assert Decimal(str(db.session.get(Pago, data['pago_a'].id).monto)) == Decimal('15.00')

    def test_put_cannot_move_payment_to_another_sale(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.put(
            f"/pagos/{data['pago_a'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={'monto': '10.00', 'venta_id': data['venta_b'].id},
        )
        assert response.status_code == 400
        db.session.expire_all()
        assert db.session.get(Pago, data['pago_a'].id).venta_id == data['venta_a'].id

    def test_delete_foreign_payment_rejected(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.delete(
            f"/pagos/{data['pago_b'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert db.session.get(Pago, data['pago_b'].id) is not None

    def test_delete_own_payment_allowed(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.delete(
            f"/pagos/{data['pago_a'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.get(Pago, data['pago_a'].id) is None

    def test_pagos_por_venta_does_not_leak_foreign_owner(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.get(
            f"/pagos/venta/{data['venta_b'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 200
        assert response.get_json() == []

    def test_pagos_por_venta_foreign_warehouse_rejected(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.get(
            f"/pagos/venta/{data['venta_c'].id}",
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
        )
        assert response.status_code == 403

    def test_batch_foreign_warehouse_rejected_atomically(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        before_b = Pago.query.filter_by(venta_id=data['venta_b'].id).count()
        response = test_client.post(
            '/pagos/batch',
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={
                'pagos': [
                    {'venta_id': data['venta_b'].id, 'monto': '5.00'},
                    {'venta_id': data['venta_c'].id, 'monto': '5.00'},
                ],
                'fecha': '2024-01-15T10:30:00',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 403
        assert Pago.query.filter_by(venta_id=data['venta_b'].id).count() == before_b

    def test_batch_same_warehouse_uses_actor_as_owner(self, test_client, setup_pagos_auth):
        data = setup_pagos_auth
        response = test_client.post(
            '/pagos/batch',
            headers=_headers(test_client, 'user_pagos_a', 'userpass'),
            json={
                'pagos': [{'venta_id': data['venta_b'].id, 'monto': '5.00'}],
                'fecha': '2024-01-15T10:30:00',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        creados = response.get_json()['pagos_creados']
        assert len(creados) == 1
        assert creados[0]['usuario_id'] == data['user_a'].id
