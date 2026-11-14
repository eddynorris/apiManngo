import pytest

import telegram.router as router_module
from extensions import db
from models import Users
from telegram.router import TelegramRouter


class _ForbiddenActorLookup(BaseException):
    """Falla si el router intenta resolver un actor antes de validar identidad."""


class _NoQuery:
    def __getattr__(self, name):
        raise _ForbiddenActorLookup(name)


class _ForbiddenUsers:
    def __init__(self):
        self.query = _NoQuery()


class _FakeQuery:
    def __init__(self, user):
        self.user = user
        self.filter_kwargs = None

    def filter_by(self, **kwargs):
        self.filter_kwargs = kwargs
        return self

    def first(self):
        return self.user


class _FakeUsers:
    def __init__(self, user):
        self.query = _FakeQuery(user)


class _FakeUser:
    id = 7  # el router consulta StateMachine.get_context(user), que usa user.id
    username = 'a07_operador'
    telegram_context = None
    telegram_history = []


class _FakeTelegramService:
    def __init__(self):
        self.sent = []
        self.edited = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.edited.append((chat_id, message_id, text, kwargs))


@pytest.mark.parametrize(
    'message',
    [
        {
            'chat': {'id': -100123, 'type': 'supergroup'},
            'from': {'id': 777},
            'text': '/start',
        },
        {
            'chat': {'id': -100124, 'type': 'group'},
            'from': {'id': -100124},
            'text': '/start',
        },
        {
            'chat': {'id': -5, 'type': 'private'},
            'from': {'id': -5},
            'text': '/start',
        },
        {
            'chat': {'id': 555, 'type': 'private'},
            'text': '/start',
        },
        {
            'chat': {'id': 555, 'type': 'private'},
            'from': {'id': 556},
            'text': '/start',
        },
        {
            'chat': {'id': 555},
            'from': {'id': 555},
            'text': '/start',
        },
    ],
)
def test_mensaje_sin_identidad_privada_no_resuelve_actor_ni_actua(
    message, monkeypatch
):
    fake_telegram = _FakeTelegramService()
    monkeypatch.setattr(router_module, 'Users', _ForbiddenUsers())
    monkeypatch.setattr(router_module, 'telegram_service', fake_telegram)

    TelegramRouter.handle_message(message)

    assert fake_telegram.sent == []
    assert fake_telegram.edited == []


@pytest.mark.parametrize(
    'callback_query',
    [
        {
            'from': {'id': 777},
            'data': 'cancel',
            'message': {
                'message_id': 9,
                'chat': {'id': -100123, 'type': 'supergroup'},
                'from': {'id': 999},
            },
        },
        {
            'from': {'id': -5},
            'data': 'cancel',
            'message': {
                'message_id': 9,
                'chat': {'id': -5, 'type': 'private'},
            },
        },
        {
            'data': 'cancel',
            'message': {
                'message_id': 9,
                'chat': {'id': 555, 'type': 'private'},
            },
        },
        {
            'from': {'id': 556},
            'data': 'cancel',
            'message': {
                'message_id': 9,
                'chat': {'id': 555, 'type': 'private'},
            },
        },
        {
            'from': {'id': 555},
            'data': 'confirm:venta',
        },
    ],
)
def test_callback_sin_identidad_privada_no_resuelve_actor_ni_escribe(
    callback_query, monkeypatch
):
    fake_telegram = _FakeTelegramService()
    cleared = []
    monkeypatch.setattr(router_module, 'Users', _ForbiddenUsers())
    monkeypatch.setattr(router_module, 'telegram_service', fake_telegram)
    monkeypatch.setattr(
        router_module, 'clear_user_context', lambda user: cleared.append(user)
    )

    TelegramRouter.handle_callback_query(callback_query)

    assert fake_telegram.sent == []
    assert fake_telegram.edited == []
    assert cleared == []


def test_mensaje_privado_resuelve_actor_por_remitente_positivo(monkeypatch):
    fake_telegram = _FakeTelegramService()
    fake_users = _FakeUsers(_FakeUser())
    monkeypatch.setattr(router_module, 'Users', fake_users)
    monkeypatch.setattr(router_module, 'telegram_service', fake_telegram)

    TelegramRouter.handle_message(
        {
            'chat': {'id': 555, 'type': 'private'},
            'from': {'id': 555},
            'text': '/start',
        }
    )

    assert fake_users.query.filter_kwargs == {'telegram_chat_id': 555}
    assert len(fake_telegram.sent) == 1
    assert fake_telegram.sent[0][0] == 555
    assert 'Bienvenido' in fake_telegram.sent[0][1]


def test_callback_privado_usa_callback_from_y_no_el_autor_del_mensaje(monkeypatch):
    fake_telegram = _FakeTelegramService()
    user = _FakeUser()
    fake_users = _FakeUsers(user)
    cleared = []
    monkeypatch.setattr(router_module, 'Users', fake_users)
    monkeypatch.setattr(router_module, 'telegram_service', fake_telegram)
    monkeypatch.setattr(
        router_module, 'clear_user_context', lambda target: cleared.append(target)
    )

    TelegramRouter.handle_callback_query(
        {
            'id': 'callback-1',
            'from': {'id': 555},
            'data': 'cancel',
            'message': {
                'message_id': 9,
                'chat': {'id': 555, 'type': 'private'},
                'from': {'id': 999},
            },
        }
    )

    assert fake_users.query.filter_kwargs == {'telegram_chat_id': 555}
    assert cleared == [user]
    assert len(fake_telegram.edited) == 1
    assert fake_telegram.edited[0][0] == 555


def test_callback_de_grupo_no_modifica_usuario_vinculado(test_client, monkeypatch):
    user = Users(
        username='a07_callback_grupo',
        password='x',
        rol='admin',
        telegram_chat_id=-100123,
        telegram_context={'keep': True},
    )
    db.session.add(user)
    db.session.commit()
    user_id = user.id

    fake_telegram = _FakeTelegramService()
    monkeypatch.setattr(router_module, 'telegram_service', fake_telegram)

    TelegramRouter.handle_callback_query(
        {
            'id': 'callback-2',
            'from': {'id': 777},
            'data': 'cancel',
            'message': {
                'message_id': 9,
                'chat': {'id': -100123, 'type': 'supergroup'},
                'from': {'id': 999},
            },
        }
    )

    db.session.expire_all()
    stored = db.session.get(Users, user_id)
    assert stored.telegram_context == {'keep': True}
    assert fake_telegram.sent == []
    assert fake_telegram.edited == []

    db.session.delete(stored)
    db.session.commit()


def test_mensaje_de_grupo_no_actua_sobre_usuario_vinculado(test_client, monkeypatch):
    user = Users(
        username='a07_mensaje_grupo',
        password='x',
        rol='admin',
        telegram_chat_id=-100456,
    )
    db.session.add(user)
    db.session.commit()
    user_id = user.id

    fake_telegram = _FakeTelegramService()
    monkeypatch.setattr(router_module, 'telegram_service', fake_telegram)

    TelegramRouter.handle_message(
        {
            'chat': {'id': -100456, 'type': 'supergroup'},
            'from': {'id': 777},
            'text': '/start',
        }
    )

    assert fake_telegram.sent == []
    assert fake_telegram.edited == []

    db.session.delete(db.session.get(Users, user_id))
    db.session.commit()
