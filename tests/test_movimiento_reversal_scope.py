"""Regresiones L3/A25: alcance exacto y reversión segura de movimientos."""
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query as SAQuery

from app import app
from extensions import db
from models import (
    Almacen,
    Inventario,
    Lote,
    Movimiento,
    PresentacionProducto,
    Producto,
    Users,
)
from models_stock_audit import MovimientoScope


def _headers(usuario, incluir_claims=True):
    claims = {"auth_version": int(usuario.auth_version or 0)}
    if incluir_claims:
        claims.update({"rol": usuario.rol, "almacen_id": usuario.almacen_id})
    token = create_access_token(identity=str(usuario.id), additional_claims=claims)
    return {"Authorization": f"Bearer {token}"}


def _inventario(stock_env, almacen_id):
    return Inventario.query.filter_by(
        almacen_id=almacen_id,
        presentacion_id=stock_env["presentacion"].id,
        lote_id=stock_env["lotes"][almacen_id].id,
    ).one()


def _payload_movimiento(stock_env, almacen_id, tipo="entrada", cantidad=10, lote_id=None):
    return {
        "tipo": tipo,
        "almacen_id": almacen_id,
        "presentacion_id": stock_env["presentacion"].id,
        "lote_id": stock_env["lotes"][almacen_id].id if lote_id is None else lote_id,
        "cantidad": cantidad,
    }


def _crear_movimiento_manual(test_client, stock_env, usuario, almacen_id, tipo, cantidad):
    response = test_client.post(
        "/movimientos",
        json=_payload_movimiento(stock_env, almacen_id, tipo, cantidad),
        headers=_headers(usuario),
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


@pytest.fixture(scope="module")
def test_client():
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        JWT_SECRET_KEY="test-secret-key-movement-scope-32-chars",
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
    for model in (MovimientoScope, Movimiento, Inventario, PresentacionProducto, Lote, Producto, Users, Almacen):
        db.session.query(model).delete()
    db.session.commit()

    almacen1 = Almacen(id=1, nombre="L3 Movimiento Uno")
    almacen2 = Almacen(id=2, nombre="L3 Movimiento Dos")
    almacen3 = Almacen(id=3, nombre="L3 Movimiento Tres")
    db.session.add_all([almacen1, almacen2, almacen3])
    db.session.flush()

    admin = Users(username="mov_admin", password="x", rol="admin", almacen_id=almacen1.id)
    user1 = Users(username="mov_user1", password="x", rol="usuario", almacen_id=almacen1.id)
    user2 = Users(username="mov_user2", password="x", rol="usuario", almacen_id=almacen2.id)
    db.session.add_all([admin, user1, user2])
    db.session.flush()

    producto = Producto(nombre="L3 Movimiento Carbón", precio_compra=Decimal("10.00"))
    db.session.add(producto)
    db.session.flush()

    presentacion = PresentacionProducto(
        producto_id=producto.id,
        nombre="L3 Movimiento Saco",
        capacidad_kg=Decimal("20.00"),
        tipo="procesado",
        precio_venta=Decimal("50.00"),
    )
    db.session.add(presentacion)
    db.session.flush()

    lotes = {}
    for almacen in (almacen1, almacen2, almacen3):
        lote = Lote(
            producto_id=producto.id,
            descripcion=f"L3 Movimiento lote {almacen.id}",
            cantidad_disponible_kg=Decimal("1000.00"),
        )
        db.session.add(lote)
        lotes[almacen.id] = lote
    db.session.flush()

    for almacen in (almacen1, almacen2, almacen3):
        db.session.add(
            Inventario(
                presentacion_id=presentacion.id,
                almacen_id=almacen.id,
                lote_id=lotes[almacen.id].id,
                cantidad=Decimal("100"),
            )
        )
    db.session.commit()

    return {
        "almacenes": {1: almacen1, 2: almacen2, 3: almacen3},
        "lotes": lotes,
        "admin": admin,
        "user1": user1,
        "user2": user2,
        "producto": producto,
        "presentacion": presentacion,
    }


def test_manual_movement_persists_exact_scope(test_client, stock_env):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 1, "entrada", 10
    )

    db.session.expire_all()
    inventario = _inventario(stock_env, 1)
    scope = db.session.get(MovimientoScope, movimiento["id"])
    assert scope is not None
    assert scope.movimiento_id == movimiento["id"]
    assert scope.inventario_id == inventario.id
    assert scope.almacen_id == stock_env["almacenes"][1].id
    assert inventario.cantidad == Decimal("110")


def test_same_product_in_several_warehouses_reverts_only_the_target(test_client, stock_env):
    mov1 = _crear_movimiento_manual(test_client, stock_env, stock_env["admin"], 1, "entrada", 10)
    mov2 = _crear_movimiento_manual(test_client, stock_env, stock_env["admin"], 2, "entrada", 20)
    mov3 = _crear_movimiento_manual(test_client, stock_env, stock_env["admin"], 3, "entrada", 30)

    db.session.expire_all()
    scopes = {
        mov_id: db.session.get(MovimientoScope, mov_id)
        for mov_id in (mov1["id"], mov2["id"], mov3["id"])
    }
    assert scopes[mov1["id"]].almacen_id == stock_env["almacenes"][1].id
    assert scopes[mov2["id"]].almacen_id == stock_env["almacenes"][2].id
    assert scopes[mov3["id"]].almacen_id == stock_env["almacenes"][3].id
    assert _inventario(stock_env, 1).cantidad == Decimal("110")
    assert _inventario(stock_env, 2).cantidad == Decimal("120")
    assert _inventario(stock_env, 3).cantidad == Decimal("130")

    response = test_client.delete(
        f"/movimientos/{mov2['id']}", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 204
    db.session.expire_all()
    assert _inventario(stock_env, 1).cantidad == Decimal("110")
    assert _inventario(stock_env, 2).cantidad == Decimal("100")
    assert _inventario(stock_env, 3).cantidad == Decimal("130")
    assert Movimiento.query.count() == 2
    assert MovimientoScope.query.count() == 2
    assert db.session.get(MovimientoScope, mov2["id"]) is None


def test_manual_output_reversal_restores_exact_inventory(test_client, stock_env):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 2, "salida", 15
    )
    db.session.expire_all()
    assert _inventario(stock_env, 2).cantidad == Decimal("85")

    response = test_client.delete(
        f"/movimientos/{movimiento['id']}", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 204
    db.session.expire_all()
    assert _inventario(stock_env, 2).cantidad == Decimal("100")
    assert Movimiento.query.count() == 0
    assert MovimientoScope.query.count() == 0


def test_legacy_movement_without_scope_returns_409_even_for_admin(test_client, stock_env):
    legacy = Movimiento(
        tipo="entrada",
        presentacion_id=stock_env["presentacion"].id,
        lote_id=stock_env["lotes"][1].id,
        cantidad=Decimal("10"),
        usuario_id=stock_env["admin"].id,
    )
    db.session.add(legacy)
    db.session.commit()

    response = test_client.delete(
        f"/movimientos/{legacy.id}", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 409
    db.session.expire_all()
    assert _inventario(stock_env, 1).cantidad == Decimal("100")
    assert _inventario(stock_env, 2).cantidad == Decimal("100")
    assert _inventario(stock_env, 3).cantidad == Decimal("100")
    assert db.session.get(Movimiento, legacy.id) is not None
    assert MovimientoScope.query.count() == 0


@pytest.mark.parametrize(
    "tipo_operacion",
    ["venta", "produccion", "transferencia", "compra", "ensamblaje", "merma"],
)
def test_derived_movement_is_not_reverted_by_deleting_one_leg(
    test_client, stock_env, tipo_operacion
):
    movimiento = Movimiento(
        tipo="entrada",
        presentacion_id=stock_env["presentacion"].id,
        lote_id=stock_env["lotes"][1].id,
        cantidad=Decimal("10"),
        usuario_id=stock_env["admin"].id,
        tipo_operacion=tipo_operacion,
    )
    db.session.add(movimiento)
    db.session.flush()
    scope = MovimientoScope(
        movimiento_id=movimiento.id,
        inventario_id=_inventario(stock_env, 1).id,
        almacen_id=stock_env["almacenes"][1].id,
    )
    db.session.add(scope)
    db.session.commit()

    response = test_client.delete(
        f"/movimientos/{movimiento.id}", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 409
    db.session.expire_all()
    assert _inventario(stock_env, 1).cantidad == Decimal("100")
    assert db.session.get(Movimiento, movimiento.id) is not None
    assert db.session.get(MovimientoScope, movimiento.id) is not None


def test_reversal_with_insufficient_stock_returns_409_without_clamping(test_client, stock_env):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 1, "entrada", 10
    )
    inventario = _inventario(stock_env, 1)
    inventario.cantidad = Decimal("5")
    db.session.commit()

    response = test_client.delete(
        f"/movimientos/{movimiento['id']}", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 409
    db.session.expire_all()
    assert _inventario(stock_env, 1).cantidad == Decimal("5")
    assert db.session.get(Movimiento, movimiento["id"]) is not None
    assert db.session.get(MovimientoScope, movimiento["id"]) is not None


def test_manual_post_requires_an_existing_inventory(test_client, stock_env):
    lote_sin_inventario = Lote(
        producto_id=stock_env["producto"].id,
        descripcion="L3 sin inventario",
        cantidad_disponible_kg=Decimal("100"),
    )
    db.session.add(lote_sin_inventario)
    db.session.commit()

    payload = _payload_movimiento(stock_env, 1, "entrada", 10, lote_sin_inventario.id)
    response = test_client.post(
        "/movimientos", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 409
    db.session.expire_all()
    assert Movimiento.query.count() == 0
    assert MovimientoScope.query.count() == 0
    assert Inventario.query.filter_by(lote_id=lote_sin_inventario.id).count() == 0
    assert _inventario(stock_env, 1).cantidad == Decimal("100")


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("almacen_id", True),
        ("almacen_id", 1.0),
        ("presentacion_id", 1.0),
        ("presentacion_id", True),
        ("lote_id", "1.0"),
        ("lote_id", False),
    ],
)
def test_manual_post_rejects_non_strict_ids_without_effects(
    test_client, stock_env, campo, valor
):
    payload = _payload_movimiento(stock_env, 1, "entrada", 10)
    payload[campo] = valor
    response = test_client.post(
        "/movimientos", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert Movimiento.query.count() == 0
    assert MovimientoScope.query.count() == 0
    assert _inventario(stock_env, 1).cantidad == Decimal("100")


@pytest.mark.parametrize(
    "cantidad_invalida",
    [0, -1, True, "NaN", "Infinity", "-Infinity", "abc", None],
)
def test_manual_post_rejects_non_positive_or_non_finite_quantities(
    test_client, stock_env, cantidad_invalida
):
    payload = _payload_movimiento(stock_env, 1, "entrada", cantidad_invalida)
    response = test_client.post(
        "/movimientos", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert Movimiento.query.count() == 0
    assert MovimientoScope.query.count() == 0
    assert _inventario(stock_env, 1).cantidad == Decimal("100")


def test_manual_post_authorizes_against_database_actor(test_client, stock_env):
    permitido = test_client.post(
        "/movimientos",
        json=_payload_movimiento(stock_env, 1, "entrada", 10),
        headers=_headers(stock_env["user1"], incluir_claims=False),
    )
    assert permitido.status_code == 201, permitido.get_data(as_text=True)

    rechazado = test_client.post(
        "/movimientos",
        json=_payload_movimiento(stock_env, 2, "entrada", 10),
        headers=_headers(stock_env["user1"], incluir_claims=False),
    )
    assert rechazado.status_code == 403

    db.session.expire_all()
    assert _inventario(stock_env, 1).cantidad == Decimal("110")
    assert _inventario(stock_env, 2).cantidad == Decimal("100")
    assert Movimiento.query.count() == 1
    assert MovimientoScope.query.count() == 1


def test_manual_post_rejects_derived_operation_type(test_client, stock_env):
    payload = _payload_movimiento(stock_env, 1, "entrada", 10)
    payload["tipo_operacion"] = "venta"
    response = test_client.post(
        "/movimientos", json=payload, headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert Movimiento.query.count() == 0
    assert MovimientoScope.query.count() == 0
    assert _inventario(stock_env, 1).cantidad == Decimal("100")


def test_delete_manual_movement_from_foreign_warehouse_is_forbidden(test_client, stock_env):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 2, "entrada", 10
    )

    response = test_client.delete(
        f"/movimientos/{movimiento['id']}", headers=_headers(stock_env["user1"])
    )

    assert response.status_code == 403
    db.session.expire_all()
    assert _inventario(stock_env, 2).cantidad == Decimal("110")
    assert db.session.get(Movimiento, movimiento["id"]) is not None
    assert db.session.get(MovimientoScope, movimiento["id"]) is not None


def test_delete_movement_with_missing_inventory_returns_409(
    test_client, stock_env, monkeypatch
):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 1, "entrada", 10
    )
    scope = db.session.get(MovimientoScope, movimiento["id"])
    inventario_id = scope.inventario_id
    almacen_id_antes = scope.almacen_id
    inventario = db.session.get(Inventario, inventario_id)
    assert inventario is not None
    cantidad_antes = inventario.cantidad
    movimientos_antes = Movimiento.query.count()
    scopes_antes = MovimientoScope.query.count()

    one_or_none_original = SAQuery.one_or_none

    def one_or_none_inventario_ausente(query, *args, **kwargs):
        entidades = [
            descripcion.get("entity")
            for descripcion in query.column_descriptions
            if isinstance(descripcion, dict)
        ]
        if Inventario in entidades:
            return None
        return one_or_none_original(query, *args, **kwargs)

    with monkeypatch.context() as contexto:
        contexto.setattr(SAQuery, "one_or_none", one_or_none_inventario_ausente)
        response = test_client.delete(
            f"/movimientos/{movimiento['id']}", headers=_headers(stock_env["admin"])
        )

    assert response.status_code == 409
    db.session.expire_all()
    inventario_despues = db.session.get(Inventario, inventario_id)
    assert inventario_despues is not None
    assert inventario_despues.cantidad == cantidad_antes
    assert db.session.get(Movimiento, movimiento["id"]) is not None
    scope_despues = db.session.get(MovimientoScope, movimiento["id"])
    assert scope_despues is not None
    assert scope_despues.inventario_id == inventario_id
    assert scope_despues.almacen_id == almacen_id_antes
    assert Movimiento.query.count() == movimientos_antes
    assert MovimientoScope.query.count() == scopes_antes


def test_movimiento_scope_fk_blocks_real_inventory_delete(test_client, stock_env):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 1, "entrada", 10
    )
    scope = db.session.get(MovimientoScope, movimiento["id"])
    inventario_id = scope.inventario_id
    inventario = db.session.get(Inventario, inventario_id)
    assert inventario is not None

    if db.engine.dialect.name == "postgresql":
        with pytest.raises(IntegrityError):
            db.session.delete(inventario)
            db.session.commit()
        db.session.rollback()
        db.session.expire_all()
        assert db.session.get(Inventario, inventario_id) is not None
        return

    db.session.rollback()
    db.session.execute(text("PRAGMA foreign_keys=ON"))
    fk_activa = db.session.execute(text("PRAGMA foreign_keys")).scalar()
    db.session.commit()

    if fk_activa:
        try:
            with pytest.raises(IntegrityError):
                db.session.delete(inventario)
                db.session.commit()
            db.session.rollback()
            db.session.expire_all()
            assert db.session.get(Inventario, inventario_id) is not None
        finally:
            db.session.rollback()
            db.session.execute(text("PRAGMA foreign_keys=OFF"))
            db.session.commit()
    else:
        fk_inventario = next(
            fk
            for fk in MovimientoScope.__table__.foreign_keys
            if fk.parent.name == "inventario_id"
        )
        assert fk_inventario.ondelete == "RESTRICT"
        db.session.expire_all()
        assert db.session.get(Inventario, inventario_id) is not None


def test_delete_movement_with_mismatched_scope_returns_409(test_client, stock_env):
    movimiento = _crear_movimiento_manual(
        test_client, stock_env, stock_env["admin"], 1, "entrada", 10
    )
    scope = db.session.get(MovimientoScope, movimiento["id"])
    inventario_distinto = _inventario(stock_env, 2)
    scope.inventario_id = inventario_distinto.id
    db.session.commit()

    response = test_client.delete(
        f"/movimientos/{movimiento['id']}", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 409
    db.session.expire_all()
    assert _inventario(stock_env, 1).cantidad == Decimal("110")
    assert _inventario(stock_env, 2).cantidad == Decimal("100")
    assert db.session.get(Movimiento, movimiento["id"]) is not None


def test_delete_missing_movement_returns_404_not_500(test_client, stock_env):
    response = test_client.delete(
        "/movimientos/999999", headers=_headers(stock_env["admin"])
    )

    assert response.status_code == 404
