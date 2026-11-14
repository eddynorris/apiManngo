"""Contadores de Flask-Limiter compartidos entre dos apps Flask con Redis local.

Opt-in: solo se ejecuta cuando el padre lanza el runner con
``--allow-local-redis`` y ``MANNGO_ALLOW_LOCAL_REDIS_TESTS=1``. Sin ese flag se
omite; con el flag activo un fallo de Redis hace fallar la prueba, no se
omite silenciosamente.
"""
import os
import uuid

import pytest
import redis
from flask import Flask
from flask_limiter import Limiter


MANNGO_ALLOW_LOCAL_REDIS_TESTS = "MANNGO_ALLOW_LOCAL_REDIS_TESTS"
MANNGO_LOCAL_REDIS_URL = "MANNGO_LOCAL_REDIS_URL"

pytestmark = pytest.mark.skipif(
    os.environ.get(MANNGO_ALLOW_LOCAL_REDIS_TESTS) != "1",
    reason="Integración Redis local no autorizada (falta MANNGO_ALLOW_LOCAL_REDIS_TESTS=1).",
)


def _url_redis():
    url = os.environ.get(MANNGO_LOCAL_REDIS_URL)
    if not url:
        pytest.fail(
            "MANNGO_ALLOW_LOCAL_REDIS_TESTS=1 requiere MANNGO_LOCAL_REDIS_URL "
            "(por ejemplo redis://127.0.0.1:6379/0)."
        )
    return url


def _borrar_solo_claves_propias(cliente, prefijo):
    """Elimina únicamente las claves de este test; nunca hace flush global."""
    patron = f"LIMITS:{prefijo}*"
    lote = []
    for clave in cliente.scan_iter(match=patron, count=100):
        lote.append(clave)
        if len(lote) >= 100:
            cliente.delete(*lote)
            lote.clear()
    if lote:
        cliente.delete(*lote)


def _crear_app(redis_url, prefijo):
    app = Flask(f"limiter-compartido-{uuid.uuid4().hex}")
    app.config.update(
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI=redis_url,
        RATELIMIT_STORAGE_OPTIONS={"socket_connect_timeout": 2, "socket_timeout": 2},
        RATELIMIT_STRATEGY="fixed-window",
        RATELIMIT_KEY_PREFIX=prefijo,
        RATELIMIT_REQUEST_IDENTIFIER=lambda: "shared-probe",
        RATELIMIT_SWALLOW_ERRORS=False,
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=False,
        RATELIMIT_IN_MEMORY_FALLBACK=None,
        PROPAGATE_EXCEPTIONS=False,
    )
    limiter = Limiter(key_func=lambda: "shared-client", app=app, default_limits=[])

    @app.route("/probe")
    @limiter.limit("2 per minute")
    def probe():
        return "ok"

    return app, limiter


def test_dos_apps_flask_comparten_el_limite_en_redis():
    redis_url = _url_redis()
    prefijo = f"manngo-l8-{uuid.uuid4().hex}"
    cliente = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        _borrar_solo_claves_propias(cliente, prefijo)

        app_uno, limiter_uno = _crear_app(redis_url, prefijo)
        app_dos, limiter_dos = _crear_app(redis_url, prefijo)

        assert limiter_uno.storage.__class__.__name__ == "RedisStorage"
        assert limiter_dos.storage.__class__.__name__ == "RedisStorage"

        cliente_uno = app_uno.test_client()
        cliente_dos = app_dos.test_client()

        assert cliente_uno.get("/probe").status_code == 200
        assert cliente_uno.get("/probe").status_code == 200
        # Tercer hit entre dos instancias: debe ver el contador de la primera.
        assert cliente_dos.get("/probe").status_code == 429
    finally:
        _borrar_solo_claves_propias(cliente, prefijo)
        cliente.close()
