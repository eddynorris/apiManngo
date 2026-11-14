"""A08 en PostgreSQL: el reclamo de un update es atómico entre conexiones concurrentes.

Opt-in igual que el resto de tests de integración:
    MANNGO_RUN_PG_TESTS=1
    MANNGO_LOCAL_POSTGRES_URL=postgresql://.../manngo_test_...
    DATABASE_URL=<la misma URL>
    scripts/run_tests_offline.py --allow-local-postgres ...
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app import app
from extensions import db
from models import TelegramUpdate
from services import telegram_inbox
from tests.integration.test_pago_concurrency_postgres import _require_pg_url

HILOS = 8


def test_un_mismo_update_solo_lo_reclama_un_hilo():
    _require_pg_url()
    update_id = int(uuid.uuid4().int % 10**12) + 10**12  # único y > 0
    with app.app_context():
        assert db.engine.dialect.name == 'postgresql'
        db.create_all()
        telegram_inbox.registrar_update({
            'update_id': update_id,
            'message': {'chat': {'id': update_id % 10**9 + 1, 'type': 'private'}, 'text': 'x'},
        })

    barrera = threading.Barrier(HILOS, timeout=15)

    def _intento(_):
        barrera.wait()
        with app.app_context():
            try:
                return telegram_inbox.reclamar(update_id)
            finally:
                db.session.remove()

    try:
        with ThreadPoolExecutor(max_workers=HILOS) as executor:
            resultados = list(executor.map(_intento, range(HILOS)))
        assert resultados.count(True) == 1, resultados
        with app.app_context():
            fila = db.session.get(TelegramUpdate, update_id)
            assert fila.estado == telegram_inbox.PROCESANDO
            assert fila.intentos == 1
    finally:
        with app.app_context():
            db.session.query(TelegramUpdate).filter_by(update_id=update_id).delete()
            db.session.commit()
            db.session.remove()
