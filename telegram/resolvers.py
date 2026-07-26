import re
import logging
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import func

from extensions import db
from models import Users, Almacen, PresentacionProducto
from services.telegram_service import telegram_service

logger = logging.getLogger(__name__)

def resolver_almacen(user, text):
    if user.rol == 'admin':
        almacenes = Almacen.query.all()
        for al in almacenes:
            if al.nombre.lower() in text.lower():
                return al.id, al.nombre
        if user.almacen_id:
            al = db.session.get(Almacen, user.almacen_id)
            return user.almacen_id, al.nombre if al else "Desconocido"
        return None, None
    else:
        if user.almacen_id:
            al = db.session.get(Almacen, user.almacen_id)
            return user.almacen_id, al.nombre if al else "Desconocido"
        return None, None

import difflib

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
    for base, variants in CONTAINER_TYPES.items():
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
    now = datetime.now(timezone.utc)
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
