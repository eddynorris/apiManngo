from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import (
    Almacen,
    Cliente,
    Gasto,
    Inventario,
    Lote,
    Movimiento,
    Pago,
    PresentacionProducto,
    Producto,
    Proveedor,
    Users,
    Venta,
    VentaDetalle,
)
from utils.date_parser import (
    parse_fecha_operacion,
    parse_telegram_date,
    parse_telegram_date_only,
)
from utils.date_utils import get_peru_now, to_peru_time


def _headers(client, username='admin_fechas', password='adminpass'):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return {'Authorization': f"Bearer {response.get_json()['access_token']}"}


@pytest.fixture(scope='function')
def setup_fechas_data(test_client):
    db.session.remove()
    db.session.query(Gasto).delete()
    db.session.query(Pago).delete()
    db.session.query(Movimiento).delete()
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.query(Inventario).delete()
    db.session.query(PresentacionProducto).delete()
    db.session.query(Lote).delete()
    db.session.query(Producto).delete()
    db.session.query(Proveedor).delete()
    db.session.query(Cliente).delete()
    db.session.query(Users).delete()
    db.session.query(Almacen).delete()
    db.session.commit()

    almacen = Almacen(nombre='Almacén Fechas')
    db.session.add(almacen)
    db.session.commit()

    admin = Users(
        username='admin_fechas',
        password=generate_password_hash('adminpass'),
        rol='gerente',
        almacen_id=almacen.id,
    )
    cliente = Cliente(nombre='Cliente Fechas')
    producto = Producto(nombre='Producto Fechas', precio_compra=Decimal('5.00'))
    proveedor = Proveedor(nombre='Proveedor Fechas')
    db.session.add_all([admin, cliente, producto, proveedor])
    db.session.commit()

    presentacion = PresentacionProducto(
        producto_id=producto.id,
        nombre='Presentación Fechas',
        capacidad_kg=Decimal('1.00'),
        tipo='procesado',
        precio_venta=Decimal('10.00'),
        activo=True,
    )
    insumo = PresentacionProducto(
        producto_id=producto.id,
        nombre='Insumo Fechas',
        capacidad_kg=Decimal('0.01'),
        tipo='insumo',
        precio_venta=Decimal('1.00'),
        activo=True,
    )
    lote = Lote(
        producto_id=producto.id,
        descripcion='Lote Fechas',
        cantidad_disponible_kg=Decimal('100.00'),
        fecha_ingreso=datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.session.add_all([presentacion, insumo, lote])
    db.session.flush()

    inventario = Inventario(
        presentacion_id=presentacion.id,
        almacen_id=almacen.id,
        lote_id=lote.id,
        cantidad=Decimal('20'),
    )
    inventario_insumo = Inventario(
        presentacion_id=insumo.id,
        almacen_id=almacen.id,
        lote_id=None,
        cantidad=Decimal('20'),
        stock_minimo=5,  # columna Integer
    )
    db.session.add_all([inventario, inventario_insumo])
    db.session.commit()

    return {
        'almacen': almacen,
        'admin': admin,
        'cliente': cliente,
        'producto': producto,
        'proveedor': proveedor,
        'presentacion': presentacion,
        'insumo': insumo,
        'lote': lote,
        'inventario': inventario,
        'inventario_insumo': inventario_insumo,
    }


def _transaction_payload(data, **overrides):
    payload = {
        'cliente': {'id': data['cliente'].id},
        'items': [{
            'producto_id': data['presentacion'].id,
            'lote_id': data['lote'].id,
            'cantidad': 1,
            'precio_unitario': '10.00',
        }],
        'pagos': [{'monto': '10.00', 'metodo_pago': 'efectivo'}],
    }
    payload.update(overrides)
    return payload


def _compra_payload(data, **overrides):
    payload = {
        'presentacion_id': data['insumo'].id,
        'almacen_id': data['almacen'].id,
        'cantidad': 5,
        'costo_total': '12.50',
        'proveedor_id': data['proveedor'].id,
        'descripcion': 'Compra fechas',
    }
    payload.update(overrides)
    return payload


def _snapshot(data):
    db.session.expire_all()
    return {
        'ventas': Venta.query.count(),
        'detalles': VentaDetalle.query.count(),
        'pagos': Pago.query.count(),
        'gastos': Gasto.query.count(),
        'movimientos': Movimiento.query.count(),
        'stock_procesado': Decimal(str(db.session.get(
            Inventario, data['inventario'].id
        ).cantidad)),
        'stock_insumo': Decimal(str(db.session.get(
            Inventario, data['inventario_insumo'].id
        ).cantidad)),
    }


class TestParseoFechasOperacion:
    def test_fecha_dia_es_medianoche_de_peru_en_utc(self):
        assert parse_fecha_operacion('2026-03-11') == datetime(
            2026, 3, 11, 5, 0, tzinfo=UTC
        )

    def test_hora_ingenua_se_interpreta_en_peru(self):
        assert parse_fecha_operacion('2026-03-11T23:30:00') == datetime(
            2026, 3, 12, 4, 30, tzinfo=UTC
        )

    def test_iso_con_zona_respeta_el_instante(self):
        esperado = datetime(2026, 3, 12, 4, 30, tzinfo=UTC)
        assert parse_fecha_operacion('2026-03-11T23:30:00-05:00') == esperado
        assert parse_fecha_operacion('2026-03-12T04:30:00Z') == esperado

    @pytest.mark.parametrize('valor', [None, '', '   ', '11/03/2026', '2026-02-30'])
    def test_fecha_explicita_invalida_rechaza(self, valor):
        with pytest.raises(ValueError):
            parse_fecha_operacion(valor)

    def test_telegram_fecha_dia_es_dia_peru(self):
        assert parse_telegram_date_only('2026-03-11') == date(2026, 3, 11)
        fecha = parse_telegram_date('2026-03-11')
        assert fecha.date() == date(2026, 3, 11)
        assert to_peru_time(fecha).date() == date(2026, 3, 11)

    def test_telegram_fecha_ausente_usa_hoy_peru(self):
        hoy = get_peru_now().date()
        assert parse_telegram_date_only(None) == hoy
        assert parse_telegram_date_only('') == hoy
        assert to_peru_time(parse_telegram_date(None)).date() == hoy

    @pytest.mark.parametrize('valor', ['11/03/2026', 'this-is-not-a-date', '2026-13-01'])
    def test_telegram_fecha_invalida_no_cae_a_hoy(self, valor):
        with pytest.raises(ValueError):
            parse_telegram_date_only(valor)
        with pytest.raises(ValueError):
            parse_telegram_date(valor)


class TestTransaccionFechas:
    @pytest.mark.parametrize(
        'fecha',
        ['this-is-not-a-date', '2026/03/11', '2026-02-30', '', None],
    )
    def test_fecha_explicita_invalida_da_400_sin_efectos(
        self, test_client, setup_fechas_data, fecha
    ):
        data = setup_fechas_data
        antes = _snapshot(data)

        response = test_client.post(
            '/transacciones/venta-completa',
            headers=_headers(test_client),
            json=_transaction_payload(
                data,
                fecha=fecha,
                gasto_asociado={
                    'descripcion': 'Gasto que no debe registrarse',
                    'monto': '2.00',
                    'categoria': 'logistica',
                },
            ),
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        assert 'fecha' in response.get_json()['error'].lower()
        assert _snapshot(data) == antes

    def test_fecha_ausente_usa_ahora_y_registra(self, test_client, setup_fechas_data):
        data = setup_fechas_data
        hoy = get_peru_now().date()
        payload = _transaction_payload(data)

        response = test_client.post(
            '/transacciones/venta-completa',
            headers=_headers(test_client),
            json=payload,
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        venta = Venta.query.order_by(Venta.id.desc()).first()
        assert venta is not None
        assert to_peru_time(venta.fecha).date() in {hoy, get_peru_now().date()}

    def test_fecha_dia_valida_se_guarda_como_dia_peru(
        self, test_client, setup_fechas_data
    ):
        data = setup_fechas_data
        response = test_client.post(
            '/transacciones/venta-completa',
            headers=_headers(test_client),
            json=_transaction_payload(data, fecha='2026-03-11'),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        venta = Venta.query.order_by(Venta.id.desc()).first()
        pago = Pago.query.order_by(Pago.id.desc()).first()
        assert to_peru_time(venta.fecha).date() == date(2026, 3, 11)
        assert to_peru_time(pago.fecha).date() == date(2026, 3, 11)

    def test_iso_cerca_de_medianoche_conserva_dia_peru(
        self, test_client, setup_fechas_data
    ):
        data = setup_fechas_data
        response = test_client.post(
            '/transacciones/venta-completa',
            headers=_headers(test_client),
            json=_transaction_payload(data, fecha='2026-03-11T23:30:00-05:00'),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        venta = Venta.query.order_by(Venta.id.desc()).first()
        assert to_peru_time(venta.fecha).date() == date(2026, 3, 11)

    def test_gasto_asociado_usa_dia_peru(self, test_client, setup_fechas_data):
        data = setup_fechas_data
        payload = _transaction_payload(
            data,
            fecha='2026-03-11T23:30:00-05:00',
            gasto_asociado={
                'descripcion': 'Flete fechas',
                'monto': '2.00',
                'categoria': 'logistica',
            },
        )
        response = test_client.post(
            '/transacciones/venta-completa',
            headers=_headers(test_client),
            json=payload,
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        gasto = Gasto.query.order_by(Gasto.id.desc()).first()
        assert gasto.fecha == date(2026, 3, 11)


class TestCompraInsumoFechas:
    @pytest.mark.parametrize(
        'fecha',
        ['this-is-not-a-date', '2026/03/11', '2026-02-30', '', None],
    )
    def test_fecha_explicita_invalida_da_400_sin_efectos(
        self, test_client, setup_fechas_data, fecha
    ):
        data = setup_fechas_data
        antes = _snapshot(data)

        response = test_client.post(
            '/compras/insumos',
            headers=_headers(test_client),
            json=_compra_payload(data, fecha=fecha),
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        body = response.get_json()
        assert 'fecha' in body['error'].lower()
        assert 'detalle' not in body
        assert _snapshot(data) == antes

    def test_fecha_ausente_usa_ahora_y_registra(self, test_client, setup_fechas_data):
        data = setup_fechas_data
        hoy = get_peru_now().date()
        response = test_client.post(
            '/compras/insumos',
            headers=_headers(test_client),
            json=_compra_payload(data),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        gasto = Gasto.query.order_by(Gasto.id.desc()).first()
        movimiento = Movimiento.query.filter_by(tipo_operacion='compra').first()
        dias_validos = {hoy, get_peru_now().date()}
        assert gasto.fecha in dias_validos
        assert to_peru_time(movimiento.fecha).date() in dias_validos

    def test_fecha_dia_valida_es_dia_peru(self, test_client, setup_fechas_data):
        data = setup_fechas_data
        response = test_client.post(
            '/compras/insumos',
            headers=_headers(test_client),
            json=_compra_payload(data, fecha='2026-03-11'),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body['resumen']['fecha'] == '2026-03-11'

        gasto = Gasto.query.order_by(Gasto.id.desc()).first()
        movimiento = Movimiento.query.filter_by(tipo_operacion='compra').first()
        inventario = db.session.get(Inventario, data['inventario_insumo'].id)

        assert gasto.fecha == date(2026, 3, 11)
        assert to_peru_time(movimiento.fecha).date() == date(2026, 3, 11)
        assert Decimal(str(inventario.cantidad)) == Decimal('25')

    def test_iso_cerca_de_medianoche_conserva_dia_peru(
        self, test_client, setup_fechas_data
    ):
        data = setup_fechas_data
        response = test_client.post(
            '/compras/insumos',
            headers=_headers(test_client),
            json=_compra_payload(
                data, fecha='2026-03-11T23:30:00-05:00'
            ),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        gasto = Gasto.query.order_by(Gasto.id.desc()).first()
        movimiento = Movimiento.query.filter_by(tipo_operacion='compra').first()
        assert gasto.fecha == date(2026, 3, 11)
        assert to_peru_time(movimiento.fecha).date() == date(2026, 3, 11)
