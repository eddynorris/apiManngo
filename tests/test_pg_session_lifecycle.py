import os
import sys

import pytest
from sqlalchemy.engine import make_url

import scripts.offline_test_env as offline
from scripts.offline_test_env import ALLOW_LOCAL_POSTGRES_ENV

VALID_LOCAL_PG = 'postgresql://manngo_test@127.0.0.1:5432/manngo_test_lifecycle'
OTHER_LOCAL_PG = 'postgresql://manngo_test@localhost:5432/manngo_test_other'
REMOTE_PG = (
    'postgresql://manngo_test:sentinel@db.example.invalid/manngo_test_lifecycle'
)
PG_GUARD_ENV = {ALLOW_LOCAL_POSTGRES_ENV: '1'}

_probe_state = {}


def _harness():
    module = sys.modules.get('tests.conftest')
    if module is None:
        module = sys.modules.get('conftest')
    assert module is not None, 'El conftest de tests debe estar cargado'
    return module


harness = _harness()


def test_collection_clobber_keeps_the_validated_dsn():
    assert harness.environment_status.database_kind in {
        'sqlite-memory',
        'postgresql-loopback-disposable',
    }
    assert os.environ['DATABASE_URL'] == harness.VALIDATED_DATABASE_URL
    if harness.PG_OPT_IN:
        assert make_url(os.environ['DATABASE_URL']).get_backend_name() in {
            'postgres',
            'postgresql',
        }


def test_restore_replaces_sqlite_clobber_in_pg_mode():
    env = {'DATABASE_URL': 'sqlite:///:memory:'}

    changed = harness._restore_validated_pg_dsn(
        env,
        enabled=True,
        snapshot=VALID_LOCAL_PG,
        configured_url=VALID_LOCAL_PG,
        engine_url=VALID_LOCAL_PG,
        guard_env=PG_GUARD_ENV,
    )

    assert changed is True
    assert env['DATABASE_URL'] == VALID_LOCAL_PG


def test_restore_never_uses_a_remote_snapshot():
    env = {'DATABASE_URL': 'sqlite:///:memory:'}

    with pytest.raises(RuntimeError) as error:
        harness._restore_validated_pg_dsn(
            env,
            enabled=True,
            snapshot=REMOTE_PG,
            configured_url=REMOTE_PG,
            engine_url=REMOTE_PG,
            guard_env=PG_GUARD_ENV,
        )

    assert env['DATABASE_URL'] == 'sqlite:///:memory:'
    assert 'sentinel' not in str(error.value)


def test_restore_refuses_engine_that_does_not_match_snapshot():
    env = {'DATABASE_URL': 'sqlite:///:memory:'}

    with pytest.raises(RuntimeError):
        harness._restore_validated_pg_dsn(
            env,
            enabled=True,
            snapshot=VALID_LOCAL_PG,
            configured_url=VALID_LOCAL_PG,
            engine_url=OTHER_LOCAL_PG,
            guard_env=PG_GUARD_ENV,
        )

    assert env['DATABASE_URL'] == 'sqlite:///:memory:'


def test_restore_refuses_config_that_does_not_match_snapshot():
    env = {'DATABASE_URL': 'sqlite:///:memory:'}

    with pytest.raises(RuntimeError):
        harness._restore_validated_pg_dsn(
            env,
            enabled=True,
            snapshot=VALID_LOCAL_PG,
            configured_url=OTHER_LOCAL_PG,
            engine_url=VALID_LOCAL_PG,
            guard_env=PG_GUARD_ENV,
        )

    assert env['DATABASE_URL'] == 'sqlite:///:memory:'


def test_restore_is_disabled_for_sqlite_mode():
    env = {'DATABASE_URL': 'sqlite:///:memory:'}

    changed = harness._restore_validated_pg_dsn(
        env,
        enabled=False,
        snapshot=VALID_LOCAL_PG,
        configured_url=VALID_LOCAL_PG,
        engine_url=VALID_LOCAL_PG,
        guard_env=PG_GUARD_ENV,
    )

    assert changed is False
    assert env['DATABASE_URL'] == 'sqlite:///:memory:'


@pytest.fixture
def ddl_teardown_probe():
    with harness.app.app_context():
        db = harness.db
        db.session.execute(
            db.text(
                'CREATE TABLE IF NOT EXISTS manngo_test_cleanup_probe '
                '(id integer)'
            )
        )
        db.session.commit()
        _probe_state['session'] = db.session()
        yield
        assert db.session() is not _probe_state['session'], (
            'pytest_runtest_teardown debe eliminar la sesión activa antes '
            'de que el teardown del fixture ejecute DDL'
        )
        remaining = db.session.execute(
            db.text('SELECT count(*) FROM manngo_test_cleanup_probe')
        ).scalar()
        assert remaining == 0
        db.session.execute(
            db.text('DROP TABLE IF EXISTS manngo_test_cleanup_probe')
        )
        db.session.commit()
        db.session.remove()


def test_session_is_rolled_back_before_ddl_teardown(ddl_teardown_probe):
    db = harness.db
    assert db.session() is _probe_state['session']
    db.session.execute(
        db.text('INSERT INTO manngo_test_cleanup_probe (id) VALUES (1)')
    )


PG_ENV = {
    'DATABASE_URL': VALID_LOCAL_PG,
    'MANNGO_LOCAL_POSTGRES_URL': VALID_LOCAL_PG,
    ALLOW_LOCAL_POSTGRES_ENV: '1',
}


def _use_allowed_port(monkeypatch, port=5432):
    monkeypatch.setitem(offline._guard_state, 'allow_loopback', True)
    monkeypatch.setitem(
        offline._guard_state, 'allowed_loopback_ports', frozenset({port})
    )


def test_validated_postgres_ports_derives_coincident_loopback_endpoint():
    assert offline._validated_postgres_ports(PG_ENV) == frozenset({5432})


def test_validated_postgres_ports_rejects_non_coincident_dsns():
    env = dict(PG_ENV, MANNGO_LOCAL_POSTGRES_URL=OTHER_LOCAL_PG)

    with pytest.raises(offline.UnsafeTestDatabaseError):
        offline._validated_postgres_ports(env)


def test_validated_postgres_ports_rejects_missing_local_dsn():
    env = {
        'DATABASE_URL': VALID_LOCAL_PG,
        ALLOW_LOCAL_POSTGRES_ENV: '1',
    }

    with pytest.raises(offline.UnsafeTestDatabaseError):
        offline._validated_postgres_ports(env)


def test_validated_postgres_ports_rejects_remote_dsns():
    env = dict(
        PG_ENV,
        DATABASE_URL=REMOTE_PG,
        MANNGO_LOCAL_POSTGRES_URL=REMOTE_PG,
    )

    with pytest.raises(offline.UnsafeTestDatabaseError):
        offline._validated_postgres_ports(env)


@pytest.mark.parametrize(
    'address',
    [
        ('127.0.0.1', 5432),
        ('localhost', 5432),
        ('::1', 5432),
    ],
)
def test_guarded_connect_allows_only_validated_loopback_port(
    monkeypatch, address
):
    calls = []

    def fake_original(original_self, original_address, *args, **kwargs):
        calls.append((original_self, original_address))
        return 'original-connect'

    monkeypatch.setattr(offline, '_original_socket_connect', fake_original)
    _use_allowed_port(monkeypatch)
    sentinel = object()

    assert (
        offline._guarded_socket_connect(sentinel, address) == 'original-connect'
    )
    assert calls == [(sentinel, address)]


def test_guarded_connect_ex_allows_only_validated_loopback_port(monkeypatch):
    calls = []

    def fake_original(original_self, original_address, *args, **kwargs):
        calls.append((original_self, original_address))
        return 0

    monkeypatch.setattr(
        offline, '_original_socket_connect_ex', fake_original
    )
    _use_allowed_port(monkeypatch)
    sentinel = object()

    assert offline._guarded_socket_connect_ex(
        sentinel, ('127.0.0.1', 5432)
    ) == 0
    assert calls == [(sentinel, ('127.0.0.1', 5432))]


def test_guarded_create_connection_allows_only_validated_loopback_port(
    monkeypatch,
):
    calls = []

    def fake_original(original_address, *args, **kwargs):
        calls.append(original_address)
        return 'original-create-connection'

    monkeypatch.setattr(
        offline, '_original_create_connection', fake_original
    )
    _use_allowed_port(monkeypatch)

    assert (
        offline._guarded_create_connection(('127.0.0.1', 5432))
        == 'original-create-connection'
    )
    assert calls == [('127.0.0.1', 5432)]


@pytest.mark.parametrize(
    'address',
    [
        ('127.0.0.1', 9),
        ('localhost', 9),
        ('203.0.113.10', 5432),
        '/var/run/postgresql/.s.PGSQL.5432',
    ],
)
def test_guarded_connect_blocks_other_ports_remotes_and_unix(
    monkeypatch, address
):
    calls = []

    def fake_original(*args, **kwargs):
        calls.append((args, kwargs))
        return 'must-not-be-called'

    monkeypatch.setattr(offline, '_original_socket_connect', fake_original)
    _use_allowed_port(monkeypatch)

    with pytest.raises(offline.OfflineNetworkBlocked):
        offline._guarded_socket_connect(object(), address)

    assert calls == []


@pytest.mark.parametrize(
    'address',
    [
        ('127.0.0.1', 5432),
        ('127.0.0.1', 9),
        ('203.0.113.10', 5432),
    ],
)
def test_guarded_sendto_is_always_blocked(monkeypatch, address):
    calls = []

    def fake_original(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(offline, '_original_socket_sendto', fake_original)
    _use_allowed_port(monkeypatch)

    with pytest.raises(offline.OfflineNetworkBlocked):
        offline._guarded_socket_sendto(object(), b'probe', address)

    assert calls == []


def test_install_does_not_expand_after_initial_installation(monkeypatch):
    monkeypatch.setitem(offline._guard_state, 'installed', True)
    monkeypatch.setitem(offline._guard_state, 'allow_loopback', False)
    monkeypatch.setitem(
        offline._guard_state, 'allowed_loopback_ports', frozenset()
    )

    offline.install_offline_guards(True, env=PG_ENV)

    assert offline._guard_state['allow_loopback'] is False
    assert offline._guard_state['allowed_loopback_ports'] == frozenset()


def test_install_true_rejects_unsafe_dsn(monkeypatch):
    monkeypatch.setitem(offline._guard_state, 'installed', True)
    env = dict(
        PG_ENV,
        DATABASE_URL=REMOTE_PG,
        MANNGO_LOCAL_POSTGRES_URL=REMOTE_PG,
    )

    with pytest.raises(offline.UnsafeTestDatabaseError):
        offline.install_offline_guards(True, env=env)


def test_install_none_preserves_existing_policy(monkeypatch):
    monkeypatch.setitem(offline._guard_state, 'installed', True)
    _use_allowed_port(monkeypatch)

    offline.install_offline_guards()

    assert offline._guard_state['allow_loopback'] is True
    assert offline._guard_state['allowed_loopback_ports'] == frozenset({5432})


def test_install_false_closes_existing_policy(monkeypatch):
    monkeypatch.setitem(offline._guard_state, 'installed', True)
    _use_allowed_port(monkeypatch)

    offline.install_offline_guards(False)

    assert offline._guard_state['allow_loopback'] is False
    assert offline._guard_state['allowed_loopback_ports'] == frozenset()
