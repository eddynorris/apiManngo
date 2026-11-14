import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_

from extensions import db
from models import Pago, StorageCleanupTask
from utils import file_handlers

logger = logging.getLogger(__name__)

_UPLOADED_KEYS = 'storage_uploaded_keys'
_CLEANUP_RETRY_BASE_SECONDS = 60
_CLEANUP_RETRY_MAX_SECONDS = 3600


def track_uploaded_key(storage_key):
    if not storage_key:
        return
    keys = db.session.info.setdefault(_UPLOADED_KEYS, [])
    if storage_key not in keys:
        keys.append(storage_key)


def clear_uploaded_keys():
    db.session.info.pop(_UPLOADED_KEYS, None)


def storage_key_is_referenced(storage_key):
    if not storage_key:
        return False
    return db.session.query(Pago.id).filter(Pago.url_comprobante == storage_key).first() is not None


def enqueue_storage_cleanup(storage_key, reason='replaced'):
    """Registra el borrado en la misma transacción SQL que la referencia nueva."""
    if not storage_key:
        return None

    task = StorageCleanupTask.query.filter_by(storage_key=storage_key).first()
    if task:
        task.reason = reason
        task.next_attempt_at = None
        return task

    task = StorageCleanupTask(storage_key=storage_key, reason=reason)
    db.session.add(task)
    return task


def _next_attempt(attempts):
    delay = min(
        _CLEANUP_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)),
        _CLEANUP_RETRY_MAX_SECONDS,
    )
    return datetime.now(UTC) + timedelta(seconds=delay)


def _record_failed_task(task, error):
    task.attempts = (task.attempts or 0) + 1
    task.last_error = (error or 'delete_failed')[:255]
    task.next_attempt_at = _next_attempt(task.attempts)


def process_pending_storage_cleanups(limit=100, force=False):
    """
    Reintenta borrados ya confirmados en SQL. Un fallo de Storage deja la tarea
    pendiente; nunca se presenta la limpieza como exitosa en ese caso.
    """
    now = datetime.now(UTC)
    query = StorageCleanupTask.query
    if not force:
        query = query.filter(or_(
            StorageCleanupTask.next_attempt_at.is_(None),
            StorageCleanupTask.next_attempt_at <= now,
        ))

    tasks = query.order_by(StorageCleanupTask.id.asc()).limit(limit).all()
    processed = 0
    deleted = 0
    retained = 0

    for task in tasks:
        processed += 1
        if storage_key_is_referenced(task.storage_key):
            task.attempts = (task.attempts or 0) + 1
            task.last_error = 'still_referenced'
            task.next_attempt_at = _next_attempt(task.attempts)
            retained += 1
            continue

        try:
            success = file_handlers.delete_file(task.storage_key)
        except Exception as exc:
            logger.error(f"Fallo inesperado eliminando {task.storage_key}: {exc}")
            success = False

        if success:
            db.session.delete(task)
            deleted += 1
        else:
            _record_failed_task(task, 'delete_failed')
            retained += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error(f"No se pudo persistir el estado de limpieza de Storage: {exc}")
        return {
            'processed': processed,
            'deleted': 0,
            'retained': retained,
            'commit_failed': True,
        }

    return {
        'processed': processed,
        'deleted': deleted,
        'retained': retained,
        'commit_failed': False,
    }


def _enqueue_after_rollback(storage_key, reason):
    try:
        enqueue_storage_cleanup(storage_key, reason)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        logger.error(f"No se pudo encolar la limpieza compensatoria de {storage_key}: {exc}")
        return False


def compensate_uncommitted_uploads():
    """Tras un rollback, borra lo subido que nunca llegó a quedar referenciado."""
    keys = list(db.session.info.get(_UPLOADED_KEYS, []))
    clear_uploaded_keys()

    for key in keys:
        if storage_key_is_referenced(key):
            continue
        try:
            deleted = file_handlers.delete_file(key)
        except Exception as exc:
            logger.error(f"Fallo compensando la subida {key}: {exc}")
            deleted = False
        if not deleted:
            _enqueue_after_rollback(key, 'compensation')


def rollback_and_compensate():
    db.session.rollback()
    compensate_uncommitted_uploads()


def commit_with_storage_finalization():
    """
    Confirma la transacción de negocio y solo después intenta los borrados
    diferidos. Si el commit falla, conserva la referencia anterior y compensa
    la subida nueva.
    """
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        compensate_uncommitted_uploads()
        raise

    clear_uploaded_keys()
    try:
        process_pending_storage_cleanups()
    except Exception as exc:
        db.session.rollback()
        logger.error(f"Las tareas de limpieza quedaron pendientes para reintento: {exc}")
