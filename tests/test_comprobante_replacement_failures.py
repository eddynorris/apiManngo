import io
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

from extensions import db
from models import Almacen, Cliente, Pago, StorageCleanupTask, Users, Venta
from services.pago_service import PagoService
from services.storage_cleanup_service import (
    commit_with_storage_finalization,
    process_pending_storage_cleanups,
    rollback_and_compensate,
)


@pytest.fixture(autouse=True)
def _isolate_pago_storage_data(test_client):
    db.session.query(StorageCleanupTask).delete()
    db.session.query(Pago).delete()
    db.session.query(Venta).delete()
    db.session.query(Cliente).delete()
    db.session.query(Users).delete()
    db.session.query(Almacen).delete()
    db.session.commit()
    yield


def _file():
    return FileStorage(
        stream=io.BytesIO(b"%PDF-1.4\n%%EOF\n"),
        filename="nuevo.pdf",
        content_type="application/pdf",
    )


def _records(old_key=None, second_pago=False):
    suffix = uuid.uuid4().hex[:10]
    almacen = Almacen(nombre=f"Almacen {suffix}")
    usuario = Users(
        username=f"user_{suffix}",
        password=generate_password_hash("secret123"),
        rol="admin",
        almacen_id=None,
    )
    cliente = Cliente(nombre=f"Cliente {suffix}")
    db.session.add_all([almacen, usuario, cliente])
    db.session.flush()
    usuario.almacen_id = almacen.id
    venta = Venta(
        cliente_id=cliente.id,
        almacen_id=almacen.id,
        vendedor_id=usuario.id,
        total=Decimal("100.00"),
        tipo_pago="credito",
        estado_pago="pendiente",
        fecha=datetime.now(timezone.utc),
    )
    db.session.add(venta)
    db.session.flush()
    pago = Pago(
        venta_id=venta.id,
        usuario_id=usuario.id,
        monto=Decimal("50.00"),
        metodo_pago="transferencia",
        fecha=datetime.now(timezone.utc),
        url_comprobante=old_key,
    )
    db.session.add(pago)
    pagos = [pago]
    if second_pago:
        otro = Pago(
            venta_id=venta.id,
            usuario_id=usuario.id,
            monto=Decimal("50.00"),
            metodo_pago="transferencia",
            fecha=datetime.now(timezone.utc),
            url_comprobante=old_key,
        )
        db.session.add(otro)
        pagos.append(otro)
    db.session.commit()
    return {
        "usuario": usuario,
        "venta": venta,
        "pago": pago,
        "pagos": pagos,
        "old_key": old_key,
    }


def _fresh_pago(pago_id):
    db.session.remove()
    return db.session.get(Pago, pago_id)


class _RecordingBucket:
    def __init__(self, events):
        self.events = events

    def upload(self, path, file, file_options=None):
        self.events.append(f"upload:{path}")
        return {"path": path}


class _RecordingStorage:
    def __init__(self, events):
        self.events = events

    def from_(self, bucket):
        return _RecordingBucket(self.events)


class _RecordingSupabase:
    def __init__(self, events):
        self.storage = _RecordingStorage(events)


def test_replacement_with_fake_storage_records_upload_commit_delete_order(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    events = []

    def fake_delete(key):
        persisted = db.session.get(Pago, pago_id)
        assert key == "comprobantes/viejo.pdf"
        assert persisted.url_comprobante.startswith("comprobantes/comprobantes/nuevo_")
        events.append(f"delete:{key}")
        return True

    with patch("utils.file_handlers.supabase", _RecordingSupabase(events)), \
            patch("utils.file_handlers.delete_file", side_effect=fake_delete):
        PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        new_key = db.session.get(Pago, pago_id).url_comprobante
        commit_with_storage_finalization()

    assert events[0].startswith("upload:")
    assert events[-1] == "delete:comprobantes/viejo.pdf"
    assert _fresh_pago(pago_id).url_comprobante == new_key


def test_replacement_uploads_before_commit_and_deletes_after(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    new_key = "comprobantes/nuevo.pdf"
    deleted = []

    def fake_delete(key):
        persisted = db.session.get(Pago, pago_id)
        assert persisted.url_comprobante == new_key
        deleted.append(key)
        return True

    with patch("services.pago_service.save_file", return_value=new_key), \
            patch("utils.file_handlers.delete_file", side_effect=fake_delete):
        PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        commit_with_storage_finalization()

    assert deleted == ["comprobantes/viejo.pdf"]
    assert _fresh_pago(pago_id).url_comprobante == new_key
    assert StorageCleanupTask.query.count() == 0


def test_failed_commit_preserves_old_reference_and_compensates_new_upload(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    new_key = "comprobantes/nuevo.pdf"
    deleted = []

    def fake_delete(key):
        deleted.append(key)
        return True

    with patch("services.pago_service.save_file", return_value=new_key), \
            patch("utils.file_handlers.delete_file", side_effect=fake_delete), \
            patch.object(db.session, "commit", side_effect=RuntimeError("commit failed")):
        PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        with pytest.raises(RuntimeError, match="commit failed"):
            commit_with_storage_finalization()

    persisted = _fresh_pago(pago_id)
    assert persisted.url_comprobante == "comprobantes/viejo.pdf"
    assert deleted == [new_key]
    assert StorageCleanupTask.query.count() == 0


def test_failed_commit_and_failed_compensation_keeps_cleanup_task(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    new_key = "comprobantes/nuevo.pdf"

    original_commit = db.session.commit
    attempts = {"count": 0}

    def flaky_commit():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("commit failed")
        return original_commit()

    with patch("services.pago_service.save_file", return_value=new_key), \
            patch("utils.file_handlers.delete_file", return_value=False), \
            patch.object(db.session, "commit", side_effect=flaky_commit):
        PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        with pytest.raises(RuntimeError, match="commit failed"):
            commit_with_storage_finalization()

    assert _fresh_pago(pago_id).url_comprobante == "comprobantes/viejo.pdf"
    task = StorageCleanupTask.query.filter_by(storage_key=new_key).one()
    assert task.reason == "compensation"

    with patch("utils.file_handlers.delete_file", return_value=True):
        result = process_pending_storage_cleanups(force=True)

    assert result["deleted"] == 1
    assert StorageCleanupTask.query.filter_by(storage_key=new_key).count() == 0


def test_failed_upload_leaves_old_reference_untouched(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    deleted = []

    with patch("services.pago_service.save_file", return_value=None), \
            patch("utils.file_handlers.delete_file", side_effect=lambda key: deleted.append(key) or True):
        with pytest.raises(Exception, match="Error al subir"):
            PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        rollback_and_compensate()

    assert deleted == []
    assert _fresh_pago(pago_id).url_comprobante == "comprobantes/viejo.pdf"
    assert StorageCleanupTask.query.count() == 0


def test_failed_old_delete_keeps_durable_retry_task(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    new_key = "comprobantes/nuevo.pdf"

    with patch("services.pago_service.save_file", return_value=new_key), \
            patch("utils.file_handlers.delete_file", return_value=False):
        PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        commit_with_storage_finalization()

    assert StorageCleanupTask.query.count() == 1
    persisted = _fresh_pago(pago_id)
    assert persisted.url_comprobante == new_key
    task = StorageCleanupTask.query.filter_by(storage_key="comprobantes/viejo.pdf").one()
    assert task.attempts >= 1
    assert task.last_error == "delete_failed"

    with patch("utils.file_handlers.delete_file", return_value=True):
        result = process_pending_storage_cleanups(force=True)

    assert result["deleted"] == 1
    assert StorageCleanupTask.query.filter_by(storage_key="comprobantes/viejo.pdf").count() == 0


def test_shared_old_key_is_not_deleted(test_client):
    data = _records(old_key="comprobantes/compartido.pdf", second_pago=True)
    pago_id = data["pago"].id
    other_id = data["pagos"][1].id
    new_key = "comprobantes/nuevo.pdf"
    deleted = []

    with patch("services.pago_service.save_file", return_value=new_key), \
            patch("utils.file_handlers.delete_file", side_effect=lambda key: deleted.append(key) or True):
        PagoService.update_pago(db.session.get(Pago, pago_id), _file(), False)
        commit_with_storage_finalization()

    assert deleted == []
    assert _fresh_pago(pago_id).url_comprobante == new_key
    assert _fresh_pago(other_id).url_comprobante == "comprobantes/compartido.pdf"
    assert StorageCleanupTask.query.count() == 0


def test_delete_pago_defers_storage_removal_until_commit(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    deleted = []

    def fake_delete(key):
        assert db.session.get(Pago, pago_id) is None
        deleted.append(key)
        return True

    with patch("utils.file_handlers.delete_file", side_effect=fake_delete):
        PagoService.delete_pago(pago_id)
        commit_with_storage_finalization()

    assert deleted == ["comprobantes/viejo.pdf"]
    assert _fresh_pago(pago_id) is None
    assert StorageCleanupTask.query.count() == 0


def test_batch_validation_failure_does_not_upload_comprobante(test_client):
    data = _records()
    venta_id = data["venta"].id
    usuario_id = data["usuario"].id
    new_key = "comprobantes/lote.pdf"
    deleted = []

    with patch("services.pago_service.save_file", return_value=new_key) as mock_save_file, \
            patch("utils.file_handlers.delete_file", side_effect=lambda key: deleted.append(key) or True):
        with pytest.raises(Exception, match="excede el saldo pendiente"):
            PagoService.create_batch_pagos(
                json.dumps([{"venta_id": venta_id, "monto": "999.00"}]),
                _file(),
                "2026-10-22T10:00:00",
                "transferencia",
                "BATCH",
                usuario_id,
                "admin",
                data["usuario"].almacen_id,
            )
        rollback_and_compensate()

    mock_save_file.assert_not_called()
    assert deleted == []
    assert Pago.query.filter_by(venta_id=venta_id, url_comprobante=new_key).count() == 0
    assert StorageCleanupTask.query.count() == 0


def test_deposit_replacement_uses_new_comprobante_before_deleting_old(test_client):
    data = _records(old_key="comprobantes/viejo.pdf")
    pago_id = data["pago"].id
    new_key = "comprobantes/deposito.pdf"
    deleted = []

    login = test_client.post(
        "/auth",
        json={"username": data["usuario"].username, "password": "secret123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["access_token"]

    def fake_delete(key):
        persisted = db.session.get(Pago, pago_id)
        assert persisted.url_comprobante == new_key
        deleted.append(key)
        return True

    with patch("resources.pago_resource.save_file", return_value=new_key), \
            patch("utils.file_handlers.delete_file", side_effect=fake_delete):
        response = test_client.post(
            "/pagos/depositos",
            headers={"Authorization": f"Bearer {token}"},
            content_type="multipart/form-data",
            data={
                "depositos": json.dumps([{"pago_id": pago_id, "monto_depositado": "10.00"}]),
                "fecha_deposito": "2026-10-22T10:00:00",
                "comprobante_deposito": (io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "deposito.pdf"),
            },
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert deleted == ["comprobantes/viejo.pdf"]
    persisted = _fresh_pago(pago_id)
    assert persisted.url_comprobante == new_key
    assert StorageCleanupTask.query.count() == 0
