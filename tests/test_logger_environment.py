import logging

from utils.logger_config import setup_logging


def test_setup_logging_does_not_require_removed_flask_env_config(test_client):
    app = test_client.application
    original_handlers = logging.getLogger().handlers[:]
    original_level = logging.getLogger().level
    sentinel = object()
    old_env = app.config.pop('ENV', sentinel)
    old_flask_env = app.config.get('FLASK_ENV', sentinel)

    app.config['FLASK_ENV'] = 'testing'
    try:
        configured = setup_logging(app)
        assert configured is logging.getLogger()
    finally:
        if old_env is sentinel:
            app.config.pop('ENV', None)
        else:
            app.config['ENV'] = old_env
        if old_flask_env is sentinel:
            app.config.pop('FLASK_ENV', None)
        else:
            app.config['FLASK_ENV'] = old_flask_env

        root = logging.getLogger()
        root.handlers[:] = original_handlers
        root.setLevel(original_level)
