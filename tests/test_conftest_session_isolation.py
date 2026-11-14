from extensions import db

_captured_session = None


def test_1_capture_module_context_session(test_client):
    global _captured_session
    _captured_session = db.session()
    assert _captured_session is db.session()


def test_2_previous_module_context_session_was_removed(test_client):
    assert _captured_session is not None
    assert db.session() is not _captured_session
