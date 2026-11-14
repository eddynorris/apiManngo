import io
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from werkzeug.exceptions import BadRequest
from werkzeug.security import generate_password_hash

from extensions import db
from models import (
    Almacen,
    Cliente,
    Inventario,
    Lote,
    Movimiento,
    Pago,
    Pedido,
    PedidoDetalle,
    PresentacionProducto,
    Producto,
    Users,
    Venta,
    VentaDetalle,
)
from services.authorization import (
    _convertir_id,
    autorizar_almacen,
    normalizar_ids,
    normalizar_lista_ids,
)


def get_auth_token(client, username, password):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['access_token']


def auth_headers(client, username, password):
    return {'Authorization': f'Bearer {get_auth_token(client, username, password)}'}


@pytest.fixture(scope='function')
def setup_venta_auth(test_client):
    db.session.query(Pago).delete()
    db.session.query(Movimiento).delete()
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.query(PedidoDetalle).delete()
    db.session.query(Pedido).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Producto).delete()
    db.session.query(Lote).delete()
    db.session.query(Cliente).delete()
    db.session.query(Users).delete()
    db.session.query(Almacen).delete()
    db.session.commit()

    almacen1 = Almacen(nombre='Almacén Uno')
    almacen2 = Almacen(nombre='Almacén Dos')
    db.session.add_all([almacen1, almacen2])
    db.session.commit()

    admin = Users(username='admin_auth', password=generate_password_hash('adminpass'), rol='admin', almacen_id=almacen1.id)
    user_a = Users(username='usuario_a', password=generate_password_hash('userpass'), rol='usuario', almacen_id=almacen1.id)
    user_b = Users(username='usuario_b', password=generate_password_hash('userpass'), rol='usuario', almacen_id=almacen1.id)
    user_c = Users(username='usuario_c', password=generate_password_hash('userpass'), rol='usuario', almacen_id=almacen2.id)
    db.session.add_all([admin, user_a, user_b, user_c])
    db.session.commit()

    cliente = Cliente(nombre='Cliente Autorización')
    producto = Producto(nombre='Producto Autorización', precio_compra=Decimal('5.00'))
    db.session.add_all([cliente, producto])
    db.session.commit()

    presentacion = PresentacionProducto(
        producto_id=producto.id, nombre='Bolsa Autorización', capacidad_kg=Decimal('1.00'),
        tipo='procesado', precio_venta=Decimal('50.00'), activo=True,
    )
    lote1 = Lote(producto_id=producto.id, descripcion='Lote Uno',
                 cantidad_disponible_kg=Decimal('100.00'), fecha_ingreso=datetime(2024, 1, 1, tzinfo=UTC))
    lote2 = Lote(producto_id=producto.id, descripcion='Lote Dos',
                 cantidad_disponible_kg=Decimal('100.00'), fecha_ingreso=datetime(2024, 1, 2, tzinfo=UTC))
    db.session.add_all([presentacion, lote1, lote2])
    db.session.commit()

    inv1 = Inventario(presentacion_id=presentacion.id, almacen_id=almacen1.id, lote_id=lote1.id, cantidad=Decimal('50'))
    inv2 = Inventario(presentacion_id=presentacion.id, almacen_id=almacen2.id, lote_id=lote2.id, cantidad=Decimal('50'))
    db.session.add_all([inv1, inv2])
    db.session.commit()

    venta_a = Venta(cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_a.id,
                    total=Decimal('100.00'), tipo_pago='contado', estado_pago='pendiente', estado='completado',
                    fecha=datetime.now(UTC))
    venta_b = Venta(cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_b.id,
                    total=Decimal('100.00'), tipo_pago='contado', estado_pago='parcial', estado='completado',
                    fecha=datetime.now(UTC))
    venta_c = Venta(cliente_id=cliente.id, almacen_id=almacen2.id, vendedor_id=user_c.id,
                    total=Decimal('100.00'), tipo_pago='contado', estado_pago='parcial', estado='completado',
                    fecha=datetime.now(UTC))
    db.session.add_all([venta_a, venta_b, venta_c])
    db.session.flush()

    db.session.add_all([
        VentaDetalle(venta_id=venta_a.id, presentacion_id=presentacion.id, lote_id=lote1.id,
                     cantidad=Decimal('2'), precio_unitario=Decimal('50.00')),
        VentaDetalle(venta_id=venta_b.id, presentacion_id=presentacion.id, lote_id=lote1.id,
                     cantidad=Decimal('1'), precio_unitario=Decimal('100.00')),
        VentaDetalle(venta_id=venta_c.id, presentacion_id=presentacion.id, lote_id=lote2.id,
                     cantidad=Decimal('1'), precio_unitario=Decimal('100.00')),
    ])
    db.session.add_all([
        Pago(venta_id=venta_b.id, monto=Decimal('30.00'), metodo_pago='efectivo', usuario_id=user_b.id),
        Pago(venta_id=venta_c.id, monto=Decimal('10.00'), metodo_pago='efectivo', usuario_id=user_c.id),
    ])

    pedido_b = Pedido(cliente_id=cliente.id, almacen_id=almacen1.id, vendedor_id=user_b.id,
                      fecha_creacion=datetime.now(UTC), fecha_entrega=datetime.now(UTC),
                      estado='programado', notas='Pedido de B')
    pedido_c = Pedido(cliente_id=cliente.id, almacen_id=almacen2.id, vendedor_id=user_c.id,
                      fecha_creacion=datetime.now(UTC), fecha_entrega=datetime.now(UTC),
                      estado='programado', notas='Pedido de C')
    db.session.add_all([pedido_b, pedido_c])
    db.session.flush()
    db.session.add_all([
        PedidoDetalle(pedido_id=pedido_b.id, presentacion_id=presentacion.id, cantidad=1, precio_estimado=Decimal('50.00')),
        PedidoDetalle(pedido_id=pedido_c.id, presentacion_id=presentacion.id, cantidad=1, precio_estimado=Decimal('50.00')),
    ])
    db.session.commit()

    return {
        'almacen1': almacen1, 'almacen2': almacen2,
        'admin': admin, 'user_a': user_a, 'user_b': user_b, 'user_c': user_c,
        'cliente': cliente, 'presentacion': presentacion,
        'lote1': lote1, 'lote2': lote2, 'inv1': inv1, 'inv2': inv2,
        'venta_a': venta_a, 'venta_b': venta_b, 'venta_c': venta_c,
        'pedido_b': pedido_b, 'pedido_c': pedido_c,
    }


def _detalles(data, cantidad='1'):
    return [{
        'presentacion_id': data['presentacion'].id,
        'cantidad': cantidad,
        'precio_unitario': '50.00',
    }]


class TestVentaAuthorization:
    def test_get_foreign_sale_same_warehouse_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.get(f"/ventas/{data['venta_b'].id}", headers=headers)
        assert response.status_code == 403

    def test_put_foreign_sale_same_warehouse_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        inv_before = Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))
        pagos_before = Pago.query.filter_by(venta_id=data['venta_b'].id).count()

        response = test_client.put(
            f"/ventas/{data['venta_b'].id}", headers=headers,
            json={'detalles': _detalles(data, '5'), 'almacen_id': data['almacen1'].id},
        )
        assert response.status_code == 403

        db.session.expire_all()
        venta_b = db.session.get(Venta, data['venta_b'].id)
        assert venta_b is not None
        assert Decimal(str(venta_b.total)) == Decimal('100.00')
        assert len(venta_b.detalles) == 1
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == inv_before
        assert Pago.query.filter_by(venta_id=venta_b.id).count() == pagos_before

    def test_delete_foreign_sale_same_warehouse_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        inv_before = Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))
        pagos_before = Pago.query.filter_by(venta_id=data['venta_b'].id).count()

        response = test_client.delete(f"/ventas/{data['venta_b'].id}", headers=headers)
        assert response.status_code == 403

        db.session.expire_all()
        assert db.session.get(Venta, data['venta_b'].id) is not None
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == inv_before
        assert Pago.query.filter_by(venta_id=data['venta_b'].id).count() == pagos_before

    def test_put_foreign_warehouse_sale_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.put(
            f"/ventas/{data['venta_c'].id}", headers=headers,
            json={'detalles': _detalles(data)},
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert Decimal(str(db.session.get(Venta, data['venta_c'].id).total)) == Decimal('100.00')

    def test_delete_foreign_warehouse_sale_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.delete(f"/ventas/{data['venta_c'].id}", headers=headers)
        assert response.status_code == 403
        db.session.expire_all()
        assert db.session.get(Venta, data['venta_c'].id) is not None

    def test_change_destination_non_admin_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.put(
            f"/ventas/{data['venta_a'].id}", headers=headers,
            json={'detalles': _detalles(data), 'almacen_id': data['almacen2'].id},
        )
        assert response.status_code == 403

        db.session.expire_all()
        venta = db.session.get(Venta, data['venta_a'].id)
        assert venta.almacen_id == data['almacen1'].id
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == Decimal('50')
        assert Decimal(str(db.session.get(Inventario, data['inv2'].id).cantidad)) == Decimal('50')

    def test_admin_can_delete_foreign_sale(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'admin_auth', 'adminpass')
        inv_before = Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))
        response = test_client.delete(f"/ventas/{data['venta_b'].id}", headers=headers)
        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.get(Venta, data['venta_b'].id) is None
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == inv_before + Decimal('1')

    def test_admin_change_destination_uses_new_warehouse_stock(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'admin_auth', 'adminpass')
        response = test_client.put(
            f"/ventas/{data['venta_a'].id}", headers=headers,
            json={'detalles': _detalles(data, '1'), 'almacen_id': data['almacen2'].id},
        )
        assert response.status_code == 200, response.get_data(as_text=True)

        db.session.expire_all()
        venta = db.session.get(Venta, data['venta_a'].id)
        assert venta.almacen_id == data['almacen2'].id
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == Decimal('52')
        assert Decimal(str(db.session.get(Inventario, data['inv2'].id).cantidad)) == Decimal('49')

    def test_batch_mixed_authorization_rejects_whole_batch(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        inv_before = Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))
        response = test_client.delete(
            '/ventas', headers=headers,
            json={'ids': [data['venta_a'].id, str(data['venta_b'].id)]},
        )
        assert response.status_code == 403

        db.session.expire_all()
        assert db.session.get(Venta, data['venta_a'].id) is not None
        assert db.session.get(Venta, data['venta_b'].id) is not None
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == inv_before

    def test_batch_own_sale_accepts_mixed_id_types(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        extra = Venta(cliente_id=data['cliente'].id, almacen_id=data['almacen1'].id,
                      vendedor_id=data['user_a'].id, total=Decimal('50.00'),
                      tipo_pago='contado', estado_pago='pendiente', estado='completado')
        db.session.add(extra)
        db.session.commit()

        response = test_client.delete(
            '/ventas', headers=headers,
            json={'ids': [str(data['venta_a'].id), extra.id]},
        )
        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.get(Venta, data['venta_a'].id) is None
        assert db.session.get(Venta, extra.id) is None

    def test_batch_missing_id_rejected_before_mutation(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.delete(
            '/ventas', headers=headers,
            json={'ids': [data['venta_a'].id, 999999]},
        )
        assert response.status_code == 404
        db.session.expire_all()
        assert db.session.get(Venta, data['venta_a'].id) is not None

    def test_missing_sale_returns_404_not_500(self, test_client, setup_venta_auth):
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        assert test_client.get('/ventas/999999', headers=headers).status_code == 404
        assert test_client.delete('/ventas/999999', headers=headers).status_code == 404


class TestRequestFormatsAuthorization:
    def test_json_cross_warehouse_create_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        before = Venta.query.count()
        response = test_client.post('/ventas', headers=headers, json={
            'cliente_id': data['cliente'].id,
            'almacen_id': data['almacen2'].id,
            'detalles': _detalles(data),
        })
        assert response.status_code == 403
        db.session.expire_all()
        assert Venta.query.count() == before
        assert Decimal(str(db.session.get(Inventario, data['inv2'].id).cantidad)) == Decimal('50')

    def test_multipart_cross_warehouse_create_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        before = Venta.query.count()
        response = test_client.post(
            '/ventas', headers=headers, content_type='multipart/form-data',
            data={
                'cliente_id': str(data['cliente'].id),
                'almacen_id': str(data['almacen2'].id),
                'detalles': json.dumps(_detalles(data)),
                'comprobante': (io.BytesIO(b'no-es-un-comprobante-real'), 'comprobante.pdf'),
            },
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert Venta.query.count() == before
        assert Decimal(str(db.session.get(Inventario, data['inv2'].id).cantidad)) == Decimal('50')

    def test_multipart_same_warehouse_create_allowed(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.post(
            '/ventas', headers=headers, content_type='multipart/form-data',
            data={
                'cliente_id': str(data['cliente'].id),
                'almacen_id': str(data['almacen1'].id),
                'detalles': json.dumps(_detalles(data)),
                'comprobante': (io.BytesIO(b'no-es-un-comprobante-real'), 'comprobante.pdf'),
            },
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        creada = response.get_json()
        assert creada['almacen']['id'] == data['almacen1'].id
        assert creada['vendedor']['id'] == data['user_a'].id

    def test_json_string_ids_are_normalized(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.post('/ventas', headers=headers, json={
            'cliente_id': str(data['cliente'].id),
            'almacen_id': str(data['almacen1'].id),
            'detalles': [{
                'presentacion_id': str(data['presentacion'].id),
                'cantidad': '1',
                'precio_unitario': '50.00',
            }],
        })
        assert response.status_code == 201, response.get_data(as_text=True)
        db.session.expire_all()
        venta = db.session.get(Venta, response.get_json()['id'])
        assert venta.almacen_id == data['almacen1'].id
        assert venta.cliente_id == data['cliente'].id

    def test_put_string_ids_are_normalized(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.put(
            f"/ventas/{data['venta_a'].id}", headers=headers,
            json={
                'cliente_id': str(data['cliente'].id),
                'almacen_id': str(data['almacen1'].id),
                'detalles': [{
                    'presentacion_id': str(data['presentacion'].id),
                    'cantidad': '3',
                    'precio_unitario': '50.00',
                }],
            },
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        db.session.expire_all()
        venta = db.session.get(Venta, data['venta_a'].id)
        assert Decimal(str(venta.total)) == Decimal('150.00')


class TestPedidoAndDetalleAuthorization:
    def test_pedido_get_foreign_same_warehouse_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        assert test_client.get(f"/pedidos/{data['pedido_b'].id}", headers=headers).status_code == 403

    def test_pedido_put_foreign_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.put(
            f"/pedidos/{data['pedido_b'].id}", headers=headers,
            json={'notas': 'intento ajeno', 'estado': 'confirmado'},
        )
        assert response.status_code == 403
        db.session.expire_all()
        pedido = db.session.get(Pedido, data['pedido_b'].id)
        assert pedido.notas == 'Pedido de B'
        assert pedido.estado == 'programado'

    def test_pedido_delete_foreign_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.delete(f"/pedidos/{data['pedido_b'].id}", headers=headers)
        assert response.status_code == 403
        db.session.expire_all()
        assert db.session.get(Pedido, data['pedido_b'].id) is not None

    def test_pedido_conversion_foreign_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        ventas_before = Venta.query.count()
        inv_before = Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))
        response = test_client.post(
            f"/pedidos/{data['pedido_b'].id}/convertir", headers=headers,
            json={'tipo_pago': 'contado'},
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert Venta.query.count() == ventas_before
        assert db.session.get(Pedido, data['pedido_b'].id).estado == 'programado'
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == inv_before

    def test_pedido_list_hides_foreign_records(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.get('/pedidos?per_page=50', headers=headers)
        assert response.status_code == 200
        ids = {p['id'] for p in response.get_json()['data']}
        assert data['pedido_b'].id not in ids
        assert data['pedido_c'].id not in ids

    def test_venta_detalle_get_foreign_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        response = test_client.get(f"/ventas/{data['venta_b'].id}/detalles", headers=headers)
        assert response.status_code == 403

    def test_venta_detalle_post_foreign_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalles_before = VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).count()
        inv_before = Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))
        response = test_client.post(
            f"/ventas/{data['venta_b'].id}/detalles", headers=headers,
            json={'presentacion_id': data['presentacion'].id, 'cantidad': 1},
        )
        assert response.status_code == 403
        db.session.expire_all()
        assert VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).count() == detalles_before
        assert Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad)) == inv_before


class TestStrictIdParsing:
    """Un ID solo puede ser entero positivo auténtico o string de dígitos."""

    @pytest.mark.parametrize('valor', [
        True, False,
        1.0, 1.9, -1.0, 0.0,
        Decimal('1'), Decimal('1.0'),
        0, -1,
        '', ' ', ' 1', '1 ', '+1', '-1', '1.0', '1,0', 'abc', '0', '01.0',
        None, [], {},
    ])
    def test_valores_invalidos_rechazados(self, valor):
        with pytest.raises(BadRequest):
            _convertir_id(valor)

    @pytest.mark.parametrize('valor, esperado', [
        (1, 1), (25, 25), ('1', 1), ('25', 25), ('01', 1),
    ])
    def test_ids_enteros_validos(self, valor, esperado):
        assert _convertir_id(valor) == esperado

    def test_normalizar_ids_no_repara_floats(self):
        with pytest.raises(BadRequest):
            normalizar_ids({'almacen_id': 1.0})
        with pytest.raises(BadRequest):
            normalizar_ids({'almacen_id': 1.9})

    def test_normalizar_lista_rechaza_floats_bool_y_cero(self):
        for valor in (True, 1.0, 1.9, 0, -1, '0', '-1', '1.0', ' 1'):
            with pytest.raises(BadRequest):
                normalizar_lista_ids([valor])

    def test_normalizar_lista_acepta_strings_enteros(self):
        assert normalizar_lista_ids(['1', 2, '02']) == [1, 2]

    def test_autorizar_almacen_admin_valida_destino_antes_del_atajo(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        with pytest.raises(BadRequest):
            autorizar_almacen(data['admin'].id, 1.9)
        with pytest.raises(BadRequest):
            autorizar_almacen(data['admin'].id, 1.0)
        assert autorizar_almacen(data['admin'].id, str(data['almacen2'].id)).id == data['admin'].id

    def test_autorizar_almacen_usuario_rechaza_destino_invalido(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        with pytest.raises(BadRequest):
            autorizar_almacen(data['user_a'].id, 1.0)
