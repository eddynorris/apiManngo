import difflib
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func

from extensions import db
from models import Almacen, Cliente, PresentacionProducto, Users, Venta
from services.telegram_service import telegram_service

logger = logging.getLogger(__name__)

def resolver_almacen(user, text):
    if user.rol == 'admin':
        almacenes = Almacen.query.all()
        for al in almacenes:
            if al.nombre.lower() in text.lower():
                return al.id, al.nombre, True
        if user.almacen_id:
            al = db.session.get(Almacen, user.almacen_id)
            return user.almacen_id, al.nombre if al else "Desconocido", False
        return None, None, False
    else:
        if user.almacen_id:
            al = db.session.get(Almacen, user.almacen_id)
            return user.almacen_id, al.nombre if al else "Desconocido", False
        return None, None, False


CONTAINER_TYPES = {
    'bolsa': ['bolsa', 'bolsas'],
    'saco': ['saco', 'sacos'],
    'briqueta': ['briqueta', 'briquetas'],
    'caja': ['caja', 'cajas'],
    'malla': ['malla', 'mallas'],
    'paquete': ['paquete', 'paquetes'],
    'atado': ['atado', 'atados'],
    'insumo': ['insumo', 'insumos']
}

def _normalizar_palabra(w):
    w = w.lower().strip()
    for base, variants in CONTAINER_TYPES.items():
        if w in variants:
            return base
    return w

def _score_presentacion(c_nombre, prod_name_raw):
    prod_lower = prod_name_raw.lower()
    c_lower = c_nombre.lower()

    score = 0.0

    # 1. Chequeo de coincidencia en tipo de contenedor/empaque (bolsa vs saco, etc.)
    for _base, variants in CONTAINER_TYPES.items():
        query_has_container = any(v in prod_lower for v in variants)
        cand_has_container = any(v in c_lower for v in variants)

        if query_has_container and cand_has_container:
            score += 100.0  # Coincidencia fuerte en empaque (ej: bolsa con bolsa)
        elif query_has_container and not cand_has_container:
            score -= 15.0   # La consulta especifica un empaque pero la presentación candidate no lo incluye

    # 2. Coincidencia de palabras clave limpias
    words_query = set(_normalizar_palabra(w) for w in re.findall(r'\w+', prod_lower))
    words_cand = set(_normalizar_palabra(w) for w in re.findall(r'\w+', c_lower))

    stopwords = {'de', 'con', 'para', 'kg', 'k', 'soles', 'unidades', 'unid', 'los', 'las'}
    words_query_clean = words_query - stopwords
    words_cand_clean = words_cand - stopwords

    if words_query_clean:
        overlap = words_query_clean.intersection(words_cand_clean)
        score += len(overlap) * 20.0

    # 3. Similaridad difflib independiente de la DB (funciona igual en SQLite y Postgres)
    seq_sim = difflib.SequenceMatcher(None, prod_lower, c_lower).ratio()
    score += seq_sim * 10.0

    return score

def buscar_presentacion(prod_name, tipos_validos=None):
    if not prod_name or not prod_name.strip():
        return None

    if tipos_validos is None:
        tipos_validos = ['procesado', 'briqueta']
    prod_name_safe = prod_name.replace('%', '').replace('_', '')

    weight = None
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:kg|k\b)', prod_name.lower())
    if match:
        weight = Decimal(match.group(1))
    else:
        match_number = re.search(r'\b(\d+(?:\.\d+)?)\b', prod_name.lower())
        if match_number:
            weight = Decimal(match_number.group(1))

    query_builder = PresentacionProducto.query.filter(PresentacionProducto.tipo.in_(tipos_validos))

    if weight is not None:
        candidatos = query_builder.filter(PresentacionProducto.capacidad_kg == weight).all()
        if candidatos:
            if len(candidatos) == 1:
                return candidatos[0]
            else:
                best_match = None
                best_score = -999.0
                for c in candidatos:
                    score = _score_presentacion(c.nombre, prod_name_safe)
                    if score > best_score:
                        best_score = score
                        best_match = c
                return best_match

    # Si no hay coincidencias exactas por peso o no se indicó peso, evaluar en todo el catálogo válido
    todos_candidatos = query_builder.all()
    if not todos_candidatos:
        todos_candidatos = PresentacionProducto.query.all()

    if todos_candidatos:
        best_match = None
        best_score = -999.0
        for c in todos_candidatos:
            score = _score_presentacion(c.nombre, prod_name_safe)
            if score > best_score:
                best_score = score
                best_match = c
        if best_score > 0:
            return best_match

    return None

def intentar_vinculacion(chat_id, text):
    code_match = re.search(r'\b(\d{6})\b', text)
    if not code_match:
        return False

    code = code_match.group(1)
    now = datetime.now(UTC)
    user = Users.query.filter(
        Users.telegram_linking_code == code,
        Users.telegram_linking_expires > now
    ).first()

    if not user:
        expired_user = Users.query.filter_by(telegram_linking_code=code).first()
        if expired_user:
            telegram_service.send_message(chat_id, "❌ El código de vinculación ha expirado. Por favor, genera uno nuevo en tu perfil de Manngo.")
            return True
        return False

    user.telegram_chat_id = chat_id
    user.telegram_linking_code = None
    user.telegram_linking_expires = None
    db.session.commit()

    telegram_service.send_message(
        chat_id,
        f"✅ <b>¡Vinculación Exitosa!</b>\n\n"
        f"Tu cuenta de Telegram ha sido asociada al usuario <b>{user.username}</b>.\n"
        f"Ya puedes empezar a registrar operaciones usando lenguaje natural."
    )
    return True

def zona_cliente(cliente):
    """Almacén al que pertenece un cliente: su preferido o, si no, el de su última venta."""
    if cliente is None:
        return None
    if cliente.almacen_preferido_id:
        almacen = db.session.get(Almacen, cliente.almacen_preferido_id)
        if almacen:
            return almacen
    ultima = (
        Venta.query.filter(Venta.cliente_id == cliente.id, Venta.almacen_id.isnot(None))
        .order_by(Venta.fecha.desc().nullslast(), Venta.id.desc())
        .first()
    )
    return db.session.get(Almacen, ultima.almacen_id) if ultima else None


def almacenes_permitidos(usuario):
    """Almacenes sobre los que el usuario puede vender (misma regla que autorizar_almacen)."""
    if usuario is None:
        return []
    if usuario.rol == 'admin':
        return Almacen.query.order_by(Almacen.nombre.asc()).all()
    if usuario.almacen_id:
        almacen = db.session.get(Almacen, usuario.almacen_id)
        return [almacen] if almacen else []
    return []


_GASTO_RE = re.compile(r'^\s*(?:s/\.?\s*)?(\d+(?:[.,]\d+)?)\s*(.*)$', re.IGNORECASE)


def parsear_gasto(texto):
    """'20 flete' -> (Decimal('20'), 'flete'). Devuelve None si no hay un monto positivo."""
    match = _GASTO_RE.match(texto or "")
    if not match:
        return None
    monto = Decimal(match.group(1).replace(',', '.'))
    if monto <= 0:
        return None
    descripcion = match.group(2).strip() or "Gasto de venta"
    return monto, descripcion


def _preferir_por_almacen(candidatos, almacen_id):
    """Entre varios clientes, el de ese almacén (preferido y luego con ventas allí)."""
    if not candidatos:
        return None
    if almacen_id is None or len(candidatos) == 1:
        return candidatos[0]
    for cli in candidatos:
        if cli.almacen_preferido_id == almacen_id:
            return cli
    ids = [c.id for c in candidatos]
    con_ventas = {
        cid for (cid,) in db.session.query(Venta.cliente_id)
        .filter(Venta.cliente_id.in_(ids), Venta.almacen_id == almacen_id)
        .distinct()
    }
    for cli in candidatos:
        if cli.almacen_preferido_id is None and cli.id in con_ventas:
            return cli
    return candidatos[0]


def buscar_cliente_db(nombre: str, telefono: str = None, ruc: str = None, almacen_id: int = None):
    """
    Busca un cliente por teléfono, ruc, o realiza una búsqueda semántica por nombre.
    Con ``almacen_id``, entre varias coincidencias por nombre prefiere las de ese almacén;
    si solo hay clientes de otro almacén, devuelve igualmente el primero (la tarjeta alerta).
    Retorna el cliente encontrado o None.
    """
    if not nombre and not telefono and not ruc:
        return None

    if telefono:
        cli = Cliente.query.filter_by(telefono=telefono).first()
        if cli:
            return cli

    if ruc:
        cli = Cliente.query.filter_by(ruc=ruc).first()
        if cli:
            return cli

    if nombre:
        nombre_limpio = nombre.strip()
        # Búsqueda exacta case-insensitive
        exactos = (
            Cliente.query.filter(Cliente.nombre.ilike(nombre_limpio))
            .order_by(Cliente.id.asc()).all()
        )
        if exactos:
            return _preferir_por_almacen(exactos, almacen_id)

        # Búsqueda parcial (contiene)
        parciales = (
            Cliente.query.filter(Cliente.nombre.ilike(f"%{nombre_limpio}%"))
            .order_by(Cliente.id.asc()).limit(20).all()
        )
        if parciales:
            return _preferir_por_almacen(parciales, almacen_id)

        # Búsqueda por similitud léxica (Postgres pg_trgm)
        try:
            cli = Cliente.query.filter(func.similarity(Cliente.nombre, nombre) > 0.3).order_by(func.similarity(Cliente.nombre, nombre).desc()).first()
            if cli:
                return cli
        except Exception as e:
            logger.warning(f"Error en búsqueda por similitud de cliente '{nombre}': {e}")
            pass

    return None
