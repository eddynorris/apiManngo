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

from sqlalchemy import event

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



def test_reclamo_de_un_update_mas_antiguo_espera_al_reclamo_en_curso_del_mismo_chat():
    """Carrera de READ COMMITTED (revisión de A08): B (más nuevo) está reclamado pero sin
    commit cuando llega A (más antiguo) del mismo chat. Sin advisory lock, A también quedaría
    'procesando' y se solaparían. Con el lock, A espera al commit de B y no se reclama."""
    _require_pg_url()
    base = int(uuid.uuid4().int % 10**12) + 2 * 10**12
    id_a, id_b = base, base + 1
    chat_id = base % 10**9 + 7

    def _registrar(uid):
        telegram_inbox.registrar_update({
            'update_id': uid,
            'message': {'chat': {'id': chat_id, 'type': 'private'}, 'text': 'x'},
        })

    with app.app_context():
        db.create_all()
        _registrar(id_b)  # B llega primero

    b_sin_commit = threading.Event()
    soltar_b = threading.Event()
    resultados = {}

    def _reclamar_b():
        with app.app_context():
            try:
                def _retener(_session):
                    b_sin_commit.set()
                    soltar_b.wait(10)
                event.listen(db.session(), 'before_commit', _retener, once=True)
                resultados['b'] = telegram_inbox.reclamar(id_b)
            finally:
                db.session.remove()

    def _reclamar_a():
        with app.app_context():
            try:
                resultados['a'] = telegram_inbox.reclamar(id_a)
            finally:
                db.session.remove()

    try:
        hilo_b = threading.Thread(target=_reclamar_b)
        hilo_b.start()
        assert b_sin_commit.wait(10), 'B no llegó a reclamar'
        with app.app_context():
            _registrar(id_a)  # A (más antiguo) llega mientras B no ha hecho commit
            db.session.remove()
        hilo_a = threading.Thread(target=_reclamar_a)
        hilo_a.start()
        hilo_a.join(1.0)  # con el lock, A sigue esperando a B
        soltar_b.set()
        hilo_b.join(10)
        hilo_a.join(10)

        assert resultados == {'b': True, 'a': False}, resultados
        with app.app_context():
            assert db.session.get(TelegramUpdate, id_a).estado == telegram_inbox.RECIBIDO
            db.session.remove()
    finally:
        soltar_b.set()
        with app.app_context():
            db.session.query(TelegramUpdate).filter(TelegramUpdate.update_id.in_([id_a, id_b])).delete(
                synchronize_session=False)
            db.session.commit()
            db.session.remove()
