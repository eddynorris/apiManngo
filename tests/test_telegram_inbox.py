"""A08: entrega duradera del webhook de Telegram (semántica «como máximo una vez»)."""
from datetime import UTC, datetime, timedelta

import pytest

from extensions import db
from models import TelegramUpdate
from resources import telegram_webhook_resource as webhook_module
from services import telegram_inbox
from telegram.router import TelegramRouter

TOKEN_RUTA = 'ruta-secreta-de-prueba'
SECRETO_CABECERA = 'cabecera-secreta-de-prueba-0123456789'
URL = f'/telegram/webhook/{TOKEN_RUTA}'
CABECERAS = {'X-Telegram-Bot-Api-Secret-Token': SECRETO_CABECERA}


def _mensaje(update_id, chat_id=555, texto='hola'):
    return {
        'update_id': update_id,
        'message': {'chat': {'id': chat_id, 'type': 'private'}, 'from': {'id': chat_id}, 'text': texto},
    }


def _callback(update_id, chat_id=555):
    return {
        'update_id': update_id,
        'callback_query': {
            'id': 'cb', 'from': {'id': chat_id}, 'data': 'cancel',
            'message': {'message_id': 1, 'chat': {'id': chat_id, 'type': 'private'}},
        },
    }


@pytest.fixture()
def bandeja(test_client, monkeypatch):
    db.session.query(TelegramUpdate).delete()
    db.session.commit()
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', TOKEN_RUTA)
    monkeypatch.setenv('TELEGRAM_BOT_SECRET_TOKEN', SECRETO_CABECERA)

    llamadas = []
    monkeypatch.setattr(TelegramRouter, 'handle_message', staticmethod(lambda m: llamadas.append(('message', m['text']))))
    monkeypatch.setattr(TelegramRouter, 'handle_callback_query', staticmethod(lambda c: llamadas.append(('callback', c['data']))))
    programados = []
    monkeypatch.setattr(webhook_module, 'programar_procesamiento', lambda app, uid: programados.append(uid))
    avisos = []
    from services.telegram_service import telegram_service
    monkeypatch.setattr(telegram_service, 'send_message', lambda chat_id, text, *a, **k: avisos.append((chat_id, text)))

    yield {'llamadas': llamadas, 'programados': programados, 'avisos': avisos}
    db.session.query(TelegramUpdate).delete()
    db.session.commit()


def _fila(update_id):
    db.session.expire_all()
    return db.session.get(TelegramUpdate, update_id)


# --- Webhook: secretos y registro ---------------------------------------------------------

def test_sin_secreto_configurado_se_rechaza(test_client, bandeja, monkeypatch):
    monkeypatch.delenv('TELEGRAM_WEBHOOK_SECRET')
    resp = test_client.post(URL, json=_mensaje(1), headers=CABECERAS)
    assert resp.status_code == 403
    assert TelegramUpdate.query.count() == 0


@pytest.mark.parametrize('url, cabeceras', [
    ('/telegram/webhook/otro-token', CABECERAS),
    (URL, {}),
    (URL, {'X-Telegram-Bot-Api-Secret-Token': 'incorrecta'}),
    (URL, {'X-Telegram-Bot-Api-Secret-Token': 'ñandú-no-ascii'}),
])
def test_secretos_invalidos_no_registran_ni_procesan(test_client, bandeja, url, cabeceras):
    resp = test_client.post(url, json=_mensaje(1), headers=cabeceras)
    assert resp.status_code == 403
    assert TelegramUpdate.query.count() == 0
    assert bandeja['programados'] == []


def test_update_valido_se_guarda_antes_de_responder_y_se_programa(test_client, bandeja):
    resp = test_client.post(URL, json=_mensaje(10), headers=CABECERAS)
    assert resp.status_code == 200
    fila = _fila(10)
    assert fila.estado == telegram_inbox.RECIBIDO
    assert fila.chat_id == 555
    assert fila.payload['message']['text'] == 'hola'
    assert bandeja['programados'] == [10]


def test_update_duplicado_responde_200_sin_reprogramar(test_client, bandeja):
    assert test_client.post(URL, json=_mensaje(11), headers=CABECERAS).status_code == 200
    resp = test_client.post(URL, json=_mensaje(11, texto='otro'), headers=CABECERAS)
    assert resp.status_code == 200
    assert bandeja['programados'] == [11]
    assert _fila(11).payload['message']['text'] == 'hola'


@pytest.mark.parametrize('cuerpo', [None, [], {'sin_update_id': 1}, {'update_id': 'x'}, {'update_id': True}, {'update_id': -3}])
def test_cuerpo_inutilizable_se_ignora_con_200(test_client, bandeja, cuerpo):
    resp = test_client.post(URL, json=cuerpo, headers=CABECERAS)
    assert resp.status_code == 200
    assert TelegramUpdate.query.count() == 0


def test_sin_secreto_de_cabecera_configurado_basta_el_token_de_ruta(test_client, bandeja, monkeypatch):
    monkeypatch.delenv('TELEGRAM_BOT_SECRET_TOKEN')
    assert test_client.post(URL, json=_mensaje(12)).status_code == 200
    assert _fila(12).estado == telegram_inbox.RECIBIDO


# --- Procesador ---------------------------------------------------------------------------

def test_procesar_despacha_mensaje_y_callback_una_sola_vez(test_client, bandeja):
    telegram_inbox.registrar_update(_mensaje(20, texto='uno'))
    telegram_inbox.registrar_update(_callback(21, chat_id=777))
    assert telegram_inbox.procesar(20) == 1
    assert telegram_inbox.procesar(21) == 1
    assert telegram_inbox.procesar(20) == 0  # ya procesado: no se repite
    assert bandeja['llamadas'] == [('message', 'uno'), ('callback', 'cancel')]
    assert _fila(20).estado == telegram_inbox.PROCESADO
    assert _fila(20).intentos == 1


def test_error_en_el_router_marca_fallido_sin_guardar_el_texto(test_client, bandeja, monkeypatch):
    def _explota(message):
        raise RuntimeError(f"detalle sensible {message['text']}")
    monkeypatch.setattr(TelegramRouter, 'handle_message', staticmethod(_explota))
    telegram_inbox.registrar_update(_mensaje(30, texto='DNI 12345678'))
    telegram_inbox.procesar(30)
    fila = _fila(30)
    assert fila.estado == telegram_inbox.FALLIDO
    assert fila.ultimo_error.startswith('RuntimeError [')
    assert '12345678' not in fila.ultimo_error


def test_orden_por_chat_no_se_adelanta_ni_se_solapa(test_client, bandeja):
    telegram_inbox.registrar_update(_mensaje(40, texto='primero'))
    telegram_inbox.registrar_update(_mensaje(41, texto='segundo'))
    telegram_inbox.registrar_update(_mensaje(42, chat_id=999, texto='otro chat'))

    assert telegram_inbox.reclamar(41) is False  # hay uno más antiguo del mismo chat
    assert telegram_inbox.reclamar(40) is True
    assert telegram_inbox.reclamar(41) is False  # el chat tiene uno procesando
    assert telegram_inbox.reclamar(42) is True   # otro chat no se bloquea


def test_al_terminar_sigue_con_el_siguiente_del_mismo_chat(test_client, bandeja):
    for uid, texto in [(50, 'a'), (51, 'b'), (52, 'c')]:
        telegram_inbox.registrar_update(_mensaje(uid, texto=texto))
    assert telegram_inbox.procesar(50) == 3
    assert bandeja['llamadas'] == [('message', 'a'), ('message', 'b'), ('message', 'c')]


# --- Recuperación (cron) ------------------------------------------------------------------

def _envejecer(update_ids, minutos, ahora):
    viejo = ahora - timedelta(minutes=minutos)
    db.session.query(TelegramUpdate).filter(TelegramUpdate.update_id.in_(update_ids)).update(
        {'recibido_en': viejo, 'actualizado_en': viejo}, synchronize_session=False)
    db.session.commit()


def test_pendientes_procesa_recientes_abandona_colgados_y_caducados(test_client, bandeja):
    ahora = datetime.now(UTC)
    telegram_inbox.registrar_update(_mensaje(60, chat_id=111, texto='nunca empezó'))
    telegram_inbox.registrar_update(_mensaje(61, chat_id=222, texto='colgado'))
    telegram_inbox.registrar_update(_mensaje(62, chat_id=333, texto='procesando reciente'))
    telegram_inbox.registrar_update(_mensaje(63, chat_id=444, texto='recién llegado'))
    telegram_inbox.registrar_update(_callback(64, chat_id=555))  # confirm tardío
    for uid in (61, 62):
        assert telegram_inbox.reclamar(uid)
    _envejecer([60], 5, ahora)
    _envejecer([61, 64], 30, ahora)

    resumen = telegram_inbox.procesar_pendientes(ahora=ahora)

    assert resumen == {'abandonados': 2, 'procesados': 1, 'errores': 0, 'purgados': 0}
    assert _fila(60).estado == telegram_inbox.PROCESADO
    assert _fila(61).estado == telegram_inbox.ABANDONADO  # no se reintenta: como máximo una vez
    assert _fila(62).estado == telegram_inbox.PROCESANDO
    assert _fila(63).estado == telegram_inbox.RECIBIDO
    assert _fila(64).estado == telegram_inbox.ABANDONADO  # demasiado antiguo para ejecutarlo
    assert bandeja['llamadas'] == [('message', 'nunca empezó')]
    assert sorted(bandeja['avisos']) == [
        (222, telegram_inbox.AVISO_ABANDONADO),
        (555, telegram_inbox.AVISO_CADUCADO),
    ]


def test_pendientes_purga_filas_terminadas_antiguas(test_client, bandeja):
    ahora = datetime.now(UTC)
    for uid in (70, 71, 72):
        telegram_inbox.registrar_update(_mensaje(uid, chat_id=uid))
        telegram_inbox.procesar(uid)
    _envejecer([70, 71], 60 * 24 * 40, ahora)
    db.session.query(TelegramUpdate).filter_by(update_id=71).update({'estado': telegram_inbox.FALLIDO})
    db.session.commit()

    resumen = telegram_inbox.procesar_pendientes(ahora=ahora)

    assert resumen['purgados'] == 2
    assert _fila(70) is None and _fila(71) is None
    assert _fila(72).estado == telegram_inbox.PROCESADO


def test_un_update_problematico_no_detiene_la_recuperacion(test_client, bandeja, monkeypatch):
    ahora = datetime.now(UTC)
    for uid in (80, 81):
        telegram_inbox.registrar_update(_mensaje(uid, chat_id=uid))
    _envejecer([80, 81], 5, ahora)
    original = telegram_inbox.procesar

    def _procesar(update_id):
        if update_id == 80:
            raise RuntimeError('fallo de infraestructura')
        return original(update_id)
    monkeypatch.setattr(telegram_inbox, 'procesar', _procesar)

    resumen = telegram_inbox.procesar_pendientes(ahora=ahora)

    assert resumen['errores'] == 1 and resumen['procesados'] == 1
    assert _fila(81).estado == telegram_inbox.PROCESADO


def test_marcar_no_sobrescribe_un_update_ya_abandonado(test_client, bandeja, monkeypatch):
    def _lento(message):
        # mientras el router trabaja, el cron lo da por abandonado
        db.session.query(TelegramUpdate).filter_by(update_id=90).update(
            {'estado': telegram_inbox.ABANDONADO}, synchronize_session=False)
        db.session.commit()
    monkeypatch.setattr(TelegramRouter, 'handle_message', staticmethod(_lento))
    telegram_inbox.registrar_update(_mensaje(90))
    telegram_inbox.procesar(90)
    assert _fila(90).estado == telegram_inbox.ABANDONADO


def test_sesion_rota_por_el_router_no_deja_el_update_colgado(test_client, bandeja, monkeypatch):
    def _rompe_sesion(message):
        db.session.add(TelegramUpdate(update_id=95, estado='recibido'))  # PK duplicada
        try:
            db.session.flush()
        except Exception:
            pass  # el router se traga el error sin rollback
    monkeypatch.setattr(TelegramRouter, 'handle_message', staticmethod(_rompe_sesion))
    telegram_inbox.registrar_update(_mensaje(95))
    telegram_inbox.registrar_update(_mensaje(96, texto='siguiente'))
    monkeypatch.setattr(TelegramRouter, 'handle_message', staticmethod(_rompe_sesion))

    telegram_inbox.procesar(95)

    assert _fila(95).estado == telegram_inbox.PROCESADO
    assert _fila(96).estado == telegram_inbox.PROCESADO  # el chat no queda bloqueado


def test_logs_de_error_no_incluyen_el_texto_del_usuario(test_client, bandeja, monkeypatch, caplog):
    def _explota(message):
        raise ValueError(f"no entendí {message['text']}")
    monkeypatch.setattr(TelegramRouter, 'handle_message', staticmethod(_explota))
    telegram_inbox.registrar_update(_mensaje(97, texto='mi DNI es 87654321'))
    with caplog.at_level('DEBUG'):
        telegram_inbox.procesar(97)
    assert 'ValueError' in caplog.text
    assert '87654321' not in caplog.text


def test_comando_cli_telegram_pendientes(test_client, bandeja):
    from app import app
    resultado = app.test_cli_runner().invoke(args=['telegram-pendientes'])
    assert resultado.exit_code == 0, resultado.output
    assert 'telegram-pendientes: abandonados=0 procesados=0 errores=0 purgados=0' in resultado.output
