"""Límites de volumen para ``all=true`` y exportación de ventas."""
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from flask_jwt_extended import create_access_token
from sqlalchemy import event

from extensions import db
from models import (
    Almacen,
    Cliente,
    Pago,
    PresentacionProducto,
    Producto,
    Users,
    Venta,
    VentaDetalle,
)


@contextmanager
def _contar_selects():
    engine = db.engine
    sentencias = []

    def _antes(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            sentencias.append(statement)

    event.listen(engine, "before_cursor_execute", _antes)
    try:
        yield sentencias
    finally:
        event.remove(engine, "before_cursor_execute", _antes)


def _preparar_ventas(n):
    db.session.query(Pago).delete()
    db.session.query(VentaDetalle).delete()
    db.session.query(Venta).delete()
    db.session.commit()

    admin = Users.query.filter_by(username="admin").first()
    almacen = Almacen.query.first()
    assert admin is not None and almacen is not None

    sufijo = uuid.uuid4().hex[:8]
    producto = Producto(nombre=f"Producto export {sufijo}", precio_compra=Decimal("1.00"))
    db.session.add(producto)
    db.session.flush()

    for indice in range(n):
        cliente = Cliente(nombre=f"Cliente export {sufijo} {indice}")
        presentacion = PresentacionProducto(
            producto_id=producto.id,
            nombre=f"Presentación export {sufijo} {indice}",
            capacidad_kg=Decimal("1.00"),
            tipo="procesado",
            precio_venta=Decimal("10.00"),
            activo=True,
        )
        db.session.add_all([cliente, presentacion])
        db.session.flush()
        venta = Venta(
            cliente_id=cliente.id,
            almacen_id=almacen.id,
            vendedor_id=admin.id,
            total=Decimal("100.00"),
            tipo_pago="contado",
            estado_pago="pagado",
            estado="completado",
            fecha=datetime.now(UTC) - timedelta(seconds=indice),
        )
        db.session.add(venta)
        db.session.flush()
        db.session.add(VentaDetalle(
            venta_id=venta.id,
            presentacion_id=presentacion.id,
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100.00"),
        ))

    db.session.commit()
    token = create_access_token(
        identity=str(admin.id),
        additional_claims={
            "sub": str(admin.id),
            "username": admin.username,
            "rol": admin.rol,
            "almacen_id": admin.almacen_id,
            "auth_version": admin.auth_version or 0,
        },
    )
    db.session.remove()
    return {"Authorization": f"Bearer {token}"}


def test_all_true_rechaza_volumen_antes_de_materializar(test_client, monkeypatch):
    headers = _preparar_ventas(3)
    monkeypatch.setitem(test_client.application.config, "MAX_VENTAS_ALL", 2)

    with _contar_selects() as sentencias:
        response = test_client.get("/ventas?all=true", headers=headers)

    assert response.status_code == 413
    body = response.get_json()
    assert body["limite"] == 2
    assert body["total"] == 3
    # No se cargaron detalles/pagos de las ventas antes de rechazar.
    assert not any("venta_detalles" in sql.lower() for sql in sentencias)
    assert not any("from pagos" in sql.lower() for sql in sentencias)


def test_all_true_dentro_del_limite_conserva_contrato(test_client, monkeypatch):
    headers = _preparar_ventas(2)
    monkeypatch.setitem(test_client.application.config, "MAX_VENTAS_ALL", 2)

    response = test_client.get("/ventas?all=true", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2


def test_export_rechaza_volumen_sin_generar_archivo(test_client, monkeypatch):
    headers = _preparar_ventas(3)
    monkeypatch.setitem(test_client.application.config, "MAX_VENTAS_EXPORT", 2)

    response = test_client.get("/ventas/exportar", headers=headers)

    assert response.status_code == 413
    body = response.get_json()
    assert body["limite"] == 2
    assert body["total"] == 3
    assert "spreadsheet" not in response.content_type


def test_export_dentro_del_limite_devuelve_excel(test_client, monkeypatch):
    headers = _preparar_ventas(2)
    monkeypatch.setitem(test_client.application.config, "MAX_VENTAS_EXPORT", 2)

    response = test_client.get("/ventas/exportar", headers=headers)

    assert response.status_code == 200
    assert response.content_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_limite_configurado_fuera_de_rango_responde_400(test_client, monkeypatch):
    headers = _preparar_ventas(1)
    monkeypatch.setitem(test_client.application.config, "MAX_VENTAS_ALL", 0)
    monkeypatch.setitem(test_client.application.config, "MAX_VENTAS_EXPORT", 0)

    assert test_client.get("/ventas?all=true", headers=headers).status_code == 400
    assert test_client.get("/ventas/exportar", headers=headers).status_code == 400
