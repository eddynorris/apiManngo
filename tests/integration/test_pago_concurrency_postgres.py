"""A11 · regresión de concurrencia de pagos en PostgreSQL local.

Solo se ejecuta con autorización explícita y una base PostgreSQL desechable:

    MANNGO_RUN_PG_TESTS=1
    MANNGO_LOCAL_POSTGRES_URL=postgresql://.../manngo_test_...
    DATABASE_URL=<la misma URL>
    scripts/run_tests_offline.py --allow-local-postgres ...

Sin esa autorización se omite únicamente este bloque de integración. No se
imprime la URL ni se abren bases remotas.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func
from werkzeug.security import generate_password_hash

from app import app
from extensions import db
from models import Almacen, Cliente, Pago, Users, Venta
from scripts.offline_test_env import UnsafeTestDatabaseError, validate_testing_database_url
from services.pago_service import PagoService, PagoValidationError

PG_GATE = "MANNGO_RUN_PG_TESTS"
PG_URL_ENV = "MANNGO_LOCAL_POSTGRES_URL"
PG_AUTHORIZATION_ENV = "MANNGO_ALLOW_LOCAL_POSTGRES_TESTS"


def _require_pg_url() -> str:
    if os.environ.get(PG_GATE, "").strip() != "1":
        pytest.skip(
            "PostgreSQL concurrency test is opt-in: set MANNGO_RUN_PG_TESTS=1 "
            "and MANNGO_LOCAL_POSTGRES_URL, then run with --allow-local-postgres"
        )

    raw_url = os.environ.get(PG_URL_ENV, "").strip()
    if not raw_url:
        pytest.fail("MANNGO_RUN_PG_TESTS=1 requires MANNGO_LOCAL_POSTGRES_URL")

    try:
        validate_testing_database_url(raw_url, allow_local_postgres=True)
    except UnsafeTestDatabaseError as error:
        pytest.fail(f"Refusing unsafe PostgreSQL test URL: {error}")

    if os.environ.get("DATABASE_URL", "").strip() != raw_url:
        pytest.fail("DATABASE_URL must equal MANNGO_LOCAL_POSTGRES_URL")
    if os.environ.get(PG_AUTHORIZATION_ENV, "").strip() != "1":
        pytest.fail("Run with --allow-local-postgres for disposable PostgreSQL")

    return raw_url


def test_concurrent_payments_cannot_exceed_sale_total():
    _require_pg_url()

    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        assert db.engine.dialect.name == "postgresql", (
            "This test requires the application to use the disposable PostgreSQL database"
        )
        db.create_all()

        almacen = Almacen(nombre=f"Almacén concurrencia {suffix}")
        cliente = Cliente(nombre=f"Cliente concurrencia {suffix}")
        usuario = Users(
            username=f"user_conc_{suffix}",
            password=generate_password_hash("synthetic-password"),
            rol="admin",
            almacen_id=None,
        )
        db.session.add_all([almacen, cliente, usuario])
        db.session.flush()
        usuario.almacen_id = almacen.id

        venta = Venta(
            cliente_id=cliente.id,
            almacen_id=almacen.id,
            vendedor_id=usuario.id,
            total=Decimal("100.00"),
            tipo_pago="credito",
            estado_pago="pendiente",
            estado="completado",
            fecha=datetime.now(UTC),
        )
        db.session.add(venta)
        db.session.commit()

        ids = {
            "almacen": almacen.id,
            "cliente": cliente.id,
            "usuario": usuario.id,
            "venta": venta.id,
        }

    barrier = threading.Barrier(2, timeout=15)
    original_validar = PagoService._validar_monto_venta

    def _validar_con_ventana(cls, venta_obj, monto, pago_existente_id=None):
        original_validar(venta_obj, monto, pago_existente_id)
        # Amplía la ventana entre la lectura del saldo y la inserción. Con la
        # política correcta, el segundo hilo ya está bloqueado en el row lock.
        threading.Event().wait(0.05)

    PagoService._validar_monto_venta = classmethod(_validar_con_ventana)

    def _worker():
        barrier.wait()
        with app.app_context():
            try:
                nuevo_pago = Pago(
                    venta_id=ids["venta"],
                    monto=Decimal("70.00"),
                    metodo_pago="efectivo",
                    fecha=datetime.now(UTC),
                )
                PagoService.create_pago(
                    nuevo_pago, None, ids["usuario"], actor_id=ids["usuario"]
                )
                db.session.commit()
                return "created"
            except PagoValidationError:
                db.session.rollback()
                return "rejected"
            finally:
                db.session.remove()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(lambda _: _worker(), range(2)))

        with app.app_context():
            total = db.session.query(
                func.coalesce(func.sum(Pago.monto), 0)
            ).filter(Pago.venta_id == ids["venta"]).scalar()
            pagos = Pago.query.filter_by(venta_id=ids["venta"]).all()

            assert resultados.count("created") == 1, resultados
            assert resultados.count("rejected") == 1, resultados
            assert len(pagos) == 1
            assert Decimal(str(total)) <= Decimal("100.00")
    finally:
        PagoService._validar_monto_venta = staticmethod(original_validar)
        with app.app_context():
            db.session.rollback()
            Pago.query.filter_by(venta_id=ids["venta"]).delete()
            db.session.get(Venta, ids["venta"]) and db.session.delete(
                db.session.get(Venta, ids["venta"])
            )
            db.session.get(Cliente, ids["cliente"]) and db.session.delete(
                db.session.get(Cliente, ids["cliente"])
            )
            db.session.get(Users, ids["usuario"]) and db.session.delete(
                db.session.get(Users, ids["usuario"])
            )
            db.session.get(Almacen, ids["almacen"]) and db.session.delete(
                db.session.get(Almacen, ids["almacen"])
            )
            db.session.commit()
            db.session.remove()
