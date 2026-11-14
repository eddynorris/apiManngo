"""Presupuesto de consultas SQL para el listado de ventas.

El presupuesto no se fija a un número inventado: se mide primero el caso de
una sola venta y se exige que 10 y 20 ventas con clientes distintos no emitan
más consultas que ese caso base. Un N+1 hace crecer el conteo y falla.
"""
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    producto = Producto(nombre=f"Producto budget {sufijo}", precio_compra=Decimal("1.00"))
    db.session.add(producto)
    db.session.flush()

    for indice in range(n):
        cliente = Cliente(nombre=f"Cliente budget {sufijo} {indice}")
        presentacion = PresentacionProducto(
            producto_id=producto.id,
            nombre=f"Presentación budget {sufijo} {indice}",
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
            tipo_pago="credito",
            estado_pago="pendiente",
            estado="completado",
            fecha=datetime.now(timezone.utc) - timedelta(seconds=indice),
        )
        db.session.add(venta)
        db.session.flush()

        db.session.add(VentaDetalle(
            venta_id=venta.id,
            presentacion_id=presentacion.id,
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100.00"),
        ))
        db.session.add(Pago(
            venta_id=venta.id,
            usuario_id=admin.id,
            monto=Decimal("10.00"),
            metodo_pago="efectivo",
            fecha=datetime.now(timezone.utc) - timedelta(seconds=indice),
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
    return {"Authorization": f"Bearer {token}"}


def _medir_listado(test_client, n):
    headers = _preparar_ventas(n)
    # Sesión fría: el request no debe reutilizar identidades de la preparación.
    db.session.remove()

    with _contar_selects() as sentencias:
        response = test_client.get(f"/ventas?per_page={n}", headers=headers)

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert len(body["data"]) == n
    assert body["pagination"]["total"] == n
    assert len({fila["cliente"]["id"] for fila in body["data"]}) == n
    return len(sentencias)


def test_presupuesto_sql_no_crece_con_ventas_y_clientes_distintos(test_client):
    conteos = {n: _medir_listado(test_client, n) for n in (1, 10, 20)}

    # Se imprime para que el padre registre la medición real antes/después.
    print(f"SELECTs fríos por listado de ventas: {conteos}")

    assert conteos[10] <= conteos[1], conteos
    assert conteos[20] <= conteos[1], conteos
