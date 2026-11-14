"""Regresiones L3/A04: autorización y atomicidad de stock.

Las pruebas usan solo SQLite desechable del runner offline. No tocan red,
servicios externos ni bases reales.
"""
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from app import app
from extensions import db
from models import (
    Almacen,
    ComponenteReceta,
    Inventario,
    Lote,
    Movimiento,
    PresentacionProducto,
    Producto,
    Receta,
    Users,
)
from models_stock_audit import MovimientoScope


def _headers(usuario, incluir_claims=True):
    claims = {"auth_version": int(usuario.auth_version or 0)}
    if incluir_claims:
        claims.update({"rol": usuario.rol, "almacen_id": usuario.almacen_id})
    token = create_access_token(identity=str(usuario.id), additional_claims=claims)
    return {"Authorization": f"Bearer {token}"}


def _inventario(almacen_id, presentacion_id, lote_id=None):
    return Inventario.query.filter_by(
        almacen_id=almacen_id,
        presentacion_id=presentacion_id,
        lote_id=lote_id,
    ).one()


def _stock_total(almacen_id, presentacion_id):
    return sum(
        (
            inv.cantidad
            for inv in Inventario.query.filter_by(
                almacen_id=almacen_id, presentacion_id=presentacion_id
            ).all()
        ),
        Decimal("0"),
    )


@pytest.fixture(scope="module")
def test_client():
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        JWT_SECRET_KEY="test-secret-key-stock-auth-32-chars",
    )
    with app.test_client() as testing_client:
        with app.app_context():
            db.create_all()
            yield testing_client
            db.session.remove()
            db.drop_all()


@pytest.fixture(scope="function")
def stock_env(test_client):
    db.session.rollback()
    for model in (
        MovimientoScope,
        Movimiento,
        Inventario,
        ComponenteReceta,
        Receta,
        PresentacionProducto,
        Lote,
        Producto,
        Users,
        Almacen,
    ):
        db.session.query(model).delete()
    db.session.commit()

    almacen1 = Almacen(id=1, nombre="L3 Almacén Uno")
    almacen2 = Almacen(id=2, nombre="L3 Almacén Dos")
    almacen3 = Almacen(id=3, nombre="L3 Almacén Tres")
    db.session.add_all([almacen1, almacen2, almacen3])
    db.session.flush()

    admin = Users(username="l3_admin", password="x", rol="admin", almacen_id=almacen1.id)
    user1 = Users(username="l3_user1", password="x", rol="usuario", almacen_id=almacen1.id)
    user2 = Users(username="l3_user2", password="x", rol="usuario", almacen_id=almacen2.id)
    user3 = Users(username="l3_user3", password="x", rol="usuario", almacen_id=almacen3.id)
    gerente = Users(username="l3_gerente", password="x", rol="gerente", almacen_id=almacen1.id)
    sin_almacen = Users(username="l3_sin_almacen", password="x", rol="usuario", almacen_id=None)
    db.session.add_all([admin, user1, user2, user3, gerente, sin_almacen])
    db.session.flush()

    producto = Producto(nombre="L3 Carbón", precio_compra=Decimal("10.00"))
    db.session.add(producto)
    db.session.flush()

    principal = PresentacionProducto(
        producto_id=producto.id,
        nombre="L3 Saco 20kg",
        capacidad_kg=Decimal("20.00"),
        tipo="procesado",
        precio_venta=Decimal("50.00"),
    )
    secundaria = PresentacionProducto(
        producto_id=producto.id,
        nombre="L3 Saco 10kg",
        capacidad_kg=Decimal("10.00"),
        tipo="procesado",
        precio_venta=Decimal("25.00"),
    )
    insumo = PresentacionProducto(
        producto_id=producto.id,
        nombre="L3 Saco vacío",
        capacidad_kg=Decimal("0.10"),
        tipo="insumo",
        precio_venta=Decimal("2.00"),
    )
    final = PresentacionProducto(
        producto_id=producto.id,
        nombre="L3 Caja final",
        capacidad_kg=Decimal("5.00"),
        tipo="procesado",
        precio_venta=Decimal("30.00"),
    )
    db.session.add_all([principal, secundaria, insumo, final])
    db.session.flush()

    lotes = {}
    for almacen in (almacen1, almacen2, almacen3):
        lote = Lote(
            producto_id=producto.id,
            descripcion=f"L3 Lote almacén {almacen.id}",
            cantidad_disponible_kg=Decimal("1000.00"),
        )
        db.session.add(lote)
        lotes[almacen.id] = lote
    db.session.flush()

    db.session.add_all(
        [
            Inventario(
                presentacion_id=principal.id,
                almacen_id=almacen1.id,
                lote_id=lotes[almacen1.id].id,
                cantidad=Decimal("50"),
            ),
            Inventario(
                presentacion_id=principal.id,
                almacen_id=almacen2.id,
                lote_id=lotes[almacen2.id].id,
                cantidad=Decimal("40"),
            ),
            Inventario(
                presentacion_id=principal.id,
                almacen_id=almacen3.id,
                lote_id=lotes[almacen3.id].id,
                cantidad=Decimal("30"),
            ),
            Inventario(
                presentacion_id=secundaria.id,
                almacen_id=almacen2.id,
                lote_id=lotes[almacen2.id].id,
                cantidad=Decimal("2"),
            ),
            Inventario(
                presentacion_id=insumo.id,
                almacen_id=almacen1.id,
                lote_id=None,
                cantidad=Decimal("100"),
            ),
            Inventario(
                presentacion_id=insumo.id,
                almacen_id=almacen2.id,
                lote_id=None,
                cantidad=Decimal("100"),
            ),
            Inventario(
                presentacion_id=insumo.id,
                almacen_id=almacen3.id,
                lote_id=None,
                cantidad=Decimal("100"),
            ),
        ]
    )

    receta = Receta(presentacion_id=final.id, nombre="L3 Receta caja")
    db.session.add(receta)
    db.session.flush()
    db.session.add(
        ComponenteReceta(
            receta_id=receta.id,
            componente_presentacion_id=insumo.id,
            cantidad_necesaria=Decimal("1"),
            tipo_consumo="insumo",
        )
    )
    db.session.commit()

    return {
        "almacenes": {1: almacen1, 2: almacen2, 3: almacen3},
        "lotes": lotes,
        "admin": admin,
        "user1": user1,
        "user2": user2,
        "user3": user3,
        "gerente": gerente,
        "sin_almacen": sin_almacen,
        "producto": producto,
        "principal": principal,
        "secundaria": secundaria,
        "insumo": insumo,
        "final": final,
        "receta": receta,
    }


def _payload_transferencia(stock_env, origen, destino, lineas):
    return {
        "almacen_origen_id": origen,
        "almacen_destino_id": destino,
        "transferencias": lineas,
    }


def _linea(presentacion_id, cantidad):
    return {"presentacion_id": presentacion_id, "cantidad": cantidad}


def _payload_produccion(stock_env, almacen_id, cantidad=3):
    return {
        "almacen_id": almacen_id,
        "presentacion_id": stock_env["final"].id,
        "cantidad_a_producir": cantidad,
        "lotes_seleccionados": [],
    }


def test_non_admin_cannot_transfer_between_foreign_warehouses(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, 2, 3, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["user1"])
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert _inventario(3, stock_env["principal"].id, stock_env["lotes"][3].id).cantidad == Decimal("30")
    assert Movimiento.query.count() == 0


def test_non_admin_cannot_transfer_from_own_to_foreign_warehouse(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, 1, 2, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["user1"])
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(1, stock_env["principal"].id, stock_env["lotes"][1].id).cantidad == Decimal("50")
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert Movimiento.query.count() == 0


def test_non_admin_cannot_transfer_from_foreign_to_own_warehouse(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, 2, 1, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["user1"])
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert Movimiento.query.count() == 0


def test_authorization_resolves_actor_from_database_not_token_claims(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, 2, 3, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir",
        json=payload,
        headers=_headers(stock_env["user1"], incluir_claims=False),
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert Movimiento.query.count() == 0


def test_user_without_warehouse_is_forbidden(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, 1, 2, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir",
        json=payload,
        headers=_headers(stock_env["sin_almacen"]),
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert Movimiento.query.count() == 0


def test_valid_admin_can_transfer_across_warehouses(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, 2, 3, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    db.session.expire_all()
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("35")
    assert _stock_total(3, stock_env["principal"].id) == Decimal("35")
    assert _inventario(3, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("5")
    movimientos = Movimiento.query.order_by(Movimiento.id).all()
    assert len(movimientos) == 2
    assert {m.tipo for m in movimientos} == {"entrada", "salida"}
    assert {m.tipo_operacion for m in movimientos} == {"transferencia"}


def test_transfer_accepts_equivalent_string_ids_for_admin(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env,
        str(stock_env["almacenes"][2].id),
        str(stock_env["almacenes"][3].id),
        [_linea(str(stock_env["principal"].id), "5")],
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    db.session.expire_all()
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("35")


@pytest.mark.parametrize("valor_invalido", [True, 1.0, "1.0", 0, -2, " 2", "2 "])
def test_transfer_rejects_non_strict_warehouse_ids_without_effects(
    test_client, stock_env, valor_invalido
):
    payload = _payload_transferencia(
        stock_env, valor_invalido, 3, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert Movimiento.query.count() == 0
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert _inventario(3, stock_env["principal"].id, stock_env["lotes"][3].id).cantidad == Decimal("30")


@pytest.mark.parametrize(
    "cantidad_invalida",
    [0, -1, True, "NaN", "Infinity", "-Infinity", "abc", None, "1e10000"],
)
def test_transfer_rejects_non_positive_or_non_finite_quantities(
    test_client, stock_env, cantidad_invalida
):
    payload = _payload_transferencia(
        stock_env, 2, 3, [_linea(stock_env["principal"].id, cantidad_invalida)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert Movimiento.query.count() == 0
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert _inventario(3, stock_env["principal"].id, stock_env["lotes"][3].id).cantidad == Decimal("30")


def test_duplicate_transfer_lines_cannot_overdraw_stock(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env,
        2,
        3,
        [
            _linea(stock_env["principal"].id, 30),
            _linea(stock_env["principal"].id, 30),
        ],
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert _inventario(3, stock_env["principal"].id, stock_env["lotes"][3].id).cantidad == Decimal("30")
    assert Movimiento.query.count() == 0


def test_transfer_batch_has_no_partial_effect_on_late_failure(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env,
        2,
        3,
        [
            _linea(stock_env["principal"].id, 5),
            _linea(stock_env["secundaria"].id, 10),
        ],
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert _inventario(2, stock_env["principal"].id, stock_env["lotes"][2].id).cantidad == Decimal("40")
    assert _inventario(2, stock_env["secundaria"].id, stock_env["lotes"][2].id).cantidad == Decimal("2")
    assert Movimiento.query.count() == 0


def test_transfer_missing_warehouse_returns_400_not_500(test_client, stock_env):
    payload = _payload_transferencia(
        stock_env, None, 3, [_linea(stock_env["principal"].id, 5)]
    )
    response = test_client.post(
        "/inventario/transferir", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert Movimiento.query.count() == 0


def test_production_in_foreign_warehouse_is_forbidden(test_client, stock_env):
    response = test_client.post(
        "/produccion",
        json=_payload_produccion(stock_env, 2),
        headers=_headers(stock_env["user1"]),
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(2, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Inventario.query.filter_by(presentacion_id=stock_env["final"].id).count() == 0
    assert Movimiento.query.count() == 0


def test_gerente_is_not_admin_for_cross_warehouse_production(test_client, stock_env):
    response = test_client.post(
        "/produccion",
        json=_payload_produccion(stock_env, 2),
        headers=_headers(stock_env["gerente"]),
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(2, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Movimiento.query.count() == 0


def test_production_without_warehouse_is_forbidden(test_client, stock_env):
    response = test_client.post(
        "/produccion",
        json=_payload_produccion(stock_env, 1),
        headers=_headers(stock_env["sin_almacen"]),
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(1, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Movimiento.query.count() == 0


def test_admin_can_produce_in_another_warehouse_with_string_ids(test_client, stock_env):
    payload = _payload_produccion(
        stock_env, str(stock_env["almacenes"][2].id), cantidad="3"
    )
    payload["presentacion_id"] = str(stock_env["final"].id)
    response = test_client.post(
        "/produccion", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    db.session.expire_all()
    assert _inventario(2, stock_env["insumo"].id).cantidad == Decimal("97")
    final_inv = Inventario.query.filter_by(
        presentacion_id=stock_env["final"].id, almacen_id=2
    ).one()
    assert final_inv.cantidad == Decimal("3")


def test_production_uses_database_actor_for_own_warehouse(test_client, stock_env):
    response = test_client.post(
        "/produccion",
        json=_payload_produccion(stock_env, 1),
        headers=_headers(stock_env["user1"], incluir_claims=False),
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    db.session.expire_all()
    assert _inventario(1, stock_env["insumo"].id).cantidad == Decimal("97")


@pytest.mark.parametrize(
    "cantidad_invalida",
    [0, -1, True, "NaN", "Infinity", "-Infinity", "abc", "1e10000"],
)
def test_production_rejects_non_positive_or_non_finite_quantities(
    test_client, stock_env, cantidad_invalida
):
    response = test_client.post(
        "/produccion",
        json=_payload_produccion(stock_env, 1, cantidad=cantidad_invalida),
        headers=_headers(stock_env["admin"]),
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert _inventario(1, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Movimiento.query.count() == 0


@pytest.mark.parametrize("campo", ["almacen_id", "presentacion_id"])
@pytest.mark.parametrize("valor_invalido", [True, 1.0, "1.0", 0, -1, " 1"])
def test_production_rejects_non_strict_ids_without_effects(
    test_client, stock_env, campo, valor_invalido
):
    payload = _payload_produccion(stock_env, 1)
    payload[campo] = valor_invalido
    response = test_client.post(
        "/produccion", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert _inventario(1, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Movimiento.query.count() == 0


def test_ensamblaje_foreign_warehouse_is_forbidden(test_client, stock_env):
    payload = {
        "almacen_id": 2,
        "entradas": [
            {"presentacion_id": stock_env["final"].id, "cantidad_unidades": 1}
        ],
        "salidas": [
            {
                "tipo_consumo": "insumo",
                "presentacion_id": stock_env["insumo"].id,
                "cantidad_unidades": 1,
            }
        ],
    }
    response = test_client.post(
        "/produccion/ensamblaje", json=payload, headers=_headers(stock_env["user1"])
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(2, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Movimiento.query.count() == 0


def test_ensamblaje_duplicate_inputs_cannot_overdraw_stock(test_client, stock_env):
    payload = {
        "almacen_id": 1,
        "entradas": [
            {"presentacion_id": stock_env["final"].id, "cantidad_unidades": 1}
        ],
        "salidas": [
            {
                "tipo_consumo": "insumo",
                "presentacion_id": stock_env["insumo"].id,
                "cantidad_unidades": 60,
            },
            {
                "tipo_consumo": "insumo",
                "presentacion_id": stock_env["insumo"].id,
                "cantidad_unidades": 60,
            },
        ],
    }
    response = test_client.post(
        "/produccion/ensamblaje", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert _inventario(1, stock_env["insumo"].id).cantidad == Decimal("100")
    assert Inventario.query.filter_by(presentacion_id=stock_env["final"].id).count() == 0
    assert Movimiento.query.count() == 0
