from datetime import datetime, timezone
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import Almacen, Cliente, Pago, Users, Venta


def _headers(client, username, password):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return {'Authorization': f"Bearer {response.get_json()['access_token']}"}


@pytest.fixture(scope='function')
def setup_depositos_auth(test_client):
    db.session.query(Pago).delete()
    db.session.query(Venta).delete()
    db.session.query(Cliente).delete()
    db.session.query(Users).delete()
    db.session.query(Almacen).delete()
    db.session.commit()

    almacen1 = Almacen(nombre='Almacén Depósitos Uno')
    almacen2 = Almacen(nombre='Almacén Depósitos Dos')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    admin = Users(
        username='admin_dep', password=generate_password_hash('adminpass'),
        rol='admin', almacen_id=almacen1.id,
    )
    user_a = Users(
        username='user_dep_a', password=generate_password_hash('userpass'),
        rol='usuario', almacen_id=almacen1.id,
    )
    user_b = Users(
        username='user_dep_b', password=generate_password_hash('userpass'),
        rol='usuario', almacen_id=almacen1.id,
    )
    user_c = Users(
        username='user_dep_c', password=generate_password_hash('userpass'),
        rol='usuario', almacen_id=almacen2.id,
    )
    db.session.add_all([admin, user_a, user_b, user_c])
    db.session.commit()

    cliente = Cliente(nombre='Cliente Depósitos')
    db.session.add(cliente)
    db.session.commit()

    venta1 = Venta(
        cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_a.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='parcial',
        estado='completado', fecha=datetime.now(timezone.utc),
    )
    venta2 = Venta(
        cliente_id=cliente.id, almacen_id=almacen2.id, vendedor_id=user_c.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='parcial',
        estado='completado', fecha=datetime.now(timezone.utc),
    )
    db.session.add_all([venta1, venta2])
    db.session.flush()

    pago_ajeno_mismo_almacen = Pago(
        venta_id=venta1.id, usuario_id=user_b.id, monto=Decimal('50.00'),
        metodo_pago='efectivo', fecha=datetime.now(timezone.utc),
    )
    pago_propio = Pago(
        venta_id=venta1.id, usuario_id=user_a.id, monto=Decimal('50.00'),
        metodo_pago='efectivo', fecha=datetime.now(timezone.utc),
    )
    pago_otro_almacen = Pago(
        venta_id=venta2.id, usuario_id=user_c.id, monto=Decimal('50.00'),
        metodo_pago='efectivo', fecha=datetime.now(timezone.utc),
    )
    db.session.add_all([pago_ajeno_mismo_almacen, pago_propio, pago_otro_almacen])
    db.session.commit()

    return {
        'admin': admin, 'user_a': user_a, 'user_b': user_b, 'user_c': user_c,
        'pago_ajeno_mismo_almacen': pago_ajeno_mismo_almacen,
        'pago_propio': pago_propio,
        'pago_otro_almacen': pago_otro_almacen,
    }


def _depositar(test_client, headers, pagos):
    return test_client.post(
        '/pagos/depositos', headers=headers,
        json={'depositos': pagos, 'fecha_deposito': '2024-01-15T10:00:00'},
    )


class TestDepositoAuthorization:
    def test_foreign_warehouse_payment_rejected(self, test_client, setup_depositos_auth):
        data = setup_depositos_auth
        response = _depositar(
            test_client, _headers(test_client, 'user_dep_a', 'userpass'),
            [{'pago_id': data['pago_otro_almacen'].id, 'monto_depositado': '10.00'}],
        )
        assert response.status_code == 403
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_otro_almacen'].id)
        assert pago.depositado is False
        assert pago.monto_depositado is None

    def test_foreign_owner_same_warehouse_rejected(self, test_client, setup_depositos_auth):
        data = setup_depositos_auth
        response = _depositar(
            test_client, _headers(test_client, 'user_dep_a', 'userpass'),
            [{'pago_id': data['pago_ajeno_mismo_almacen'].id, 'monto_depositado': '10.00'}],
        )
        assert response.status_code == 403
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_ajeno_mismo_almacen'].id)
        assert pago.depositado is False
        assert pago.monto_depositado is None

    def test_own_payment_allowed(self, test_client, setup_depositos_auth):
        data = setup_depositos_auth
        response = _depositar(
            test_client, _headers(test_client, 'user_dep_a', 'userpass'),
            [{'pago_id': data['pago_propio'].id, 'monto_depositado': '10.00'}],
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_propio'].id)
        assert pago.depositado is True
        assert Decimal(str(pago.monto_depositado)) == Decimal('10.00')

    def test_admin_can_deposit_foreign_payment(self, test_client, setup_depositos_auth):
        data = setup_depositos_auth
        response = _depositar(
            test_client, _headers(test_client, 'admin_dep', 'adminpass'),
            [{'pago_id': data['pago_ajeno_mismo_almacen'].id, 'monto_depositado': '10.00'}],
        )
        assert response.status_code == 200, response.get_data(as_text=True)

    def test_mixed_batch_does_not_partially_apply(self, test_client, setup_depositos_auth):
        data = setup_depositos_auth
        response = _depositar(
            test_client, _headers(test_client, 'user_dep_a', 'userpass'),
            [
                {'pago_id': data['pago_propio'].id, 'monto_depositado': '10.00'},
                {'pago_id': data['pago_ajeno_mismo_almacen'].id, 'monto_depositado': '10.00'},
            ],
        )
        assert response.status_code == 403
        db.session.expire_all()
        propio = db.session.get(Pago, data['pago_propio'].id)
        ajeno = db.session.get(Pago, data['pago_ajeno_mismo_almacen'].id)
        assert propio.depositado is False
        assert propio.monto_depositado is None
        assert ajeno.depositado is False
        assert ajeno.monto_depositado is None
