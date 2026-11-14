"""Los reportes cuentan por día de negocio de Lima, no por día UTC.

Escenario: operación a las 23:30 del 10/03/2026 en Lima = 04:30 UTC del 11/03/2026.
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from extensions import db
from models import (
    Almacen,
    Cliente,
    Movimiento,
    Pago,
    PresentacionProducto,
    Producto,
    Users,
    Venta,
    VentaDetalle,
)

INSTANTE = datetime(2026, 3, 11, 4, 30, tzinfo=UTC)  # 23:30 del 10/03 en Lima
DIA_LIMA = '2026-03-10'
DIA_SIGUIENTE = '2026-03-11'


def _limpiar_tablas():
    for tabla in reversed(db.metadata.sorted_tables):
        db.session.execute(tabla.delete())
    db.session.commit()


def _buscar(payload, clave):
    if isinstance(payload, dict):
        if clave in payload:
            return payload[clave]
        for valor in payload.values():
            encontrado = _buscar(valor, clave)
            if encontrado is not None:
                return encontrado
    if isinstance(payload, list):
        for valor in payload:
            encontrado = _buscar(valor, clave)
            if encontrado is not None:
                return encontrado
    return None


@pytest.fixture()
def escenario(test_client):
    _limpiar_tablas()
    almacen = Almacen(nombre='Planta TZ', es_planta=True)
    db.session.add(almacen)
    db.session.commit()
    admin = Users(username='tz_admin', password=generate_password_hash('adminpass1'),
                  rol='admin', almacen_id=almacen.id)
    cliente = Cliente(nombre='Cliente TZ')
    producto = Producto(nombre='Briqueta TZ', precio_compra=Decimal('5'))
    db.session.add_all([admin, cliente, producto])
    db.session.commit()
    pres = PresentacionProducto(nombre='Caja TZ 3kg', producto_id=producto.id,
                                capacidad_kg=Decimal('3'), tipo='briqueta', precio_venta=Decimal('10'))
    db.session.add(pres)
    db.session.commit()

    venta = Venta(cliente_id=cliente.id, almacen_id=almacen.id, vendedor_id=admin.id,
                  total=Decimal('100.00'), tipo_pago='contado', estado_pago='pagado', fecha=INSTANTE)
    db.session.add(venta)
    db.session.commit()
    db.session.add_all([
        VentaDetalle(venta_id=venta.id, presentacion_id=pres.id,
                     cantidad=Decimal('10'), precio_unitario=Decimal('10.00')),
        Pago(venta_id=venta.id, monto=Decimal('100.00'), metodo_pago='efectivo', usuario_id=admin.id,
             fecha=INSTANTE, depositado=True, monto_depositado=Decimal('50.00'), fecha_deposito=INSTANTE),
        Movimiento(tipo='entrada', tipo_operacion='ensamblaje', presentacion_id=pres.id,
                   cantidad=Decimal('7'), fecha=INSTANTE, usuario_id=admin.id, motivo='Producción nocturna'),
    ])
    db.session.commit()

    resp = test_client.post('/auth', json={'username': 'tz_admin', 'password': 'adminpass1'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    yield {'Authorization': f"Bearer {resp.get_json()['access_token']}"}
    _limpiar_tablas()


def _rango(dia):
    return f'fecha_inicio={dia}&fecha_fin={dia}'


def test_ventas_por_presentacion_usa_dia_de_lima(test_client, escenario):
    resp = test_client.get(f'/reportes/ventas-presentacion?{_rango(DIA_LIMA)}', headers=escenario)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert Decimal(str(_buscar(resp.get_json(), 'total_vendido'))) == Decimal('100')

    resp = test_client.get(f'/reportes/ventas-presentacion?{_rango(DIA_SIGUIENTE)}', headers=escenario)
    assert resp.status_code == 200
    assert _buscar(resp.get_json(), 'total_vendido') in (None, 0, '0', '0.00', 0.0)


def test_resumen_financiero_ventas_y_depositos_por_dia_de_lima(test_client, escenario):
    resp = test_client.get(f'/reportes/resumen-financiero?{_rango(DIA_LIMA)}', headers=escenario)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert Decimal(str(_buscar(data, 'total_ventas'))) == Decimal('100.00')
    assert Decimal(str(_buscar(data, 'depositado_total'))) == Decimal('50.00')

    data = test_client.get(f'/reportes/resumen-financiero?{_rango(DIA_SIGUIENTE)}', headers=escenario).get_json()
    assert Decimal(str(_buscar(data, 'total_ventas'))) == Decimal('0')
    assert Decimal(str(_buscar(data, 'depositado_total'))) == Decimal('0')


def test_briquetas_agrupa_por_dia_de_lima(test_client, escenario):
    resp = test_client.get(f'/reportes/produccion-briquetas?{_rango(DIA_LIMA)}&periodo=dia', headers=escenario)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    temporal = resp.get_json()['resumen_temporal']
    assert [fila['fecha'] for fila in temporal] == [DIA_LIMA]
    assert temporal[0]['unidades_producidas'] == 7


def test_entradas_resumen_temporal_por_dia_de_lima(test_client, escenario):
    resp = test_client.get(f'/reportes/produccion-entradas?{_rango(DIA_LIMA)}', headers=escenario)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    fechas = [fila['fecha'] for fila in resp.get_json()['resumen_temporal']]
    assert fechas == [DIA_LIMA]
