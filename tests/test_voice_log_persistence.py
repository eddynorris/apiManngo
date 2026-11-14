from sqlalchemy.orm import Session

from extensions import db
from models import ComandoVozLog, Users
from services.gemini_service import gemini_service


def _headers(client, username='vendedor', password='ventapass'):
    response = client.post('/auth', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return {'Authorization': f"Bearer {response.get_json()['access_token']}"}


def _fake_result(action='none', message='Sin operación'):
    return {
        'action': action,
        'message': message,
        'history_entry': {'user': 'hola', 'model': message},
    }


def test_voice_log_se_confirma_en_sesion_independiente(test_client, monkeypatch):
    db.session.query(ComandoVozLog).delete()
    db.session.commit()

    monkeypatch.setattr(
        gemini_service, 'process_command',
        lambda texto, history=None: _fake_result('none'),
    )

    response = test_client.post(
        '/voice/command',
        headers=_headers(test_client),
        json={'text': 'hola sin operación'},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()['processed_action'] == 'none'

    # Simula que la sesión del request nunca confirma (no hay otra operación
    # comercial que haga commit por ella).
    db.session.rollback()

    usuario = Users.query.filter_by(username='vendedor').one()
    with Session(db.engine) as lectura_independiente:
        logs = lectura_independiente.query(ComandoVozLog).all()
        assert len(logs) == 1
        assert logs[0].usuario_id == usuario.id
        assert logs[0].texto_original == 'hola sin operación'
        assert logs[0].accion_detectada == 'none'
        assert logs[0].exito is False


def test_voice_log_accion_interpretada_tambien_persiste(
    test_client, monkeypatch
):
    db.session.query(ComandoVozLog).delete()
    db.session.commit()

    resultado = _fake_result('interpretar_operacion', 'Solicitó operación')
    resultado['args'] = {}
    monkeypatch.setattr(
        gemini_service, 'process_command',
        lambda texto, history=None: resultado,
    )

    response = test_client.post(
        '/voice/command',
        headers=_headers(test_client),
        json={'text': 'prepara una venta'},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()['processed_action'] == 'confirmar_operacion'

    db.session.rollback()
    with Session(db.engine) as lectura_independiente:
        log = lectura_independiente.query(ComandoVozLog).one()
        assert log.accion_detectada == 'interpretar_operacion'
        assert log.exito is True
