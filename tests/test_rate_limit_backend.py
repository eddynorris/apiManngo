"""Configuración efectiva del backend de Flask-Limiter (A18)."""
import pytest
from flask import Flask
from limits.storage import MemoryStorage, RedisStorage

from app import (
    _resolver_storage_limiter,
    _validar_storage_limiter,
    crear_limiter,
)
from app import (
    app as manngo_app,
)


def test_app_usa_el_nombre_nativo_y_no_fallback_silencioso():
    assert manngo_app.config["RATELIMIT_STORAGE_URI"] == "memory://"
    assert "RATELIMIT_STORAGE_URL" not in manngo_app.config
    assert manngo_app.config["RATELIMIT_SWALLOW_ERRORS"] is False
    assert manngo_app.config["RATELIMIT_IN_MEMORY_FALLBACK_ENABLED"] is False
    assert manngo_app.config["RATELIMIT_IN_MEMORY_FALLBACK"] is None


def test_resolver_prioriza_limiter_storage_uri():
    assert _resolver_storage_limiter({"LIMITER_STORAGE_URI": "redis://a:6379/0"}) == (
        "redis://a:6379/0"
    )
    assert _resolver_storage_limiter({"RATELIMIT_STORAGE_URI": "redis://b:6379/0"}) == (
        "redis://b:6379/0"
    )
    assert _resolver_storage_limiter({}) == "memory://"


def test_memoria_solo_permitida_fuera_de_produccion():
    assert _validar_storage_limiter(
        "memory://", is_production=False, flask_env="testing"
    ) == "memory://"

    with pytest.raises(RuntimeError):
        _validar_storage_limiter("memory://", is_production=True, flask_env="production")
    with pytest.raises(RuntimeError):
        _validar_storage_limiter("", is_production=True, flask_env="production")
    with pytest.raises(RuntimeError):
        _validar_storage_limiter(
            "sqlite:///tmp/limiter.db", is_production=True, flask_env="production"
        )


def test_produccion_acepta_redis():
    assert _validar_storage_limiter(
        "redis://127.0.0.1:6379/0", is_production=True, flask_env="production"
    ) == "redis://127.0.0.1:6379/0"
    assert _validar_storage_limiter(
        "rediss://redis.example:6379/0", is_production=True, flask_env="production"
    ) == "rediss://redis.example:6379/0"


def test_limiter_memoria_expone_storage_efectivo():
    prueba = Flask(__name__)
    prueba.config.update(
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="memory://",
        RATELIMIT_STRATEGY="fixed-window",
        RATELIMIT_SWALLOW_ERRORS=False,
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=False,
        RATELIMIT_IN_MEMORY_FALLBACK=None,
    )
    limiter = crear_limiter(prueba)
    assert isinstance(limiter.storage, MemoryStorage)


def test_limiter_redis_expone_storage_efectivo_sin_conectar():
    prueba = Flask(__name__)
    prueba.config.update(
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="redis://127.0.0.1:6379/0",
        RATELIMIT_STRATEGY="fixed-window",
        RATELIMIT_SWALLOW_ERRORS=False,
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=False,
        RATELIMIT_IN_MEMORY_FALLBACK=None,
    )
    limiter = crear_limiter(prueba)
    assert isinstance(limiter.storage, RedisStorage)


def test_error_de_redis_no_cae_silenciosamente_a_memoria(monkeypatch):
    prueba = Flask(__name__)
    prueba.config.update(
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="redis://127.0.0.1:6379/0",
        RATELIMIT_STRATEGY="fixed-window",
        RATELIMIT_SWALLOW_ERRORS=False,
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=False,
        RATELIMIT_IN_MEMORY_FALLBACK=None,
        PROPAGATE_EXCEPTIONS=False,
    )
    limiter = crear_limiter(prueba)

    def _storage_caido(*args, **kwargs):
        raise RuntimeError("Redis no disponible")

    monkeypatch.setattr(limiter.storage, "incr", _storage_caido)
    monkeypatch.setattr(limiter.storage, "get", _storage_caido)

    @prueba.route("/probe")
    @limiter.limit("1 per minute")
    def probe():
        return "ok"

    response = prueba.test_client().get("/probe")

    assert response.status_code == 500
    assert response.get_data(as_text=True) != "ok"
