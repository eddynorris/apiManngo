from decimal import Decimal

from extensions import db
from models import Inventario, Pago, Venta, VentaDetalle
from tests.test_venta_authorization import (  # noqa: F401  (fixture de pytest)
    auth_headers,
    setup_venta_auth,
)


def _detalle_payload(data, cantidad='1', precio='50.00'):
    return {
        'presentacion_id': data['presentacion'].id,
        'cantidad': cantidad,
        'precio_unitario': precio,
    }


def _crear_detalle_extra(test_client, data, headers, cantidad='1'):
    response = test_client.post(
        f"/ventas/{data['venta_a'].id}/detalles",
        headers=headers,
        json=_detalle_payload(data, cantidad=cantidad),
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def _inventario(data):
    return Decimal(str(db.session.get(Inventario, data['inv1'].id).cantidad))


class TestVentaDetalleGet:
    def test_get_returns_list_of_details(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.get(f"/ventas/{data['venta_a'].id}/detalles", headers=headers)
        body = response.get_json()

        assert response.status_code == 200
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]['id'] is not None
        assert Decimal(str(body[0]['cantidad'])) == Decimal('2')
        assert body[0]['presentacion']['id'] == data['presentacion'].id

    def test_get_foreign_sale_same_warehouse_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.get(f"/ventas/{data['venta_b'].id}/detalles", headers=headers)

        assert response.status_code == 403

    def test_get_foreign_sale_other_warehouse_rejected(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.get(f"/ventas/{data['venta_c'].id}/detalles", headers=headers)

        assert response.status_code == 403

    def test_get_missing_sale_returns_404(self, test_client, setup_venta_auth):
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.get('/ventas/999999/detalles', headers=headers)

        assert response.status_code == 404


class TestVentaDetallePost:
    def test_post_valid_creates_detail_and_updates_stock_and_total(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        total_antes = Decimal(str(data['venta_a'].total))
        inv_antes = _inventario(data)
        detalles_antes = VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count()

        response = test_client.post(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json=_detalle_payload(data),
        )
        body = response.get_json()

        assert response.status_code == 201, response.get_data(as_text=True)
        assert body['id'] is not None
        assert body['presentacion']['id'] == data['presentacion'].id
        assert Decimal(str(body['cantidad'])) == Decimal('1')

        db.session.expire_all()
        venta = db.session.get(Venta, data['venta_a'].id)
        assert Decimal(str(venta.total)) == total_antes + Decimal('50.00')
        assert VentaDetalle.query.filter_by(venta_id=venta.id).count() == detalles_antes + 1
        assert _inventario(data) == inv_antes - Decimal('1')

    def test_post_foreign_sale_same_warehouse_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalles_antes = VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).count()
        total_antes = Decimal(str(data['venta_b'].total))
        inv_antes = _inventario(data)

        response = test_client.post(
            f"/ventas/{data['venta_b'].id}/detalles",
            headers=headers,
            json=_detalle_payload(data),
        )

        assert response.status_code == 403
        db.session.expire_all()
        assert VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).count() == detalles_antes
        assert Decimal(str(db.session.get(Venta, data['venta_b'].id).total)) == total_antes
        assert _inventario(data) == inv_antes

    def test_post_foreign_sale_other_warehouse_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalles_antes = VentaDetalle.query.filter_by(venta_id=data['venta_c'].id).count()

        response = test_client.post(
            f"/ventas/{data['venta_c'].id}/detalles",
            headers=headers,
            json=_detalle_payload(data),
        )

        assert response.status_code == 403
        db.session.expire_all()
        assert VentaDetalle.query.filter_by(venta_id=data['venta_c'].id).count() == detalles_antes

    def test_post_missing_sale_returns_404(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.post(
            '/ventas/999999/detalles',
            headers=headers,
            json=_detalle_payload(data),
        )

        assert response.status_code == 404

    def test_post_cannot_move_detail_to_another_sale(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalles_antes = VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count()
        payload = _detalle_payload(data)
        payload['venta_id'] = data['venta_b'].id

        response = test_client.post(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json=payload,
        )

        assert response.status_code == 400
        db.session.expire_all()
        assert VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count() == detalles_antes
        assert VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).count() == 1

    def test_post_rejects_invalid_quantity_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalles_antes = VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count()
        inv_antes = _inventario(data)

        response = test_client.post(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json=_detalle_payload(data, cantidad='0'),
        )

        assert response.status_code == 400
        db.session.expire_all()
        assert VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count() == detalles_antes
        assert _inventario(data) == inv_antes

    def test_post_insufficient_stock_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        total_antes = Decimal(str(data['venta_a'].total))
        detalles_antes = VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count()
        inv_antes = _inventario(data)

        response = test_client.post(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json=_detalle_payload(data, cantidad='9999'),
        )

        assert response.status_code == 400
        db.session.expire_all()
        assert Decimal(str(db.session.get(Venta, data['venta_a'].id).total)) == total_antes
        assert VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).count() == detalles_antes
        assert _inventario(data) == inv_antes


class TestVentaDetalleDelete:
    def test_delete_valid_removes_detail_and_reverts_stock(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        nuevo = _crear_detalle_extra(test_client, data, headers)
        total_antes = Decimal(str(db.session.get(Venta, data['venta_a'].id).total))
        inv_antes = _inventario(data)

        response = test_client.delete(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json={'detalle_id': nuevo['id']},
        )

        assert response.status_code == 204
        assert response.get_data(as_text=True) == ''
        db.session.expire_all()
        assert db.session.get(VentaDetalle, nuevo['id']) is None
        venta = db.session.get(Venta, data['venta_a'].id)
        assert Decimal(str(venta.total)) == total_antes - Decimal('50.00')
        assert VentaDetalle.query.filter_by(venta_id=venta.id).count() == 1
        assert _inventario(data) == inv_antes + Decimal('1')

    def test_delete_foreign_sale_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalle_b = VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).first()
        inv_antes = _inventario(data)

        response = test_client.delete(
            f"/ventas/{data['venta_b'].id}/detalles",
            headers=headers,
            json={'detalle_id': detalle_b.id},
        )

        assert response.status_code == 403
        db.session.expire_all()
        assert db.session.get(VentaDetalle, detalle_b.id) is not None
        assert _inventario(data) == inv_antes

    def test_delete_detail_from_another_sale_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalle_b = VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).first()
        detalle_a = VentaDetalle.query.filter_by(venta_id=data['venta_a'].id).first()
        inv_antes = _inventario(data)

        response = test_client.delete(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json={'detalle_id': detalle_b.id},
        )

        assert response.status_code == 404
        db.session.expire_all()
        assert db.session.get(VentaDetalle, detalle_b.id) is not None
        assert db.session.get(VentaDetalle, detalle_a.id) is not None
        assert _inventario(data) == inv_antes

    def test_delete_without_detail_id_returns_400(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.delete(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
        )

        assert response.status_code == 400

    def test_delete_missing_detail_returns_404(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')

        response = test_client.delete(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json={'detalle_id': 999999},
        )

        assert response.status_code == 404

    def test_delete_last_detail_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_b', 'userpass')
        detalle_b = VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).first()

        response = test_client.delete(
            f"/ventas/{data['venta_b'].id}/detalles",
            headers=headers,
            json={'detalle_id': detalle_b.id},
        )

        assert response.status_code == 400
        db.session.expire_all()
        assert db.session.get(VentaDetalle, detalle_b.id) is not None

    def test_delete_rejects_incoherent_payment_balance(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        nuevo = _crear_detalle_extra(test_client, data, headers)
        venta = db.session.get(Venta, data['venta_a'].id)
        db.session.add(Pago(
            venta_id=venta.id,
            monto=Decimal(str(venta.total)),
            metodo_pago='efectivo',
            usuario_id=data['user_a'].id,
        ))
        db.session.commit()
        total_antes = Decimal(str(venta.total))

        response = test_client.delete(
            f"/ventas/{data['venta_a'].id}/detalles",
            headers=headers,
            json={'detalle_id': nuevo['id']},
        )

        assert response.status_code == 409
        db.session.expire_all()
        assert db.session.get(VentaDetalle, nuevo['id']) is not None
        assert Decimal(str(db.session.get(Venta, venta.id).total)) == total_antes
        assert Pago.query.filter_by(venta_id=venta.id).count() == 1


class TestVentaDetailPutContract:
    def test_put_foreign_sale_details_rejected_without_effects(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        detalle_b = VentaDetalle.query.filter_by(venta_id=data['venta_b'].id).first()
        inv_antes = _inventario(data)

        response = test_client.put(
            f"/ventas/{data['venta_b'].id}",
            headers=headers,
            json={
                'cliente_id': data['cliente'].id,
                'almacen_id': data['almacen1'].id,
                'detalles': [_detalle_payload(data, cantidad='4')],
            },
        )

        assert response.status_code == 403
        db.session.expire_all()
        assert db.session.get(VentaDetalle, detalle_b.id) is not None
        assert Decimal(str(db.session.get(VentaDetalle, detalle_b.id).cantidad)) == Decimal('1')
        assert _inventario(data) == inv_antes

    def test_put_own_sale_updates_details_atomically(self, test_client, setup_venta_auth):
        data = setup_venta_auth
        headers = auth_headers(test_client, 'usuario_a', 'userpass')
        inv_antes = _inventario(data)

        response = test_client.put(
            f"/ventas/{data['venta_a'].id}",
            headers=headers,
            json={
                'cliente_id': data['cliente'].id,
                'almacen_id': data['almacen1'].id,
                'estado': 'completado',
                'detalles': [_detalle_payload(data, cantidad='3')],
            },
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        db.session.expire_all()
        venta = db.session.get(Venta, data['venta_a'].id)
        assert Decimal(str(venta.total)) == Decimal('150.00')
        assert VentaDetalle.query.filter_by(venta_id=venta.id).count() == 1
        assert Decimal(str(VentaDetalle.query.filter_by(venta_id=venta.id).first().cantidad)) == Decimal('3')
        assert _inventario(data) == inv_antes - Decimal('1')
