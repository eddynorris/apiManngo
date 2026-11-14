"""El log de acceso de gunicorn no debe contener el token secreto del webhook de Telegram."""
from pathlib import Path
from types import SimpleNamespace

from gunicorn.config import Config

from utils.gunicorn_logging import LoggerSinSecretos, redactar_ruta

RAIZ = Path(__file__).resolve().parents[1]


def test_redactar_ruta_oculta_el_token_y_conserva_lo_demas():
    linea = 'POST /telegram/webhook/abc123-SECRETO?x=1 HTTP/1.1'
    assert redactar_ruta(linea) == 'POST /telegram/webhook/[oculto]?x=1 HTTP/1.1'
    assert redactar_ruta('GET /ventas?page=2 HTTP/1.1') == 'GET /ventas?page=2 HTTP/1.1'
    assert redactar_ruta('') == '' and redactar_ruta(None) is None


def test_atomos_del_access_log_no_contienen_el_token():
    logger = LoggerSinSecretos(Config())
    req = SimpleNamespace(
        method='POST', path='/telegram/webhook/abc123-SECRETO', query='',
        uri='/telegram/webhook/abc123-SECRETO', version=(1, 1), headers=[],
    )
    resp = SimpleNamespace(status='200 OK', response_length=10, sent=10, headers=[])
    environ = {
        'REQUEST_METHOD': 'POST', 'RAW_URI': '/telegram/webhook/abc123-SECRETO',
        'PATH_INFO': '/telegram/webhook/abc123-SECRETO', 'QUERY_STRING': '',
        'SERVER_PROTOCOL': 'HTTP/1.1', 'REMOTE_ADDR': '127.0.0.1',
    }
    atoms = logger.atoms(resp, req, environ, SimpleNamespace(seconds=0, microseconds=1000, total_seconds=lambda: 0.001))
    serializado = ' '.join(str(v) for v in atoms.values())
    assert 'abc123-SECRETO' not in serializado
    assert '/telegram/webhook/[oculto]' in atoms['r']


def test_dockerfile_usa_el_logger_sin_secretos():
    dockerfile = (RAIZ / 'Dockerfile').read_text(encoding='utf-8')
    assert '--logger-class utils.gunicorn_logging.LoggerSinSecretos' in dockerfile
