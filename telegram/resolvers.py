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

def buscar_presentacion(prod_name, tipos_validos=None):
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
            
    if weight is not None:
        candidatos = PresentacionProducto.query.filter(
            PresentacionProducto.capacidad_kg == weight,
            PresentacionProducto.tipo.in_(tipos_validos)
        ).all()
        
        if candidatos:
            if len(candidatos) == 1:
                return candidatos[0]
            else:
                best_match = None
                best_score = -1.0
                for c in candidatos:
                    try:
                        score = db.session.query(func.similarity(c.nombre, prod_name_safe)).scalar() or 0.0
                    except Exception:
                        score = 1.0 if prod_name_safe.lower() in c.nombre.lower() else 0.0
                    if score > best_score:
                        best_score = score
                        best_match = c
                return best_match

    presentacion = PresentacionProducto.query.filter(
        PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%"),
        PresentacionProducto.tipo.in_(tipos_validos)
    ).first()

    if not presentacion:
        try:
            presentacion = PresentacionProducto.query.filter(
                func.similarity(PresentacionProducto.nombre, prod_name_safe) > 0.3,
                PresentacionProducto.tipo.in_(tipos_validos)
            ).order_by(func.similarity(PresentacionProducto.nombre, prod_name_safe).desc()).first()
        except Exception:
            pass

    if not presentacion:
        presentacion = PresentacionProducto.query.filter(
            PresentacionProducto.nombre.ilike(f"%{prod_name_safe}%")
        ).first()

    return presentacion

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
