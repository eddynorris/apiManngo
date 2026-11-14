"""Contrato del CMD de producción, validado sin cargar la aplicación con Gunicorn."""

import re
import shlex
from pathlib import Path

import gunicorn
import pytest
from gunicorn.config import Config


def _argumentos_cmd():
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    contenido = re.sub(r"\\\r?\n", " ", dockerfile.read_text(encoding="utf-8"))
    comandos = re.findall(r"^CMD\s+(.+)$", contenido, flags=re.MULTILINE | re.IGNORECASE)
    assert comandos, "El Dockerfile debe declarar el CMD de producción"
    argumentos = shlex.split(comandos[-1], comments=True)
    assert argumentos[:2] == ["exec", "gunicorn"], "CMD debe ejecutar exec gunicorn"
    # Resolver solo las variables del contrato, sin consultar el entorno ni ejecutar shell.
    valores = {"PORT": "8080", "GUNICORN_WORKERS": "3", "GUNICORN_TIMEOUT": "120"}
    return [
        re.sub(r"\$\{(\w+)\}", lambda m: valores.get(m[1], m[0]), argumento)
        for argumento in argumentos[2:]
    ]


def test_cmd_desactiva_control_socket():
    assert "--no-control-socket" in _argumentos_cmd(), (
        "Gunicorn 26 necesita --no-control-socket para appuser sin home escribible"
    )


@pytest.mark.parametrize(
    ("opcion", "valor"),
    [
        ("--access-logfile", "-"),
        ("--logger-class", "utils.gunicorn_logging.LoggerSinSecretos"),
        ("--error-logfile", "-"),
    ],
)
def test_cmd_conserva_logging(opcion, valor):
    argumentos = _argumentos_cmd()
    assert opcion in argumentos, f"CMD debe conservar {opcion} {valor}"
    indice = argumentos.index(opcion)
    assert argumentos[indice + 1 : indice + 2] == [valor]


def test_cmd_opciones_validas_para_gunicorn_26():
    assert gunicorn.__version__.split(".")[0] == "26", (
        "Este contrato debe comprobarse con Gunicorn 26"
    )
    configuracion = Config()
    # parse_args reconoce todas las opciones y rechaza las desconocidas; Config.set
    # también aplica sus validadores. Ninguno importa el módulo WSGI app:app.
    opciones = configuracion.parser().parse_args(_argumentos_cmd())
    assert opciones.args == ["app:app"]
    for nombre, valor in vars(opciones).items():
        if nombre != "args" and valor is not None:
            configuracion.set(nombre, valor)
    assert configuracion.control_socket_disable is True, (
        "El CMD debe desactivar efectivamente el control socket en Gunicorn 26"
    )
