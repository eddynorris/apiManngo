"""Offline regressions for the reserved local Redis test authorization.

These tests never open Redis, PostgreSQL or any other socket. Socket calls
are replaced by doubles and the guard state is isolated with monkeypatch.
"""

import socket
import ssl

import pytest

import scripts.offline_test_env as offline
from scripts.offline_test_env import (
    ALLOW_LOCAL_POSTGRES_ENV,
    ALLOW_LOCAL_REDIS_ENV,
    DEFAULT_LOCAL_REDIS_URL,
    LOCAL_POSTGRES_URL_ENV,
    LOCAL_REDIS_URL_ENV,
    UnsafeTestDatabaseError,
    _validated_loopback_ports,
    _validated_redis_ports,
    clear_integration_environment,
    install_offline_guards,
    prepare_test_environment,
    validate_testing_redis_url,
)

PG_URL = "postgresql://manngo_test@127.0.0.1:5432/manngo_test_redis_guard"
PG_ENV = {
    "DATABASE_URL": PG_URL,
    LOCAL_POSTGRES_URL_ENV: PG_URL,
    ALLOW_LOCAL_POSTGRES_ENV: "1",
}
REDIS_URL = "redis://127.0.0.1:6379/0"
REDIS_ENV = dict(
    PG_ENV,
    **{
        ALLOW_LOCAL_REDIS_ENV: "1",
        LOCAL_REDIS_URL_ENV: REDIS_URL,
    },
)


def _fake_original(*args, **kwargs):
    return "original-called"


def _use_ports(monkeypatch, ports):
    monkeypatch.setitem(offline._guard_state, "allow_loopback", True)
    monkeypatch.setitem(
        offline._guard_state, "allowed_loopback_ports", frozenset(ports)
    )


@pytest.mark.parametrize(
    "url",
    [
        "redis://127.0.0.1:6379/0",
        "redis://localhost:6380/1",
        "redis://[::1]:6379/5",
        "redis://127.0.0.1/0",
    ],
)
def test_validate_redis_accepts_only_literal_loopback_with_numeric_db(url):
    assert (
        validate_testing_redis_url(url, allow_local_redis=True)
        == offline.REDIS_LOOPBACK_KIND
    )


def test_validate_redis_requires_explicit_authorization_not_rediss_url():
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_redis_url(REDIS_URL, env={})
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_redis_url(
            REDIS_URL, env={ALLOW_LOCAL_POSTGRES_ENV: "1"}
        )

    assert (
        validate_testing_redis_url(
            REDIS_URL, env={ALLOW_LOCAL_REDIS_ENV: "1"}
        )
        == offline.REDIS_LOOPBACK_KIND
    )


@pytest.mark.parametrize(
    "url",
    [
        "redis://user:supersecret@evil.example:6379/0",
        "redis://127.0.0.1.evil:6379/0",
        "redis://localhost.evil:6379/0",
        "redis://127.0.0.1:6379/0?host=evil.example",
        "redis://127.0.0.1:6379/0?hostaddr=203.0.113.10",
        "redis://127.0.0.1:6379/0?unix_socket_path=/tmp/redis.sock",
        "redis+unix:///tmp/redis.sock/0",
        "redis:///tmp/redis.sock/0",
        "redis://127.0.0.1:0/0",
        "redis://127.0.0.1:65536/0",
        "redis://127.0.0.1:6379/not-a-number",
        "redis://127.0.0.1:6379",
        "rediss://127.0.0.1:6379/0",
    ],
)
def test_validate_redis_rejects_unsafe_uris_without_leaking_credentials(url):
    with pytest.raises(UnsafeTestDatabaseError) as error:
        validate_testing_redis_url(url, allow_local_redis=True)

    message = str(error.value)
    assert "supersecret" not in message
    assert "evil.example" not in message
    assert "203.0.113.10" not in message


def test_redis_ports_require_pg_authorization_and_the_reserved_uri():
    with pytest.raises(UnsafeTestDatabaseError):
        _validated_redis_ports(PG_ENV)
    with pytest.raises(UnsafeTestDatabaseError):
        _validated_redis_ports(dict(PG_ENV, REDIS_URL=REDIS_URL))

    assert _validated_redis_ports(REDIS_ENV) == frozenset({6379})
    assert _validated_loopback_ports(REDIS_ENV) == frozenset({5432, 6379})


def test_conventional_redis_url_does_not_add_a_port():
    env = dict(PG_ENV, REDIS_URL=REDIS_URL, REDIS_HOST="127.0.0.1")

    assert _validated_loopback_ports(env) == frozenset({5432})


def test_prepare_preserves_redis_opt_in_when_called_without_flags():
    env = dict(REDIS_ENV)

    status = prepare_test_environment(env)

    assert env[ALLOW_LOCAL_REDIS_ENV] == "1"
    assert env[LOCAL_REDIS_URL_ENV] == REDIS_URL
    assert status.allow_loopback is True
    assert status.allow_local_redis is True


def test_prepare_uses_reserved_redis_uri_when_flag_is_preserved():
    env = dict(PG_ENV, **{ALLOW_LOCAL_REDIS_ENV: "1"})

    status = prepare_test_environment(env)

    assert env[LOCAL_REDIS_URL_ENV] == DEFAULT_LOCAL_REDIS_URL
    assert status.allow_local_redis is True


def test_prepare_explicit_false_closes_preserved_redis_permission():
    env = dict(REDIS_ENV)

    status = prepare_test_environment(env, allow_local_redis=False)

    assert ALLOW_LOCAL_REDIS_ENV not in env
    assert status.allow_local_redis is False
    with pytest.raises(UnsafeTestDatabaseError):
        validate_testing_redis_url(REDIS_URL, env=env)


def test_prepare_explicit_true_requires_a_local_postgres_and_valid_redis_uri():
    with pytest.raises(UnsafeTestDatabaseError):
        prepare_test_environment(
            {"DATABASE_URL": "sqlite:///:memory:"},
            allow_local_redis=True,
        )

    bad_redis_env = dict(
        PG_ENV, **{LOCAL_REDIS_URL_ENV: "redis://evil.example:6379/0"}
    )
    with pytest.raises(UnsafeTestDatabaseError):
        prepare_test_environment(bad_redis_env, allow_local_redis=True)


def test_clear_environment_preserves_reserved_values_and_removes_real_redis():
    env = {
        ALLOW_LOCAL_REDIS_ENV: "1",
        LOCAL_REDIS_URL_ENV: DEFAULT_LOCAL_REDIS_URL,
        "REDIS_URL": "redis://remote.invalid:6379/0",
        "REDIS_HOST": "remote.invalid",
        "REDIS_PORT": "6379",
        "LIMITER_STORAGE_URI": "redis://remote.invalid:6379/0",
        "KEEP_ME": "visible",
    }

    removed = clear_integration_environment(env)

    assert env[ALLOW_LOCAL_REDIS_ENV] == "1"
    assert env[LOCAL_REDIS_URL_ENV] == DEFAULT_LOCAL_REDIS_URL
    assert env["KEEP_ME"] == "visible"
    for key in ("REDIS_URL", "REDIS_HOST", "REDIS_PORT", "LIMITER_STORAGE_URI"):
        assert key not in env
        assert key in removed


def _prepare_initial_install(monkeypatch):
    monkeypatch.setitem(offline._guard_state, "installed", False)
    monkeypatch.setitem(offline._guard_state, "allow_loopback", False)
    monkeypatch.setitem(offline._guard_state, "allow_local_redis", False)
    monkeypatch.setitem(
        offline._guard_state, "allowed_loopback_ports", frozenset()
    )
    monkeypatch.setitem(offline._guard_state, "allowed_postgres_ports", frozenset())
    monkeypatch.setitem(offline._guard_state, "allowed_redis_ports", frozenset())

    for name in (
        "_original_socket_connect",
        "_original_socket_connect_ex",
        "_original_socket_sendto",
        "_original_create_connection",
        "_original_ssl_connect",
    ):
        monkeypatch.setattr(offline, name, getattr(offline, name))

    monkeypatch.setattr(socket.socket, "connect", _fake_original)
    monkeypatch.setattr(socket.socket, "connect_ex", _fake_original)
    monkeypatch.setattr(socket.socket, "sendto", _fake_original)
    monkeypatch.setattr(socket, "create_connection", _fake_original)
    monkeypatch.setattr(ssl.SSLSocket, "connect", _fake_original)


def test_initial_install_freezes_postgres_and_redis_ports(monkeypatch):
    _prepare_initial_install(monkeypatch)

    install_offline_guards(True, allow_local_redis=True, env=REDIS_ENV)

    assert offline._guard_state["allow_loopback"] is True
    assert offline._guard_state["allow_local_redis"] is True
    assert offline._guard_state["allowed_loopback_ports"] == frozenset(
        {5432, 6379}
    )


def test_install_does_not_widen_extra_redis_port_after_installation(monkeypatch):
    monkeypatch.setitem(offline._guard_state, "installed", True)
    monkeypatch.setitem(offline._guard_state, "allow_loopback", True)
    monkeypatch.setitem(offline._guard_state, "allow_local_redis", True)
    monkeypatch.setitem(
        offline._guard_state,
        "allowed_loopback_ports",
        frozenset({5432, 6379}),
    )
    monkeypatch.setitem(
        offline._guard_state, "allowed_postgres_ports", frozenset({5432})
    )
    monkeypatch.setitem(
        offline._guard_state, "allowed_redis_ports", frozenset({6379})
    )

    extra_port_env = dict(
        REDIS_ENV,
        **{LOCAL_REDIS_URL_ENV: "redis://127.0.0.1:6380/0"},
    )
    install_offline_guards(True, allow_local_redis=True, env=extra_port_env)

    assert offline._guard_state["allowed_loopback_ports"] == frozenset(
        {5432, 6379}
    )


def test_guarded_connect_allows_only_ports_frozen_in_state(monkeypatch):
    calls = []

    def fake_connect(original_self, address, *args, **kwargs):
        calls.append((original_self, address))
        return "original-connect"

    monkeypatch.setattr(offline, "_original_socket_connect", fake_connect)
    _use_ports(monkeypatch, {5432, 6379})
    sentinel = object()

    assert (
        offline._guarded_socket_connect(sentinel, ("127.0.0.1", 6379))
        == "original-connect"
    )
    assert (
        offline._guarded_socket_connect(sentinel, ("127.0.0.1", 5432))
        == "original-connect"
    )
    assert calls == [
        (sentinel, ("127.0.0.1", 6379)),
        (sentinel, ("127.0.0.1", 5432)),
    ]


@pytest.mark.parametrize(
    "address",
    [
        ("127.0.0.1", 9),
        ("localhost", 9),
        ("127.0.0.1", 6380),
        ("203.0.113.10", 6379),
        "/tmp/redis.sock",
    ],
)
def test_guarded_connect_still_blocks_other_endpoints(monkeypatch, address):
    calls = []

    def fake_connect(*args, **kwargs):
        calls.append((args, kwargs))
        return "must-not-be-called"

    monkeypatch.setattr(offline, "_original_socket_connect", fake_connect)
    _use_ports(monkeypatch, {5432, 6379})

    with pytest.raises(offline.OfflineNetworkBlocked):
        offline._guarded_socket_connect(object(), address)

    assert calls == []


def test_guarded_sendto_stays_blocked_with_redis_permission(monkeypatch):
    calls = []

    def fake_sendto(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(offline, "_original_socket_sendto", fake_sendto)
    _use_ports(monkeypatch, {5432, 6379})

    with pytest.raises(offline.OfflineNetworkBlocked):
        offline._guarded_socket_sendto(object(), b"probe", ("127.0.0.1", 6379))

    assert calls == []


def test_runner_refuses_redis_flag_without_postgres():
    from scripts import run_tests_offline

    assert (
        run_tests_offline.main(["--allow-local-redis", "--check-only"]) == 2
    )
