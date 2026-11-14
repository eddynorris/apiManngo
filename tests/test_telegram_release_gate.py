import logging

from extensions import db
from models import TelegramUpdate
from resources import telegram_webhook_resource as webhook_module
from telegram.router import TelegramRouter


class _NoExecutor:
    def __init__(self, effects):
        self.effects = effects

    def submit(self, *args, **kwargs):
        self.effects.append('executor')


def test_webhook_siempre_503_sin_dedup_router_executor_ni_escrituras(
    test_client, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    synthetic_url_secret = 'synthetic-url-secret-a08'
    synthetic_header_secret = 'synthetic-header-secret-a08'
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', synthetic_url_secret)
    monkeypatch.setenv('TELEGRAM_BOT_SECRET_TOKEN', synthetic_header_secret)
    monkeypatch.setenv('TELEGRAM_ENABLED', 'true')
    monkeypatch.setitem(test_client.application.config, 'TELEGRAM_ENABLED', True)

    effects = []
    monkeypatch.setattr(
        TelegramRouter,
        'handle_message',
        lambda *args, **kwargs: effects.append('router_message'),
    )
    monkeypatch.setattr(
        TelegramRouter,
        'handle_callback_query',
        lambda *args, **kwargs: effects.append('router_callback'),
    )
    monkeypatch.setattr(webhook_module, 'executor', _NoExecutor(effects))
    monkeypatch.setattr(
        webhook_module,
        '_process_update_async',
        lambda *args, **kwargs: effects.append('legacy_async'),
    )
    monkeypatch.setattr(
        webhook_module.TelegramWebhookResource,
        '_marcar_update_procesado',
        lambda self, update_id: effects.append('dedup') or True,
    )

    before = db.session.query(TelegramUpdate).count()
    updates = [
        {
            'update_id': 900001,
            'message': {
                'chat': {'id': 555, 'type': 'private'},
                'from': {'id': 555},
                'text': '/start',
            },
        },
        {
            'update_id': 900002,
            'callback_query': {
                'id': 'callback-3',
                'from': {'id': 555},
                'data': 'confirm:venta',
                'message': {
                    'message_id': 9,
                    'chat': {'id': 555, 'type': 'private'},
                },
            },
        },
        {
            'update_id': 900003,
            'message': {
                'chat': {'id': -100123, 'type': 'supergroup'},
                'from': {'id': 777},
                'text': '/start',
            },
        },
        {'update_id': 900004},
        {'update_id': 900001, 'message': {'text': '/start'}},
    ]

    for update in updates:
        response = test_client.post(
            f'/telegram/webhook/{synthetic_url_secret}',
            json=update,
            headers={'X-Telegram-Bot-Api-Secret-Token': synthetic_header_secret},
        )
        assert response.status_code == 503
        body = response.get_json()
        assert body['error'] == 'IntegrationUnavailable'
        assert synthetic_url_secret not in response.get_data(as_text=True)
        assert synthetic_header_secret not in response.get_data(as_text=True)

    response = test_client.post(
        '/telegram/webhook/token-sintetico-invalido',
        json={'update_id': 900005},
    )
    assert response.status_code == 503
    assert response.get_json()['error'] == 'IntegrationUnavailable'

    assert effects == []
    assert db.session.query(TelegramUpdate).count() == before
    assert (
        db.session.query(TelegramUpdate)
        .filter(TelegramUpdate.update_id.in_([900001, 900002, 900003, 900004, 900005]))
        .count()
        == 0
    )
    assert synthetic_url_secret not in caplog.text
    assert synthetic_header_secret not in caplog.text
