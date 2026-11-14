"""Logger de gunicorn que no escribe el token secreto de la ruta del webhook de Telegram.

El token va en la URL (/telegram/webhook/<TELEGRAM_WEBHOOK_SECRET>) y el log de acceso
por defecto incluye la línea de la petición. Uso: --logger-class utils.gunicorn_logging.LoggerSinSecretos
"""
import re

from gunicorn.glogging import Logger

_RUTA_WEBHOOK = re.compile(r'(/telegram/webhook/)[^/?\s"]+')


def redactar_ruta(texto):
    if not texto:
        return texto
    return _RUTA_WEBHOOK.sub(r'\1[oculto]', texto)


class LoggerSinSecretos(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for clave in ('r', 'U', '{raw_uri}e', '{path_info}e'):
            if isinstance(atoms.get(clave), str):
                atoms[clave] = redactar_ruta(atoms[clave])
        return atoms
