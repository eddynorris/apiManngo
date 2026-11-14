from datetime import UTC, datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import (
    Almacen,
    Cliente,
    Inventario,
    Lote,
    Movimiento,
    Pago,
    PresentacionProducto,
    Producto,
    Users,
    Venta,
    VentaDetalle,
)


def _headers(client, username='admin_val', password='adminpass'):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return {'Authorization': f"Bearer {response.get_json()['access_token']}"}


@pytest.fixture(scope='function')
def setup_pagos_validation(test_client):
    db.session.query(Pago).delete()
    db.session.query(Movimiento).delete()
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Cliente).delete()
    db.session.query(Users).delete()
    db.session.query(Almacen).delete()
    db.session.commit()

    almacen = Almacen(nombre='Almacén Validación')
    db.session.add(almacen)
    db.session.commit()

    admin = Users(
        username='admin_val', password=generate_password_hash('adminpass'),
        rol='admin', almacen_id=almacen.id,
    )
    db.session.add(admin)
    db.session.commit()

    cliente = Cliente(nombre='Cliente Validación')
    producto = Producto(nombre='Producto Validación', precio_compra=Decimal('5.00'))
    db.session.add_all([cliente, producto])
    db.session.commit()

    presentacion = PresentacionProducto(
        producto_id=producto.id, nombre='Presentación Validación',
        capacidad_kg=Decimal('1.00'), tipo='procesado',
        precio_venta=Decimal('10.00'), activo=True,
    )
    lote = Lote(
        producto_id=producto.id, descripcion='Lote Validación',
        cantidad_disponible_kg=Decimal('100.00'),
        fecha_ingreso=datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.session.add_all([presentacion, lote])
    db.session.flush()

    inventario = Inventario(
        presentacion_id=presentacion.id, almacen_id=almacen.id,
        lote_id=lote.id, cantidad=Decimal('20'),
    )
    db.session.add(inventario)

    venta = Venta(
        cliente_id=cliente.id, almacen_id=almacen.id, vendedor_id=admin.id,
        total=Decimal('100.00'), tipo_pago='credito', estado_pago='pendiente',
        estado='completado', fecha=datetime.now(UTC),
    )
    venta_parcial = Venta(
        cliente_id=cliente.id, almacen_id=almacen.id, vendedor_id=admin.id,
        total=Decimal('200.00'), tipo_pago='credito', estado_pago='parcial',
        estado='completado', fecha=datetime.now(UTC),
    )
    db.session.add_all([venta, venta_parcial])
    db.session.flush()

    pago_parcial = Pago(
        venta_id=venta_parcial.id, usuario_id=admin.id, monto=Decimal('50.00'),
        metodo_pago='efectivo', fecha=datetime.now(UTC),
    )
    pago_depositado = Pago(
        venta_id=venta_parcial.id, usuario_id=admin.id, monto=Decimal('100.00'),
        metodo_pago='deposito', fecha=datetime.now(UTC),
        depositado=True, monto_depositado=Decimal('80.00'),
        fecha_deposito=datetime(2024, 1, 10, tzinfo=UTC),
    )
    db.session.add_all([pago_parcial, pago_depositado])
    db.session.commit()

    return {
        'almacen': almacen, 'admin': admin, 'cliente': cliente,
        'presentacion': presentacion, 'lote': lote, 'inventario': inventario,
        'venta': venta, 'venta_parcial': venta_parcial,
        'pago_parcial': pago_parcial, 'pago_depositado': pago_depositado,
    }


class TestPagoAmountValidation:
    @pytest.mark.parametrize('monto', [
        '-1', '0', 'abc', 'NaN', 'Infinity', '-Infinity', '1e10000',
    ])
    def test_create_payment_rejects_invalid_amounts(self, test_client, setup_pagos_validation, monto):
        data = setup_pagos_validation
        before = Pago.query.filter_by(venta_id=data['venta'].id).count()
        response = test_client.post(
            '/pagos', headers=_headers(test_client),
            json={
                'venta_id': data['venta'].id,
                'monto': monto,
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 400, response.get_data(as_text=True)
        assert Pago.query.filter_by(venta_id=data['venta'].id).count() == before

    def test_create_payment_rejects_overpayment(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.post(
            '/pagos', headers=_headers(test_client),
            json={
                'venta_id': data['venta'].id,
                'monto': '100.01',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 400
        assert Pago.query.filter_by(venta_id=data['venta'].id).count() == 0

    def test_create_payment_accepts_exact_balance(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.post(
            '/pagos', headers=_headers(test_client),
            json={
                'venta_id': data['venta'].id,
                'monto': '100.00',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        assert Decimal(response.get_json()['monto']) == Decimal('100.00')

    def test_multipart_payment_rejects_negative_amount(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.post(
            '/pagos', headers=_headers(test_client),
            data={
                'venta_id': str(data['venta'].id),
                'monto': '-10.00',
                'metodo_pago': 'efectivo',
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        assert Pago.query.filter_by(venta_id=data['venta'].id).count() == 0

    def test_update_payment_rejects_overpayment(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.put(
            f"/pagos/{data['pago_parcial'].id}", headers=_headers(test_client),
            json={'monto': '151.00'},
        )
        assert response.status_code == 400
        db.session.expire_all()
        assert Decimal(str(db.session.get(Pago, data['pago_parcial'].id).monto)) == Decimal('50.00')

    def test_update_payment_rejects_negative_amount(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.put(
            f"/pagos/{data['pago_parcial'].id}", headers=_headers(test_client),
            json={'monto': '-1.00'},
        )
        assert response.status_code == 400
        db.session.expire_all()
        assert Decimal(str(db.session.get(Pago, data['pago_parcial'].id).monto)) == Decimal('50.00')

    def test_update_payment_rejects_amount_below_deposited(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.put(
            f"/pagos/{data['pago_depositado'].id}", headers=_headers(test_client),
            json={'monto': '50.00'},
        )
        assert response.status_code == 409
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_depositado'].id)
        assert Decimal(str(pago.monto)) == Decimal('100.00')
        assert Decimal(str(pago.monto_depositado)) == Decimal('80.00')

    def test_batch_rejects_invalid_amounts(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        for monto in ('-1', 'NaN', 'Infinity'):
            response = test_client.post(
                '/pagos/batch', headers=_headers(test_client),
                json={
                    'pagos': [{'venta_id': data['venta'].id, 'monto': monto}],
                    'fecha': '2024-01-15T10:30:00',
                    'metodo_pago': 'efectivo',
                },
            )
            assert response.status_code == 400, (monto, response.get_data(as_text=True))
        assert Pago.query.filter_by(venta_id=data['venta'].id).count() == 0

    def test_batch_rejects_aggregate_over_total(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.post(
            '/pagos/batch', headers=_headers(test_client),
            json={
                'pagos': [
                    {'venta_id': data['venta'].id, 'monto': '60.00'},
                    {'venta_id': data['venta'].id, 'monto': '50.00'},
                ],
                'fecha': '2024-01-15T10:30:00',
                'metodo_pago': 'efectivo',
            },
        )
        assert response.status_code == 400
        assert Pago.query.filter_by(venta_id=data['venta'].id).count() == 0


class TestDepositoAmountValidation:
    def _deposito(self, test_client, data, depositos):
        return test_client.post(
            '/pagos/depositos', headers=_headers(test_client),
            json={
                'depositos': depositos,
                'fecha_deposito': '2024-01-15T10:00:00',
            },
        )

    def test_deposit_rejects_over_available(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = self._deposito(test_client, data, [
            {'pago_id': data['pago_depositado'].id, 'monto_depositado': '30.00'},
        ])
        assert response.status_code == 400
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_depositado'].id)
        assert Decimal(str(pago.monto_depositado)) == Decimal('80.00')

    def test_deposit_accepts_available_remainder(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = self._deposito(test_client, data, [
            {'pago_id': data['pago_depositado'].id, 'monto_depositado': '20.00'},
        ])
        assert response.status_code == 200, response.get_data(as_text=True)
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_depositado'].id)
        assert Decimal(str(pago.monto_depositado)) == Decimal('100.00')

    def test_deposit_rejects_duplicate_payment_ids(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = self._deposito(test_client, data, [
            {'pago_id': data['pago_depositado'].id, 'monto_depositado': '10.00'},
            {'pago_id': data['pago_depositado'].id, 'monto_depositado': '10.00'},
        ])
        assert response.status_code == 400
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_depositado'].id)
        assert Decimal(str(pago.monto_depositado)) == Decimal('80.00')

    @pytest.mark.parametrize('monto', ['-1', '0', 'NaN', 'Infinity', 'abc'])
    def test_deposit_rejects_invalid_amounts(self, test_client, setup_pagos_validation, monto):
        data = setup_pagos_validation
        response = self._deposito(test_client, data, [
            {'pago_id': data['pago_depositado'].id, 'monto_depositado': monto},
        ])
        assert response.status_code == 400
        db.session.expire_all()
        pago = db.session.get(Pago, data['pago_depositado'].id)
        assert Decimal(str(pago.monto_depositado)) == Decimal('80.00')


class TestTransaccionPaymentValidation:
    def _payload(self, data, pagos):
        return {
            'cliente': {'id': data['cliente'].id},
            'items': [{
                'producto_id': data['presentacion'].id,
                'lote_id': data['lote'].id,
                'cantidad': 1,
                'precio_unitario': '10.00',
            }],
            'pagos': pagos,
        }

    def test_transaccion_rejects_negative_initial_payment_without_stock_change(
        self, test_client, setup_pagos_validation
    ):
        data = setup_pagos_validation
        before = Decimal(str(db.session.get(Inventario, data['inventario'].id).cantidad))
        ventas_before = Venta.query.count()
        response = test_client.post(
            '/transacciones/venta-completa', headers=_headers(test_client),
            json=self._payload(data, [{'monto': '-1.00', 'metodo_pago': 'efectivo'}]),
        )
        assert response.status_code == 400
        db.session.expire_all()
        assert Decimal(str(db.session.get(Inventario, data['inventario'].id).cantidad)) == before
        assert Venta.query.count() == ventas_before

    def test_transaccion_rejects_overpayment_without_stock_change(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        before = Decimal(str(db.session.get(Inventario, data['inventario'].id).cantidad))
        ventas_before = Venta.query.count()
        response = test_client.post(
            '/transacciones/venta-completa', headers=_headers(test_client),
            json=self._payload(data, [{'monto': '20.00', 'metodo_pago': 'efectivo'}]),
        )
        assert response.status_code == 400
        db.session.expire_all()
        assert Decimal(str(db.session.get(Inventario, data['inventario'].id).cantidad)) == before
        assert Venta.query.count() == ventas_before

    def test_transaccion_accepts_valid_initial_payment(self, test_client, setup_pagos_validation):
        data = setup_pagos_validation
        response = test_client.post(
            '/transacciones/venta-completa', headers=_headers(test_client),
            json=self._payload(data, [{'monto': '10.00', 'metodo_pago': 'efectivo'}]),
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        assert Decimal(response.get_json()['total_pagado']) == Decimal('10.00')
