"""
Máquina de estados para conversaciones del bot de Telegram.
Permite flujos multi-paso donde el bot puede pedir información faltante.
"""
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List
from extensions import db


class ConversationState(str, Enum):
    """Estados posibles de una conversación."""
    IDLE = "idle"                              # Sin conversación activa
    AWAITING_CONFIRMATION = "awaiting_confirm" # Tarjeta mostrada, esperando confirmación
    AWAITING_CLIENT_INFO = "awaiting_client"   # Pidiendo datos de cliente nuevo
    AWAITING_ALMACEN = "awaiting_almacen"      # Pidiendo almacén
    AWAITING_PRICE = "awaiting_price"          # Pidiendo precio
    AWAITING_EXTRA_DATA = "awaiting_extra"     # Pidiendo información adicional
    AWAITING_EDIT = "awaiting_edit"            # Esperando edición de datos


class ConversationContext:
    """Contexto de una conversación en progreso."""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.state = ConversationState.IDLE
        self.action: Optional[str] = None  # "venta", "gasto", "pago", etc.
        self.data: Dict[str, Any] = {}     # Datos acumulados
        self.missing_fields: List[str] = []  # Campos que faltan
        self.current_question: Optional[str] = None  # Pregunta actual
        self.created_at: datetime = datetime.now(timezone.utc)
        self.last_activity: datetime = datetime.now(timezone.utc)
        self.message_id: Optional[int] = None  # ID del mensaje de confirmación
    
    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """Verifica si el contexto ha expirado."""
        return datetime.now(timezone.utc) - self.last_activity > timedelta(minutes=timeout_minutes)
    
    def update_activity(self):
        """Actualiza el timestamp de última actividad."""
        self.last_activity = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el contexto a diccionario para almacenar en BD."""
        return {
            "state": self.state.value,
            "action": self.action,
            "data": self.data,
            "missing_fields": self.missing_fields,
            "current_question": self.current_question,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "message_id": self.message_id
        }
    
    @classmethod
    def from_dict(cls, user_id: int, data: Dict[str, Any]) -> 'ConversationContext':
        """Crea un contexto desde un diccionario almacenado en BD."""
        if not data:
            return cls(user_id)
        
        ctx = cls(user_id)
        ctx.state = ConversationState(data.get("state", "idle"))
        ctx.action = data.get("action")
        ctx.data = data.get("data", {})
        ctx.missing_fields = data.get("missing_fields", [])
        ctx.current_question = data.get("current_question")
        
        created_at_str = data.get("created_at")
        if created_at_str:
            ctx.created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
        
        last_activity_str = data.get("last_activity")
        if last_activity_str:
            ctx.last_activity = datetime.fromisoformat(last_activity_str.replace('Z', '+00:00'))
        
        ctx.message_id = data.get("message_id")
        return ctx


class StateMachine:
    """Máquina de estados para manejar conversaciones multi-paso."""
    
    TIMEOUT_MINUTES = 30
    
    @staticmethod
    def get_context(user) -> ConversationContext:
        """Obtiene el contexto de conversación actual del usuario."""
        if not user.telegram_context:
            return ConversationContext(user.id)
        
        ctx = ConversationContext.from_dict(user.id, user.telegram_context)
        
        # Verificar expiración
        if ctx.is_expired(StateMachine.TIMEOUT_MINUTES):
            StateMachine.clear_context(user)
            return ConversationContext(user.id)
        
        return ctx
    
    @staticmethod
    def save_context(user, context: ConversationContext):
        """Guarda el contexto de conversación en el usuario."""
        context.update_activity()
        user.telegram_context = context.to_dict()
        db.session.commit()
    
    @staticmethod
    def clear_context(user):
        """Limpia el contexto de conversación."""
        user.telegram_context = None
        db.session.commit()
    
    @staticmethod
    def transition_to(user, new_state: ConversationState, action: Optional[str] = None, data: Optional[Dict[str, Any]] = None):
        """Transiciona a un nuevo estado."""
        ctx = StateMachine.get_context(user)
        ctx.state = new_state
        if action:
            ctx.action = action
        if data:
            ctx.data.update(data)
        StateMachine.save_context(user, ctx)
        return ctx
    
    @staticmethod
    def ask_for_client_creation(user, cliente_nombre: str, action: str, data: Dict[str, Any]):
        """Inicia el flujo de creación de cliente."""
        ctx = StateMachine.get_context(user)
        ctx.state = ConversationState.AWAITING_CLIENT_INFO
        ctx.action = action
        ctx.data = data
        ctx.data["pending_client_name"] = cliente_nombre
        ctx.current_question = f"No encontré un cliente llamado '{cliente_nombre}'. ¿Deseas crearlo? Responde con:\n\n• Sí, crearlo\n• No, usar cliente genérico\n• Nombre correcto del cliente"
        StateMachine.save_context(user, ctx)
        return ctx
    
    @staticmethod
    def ask_for_almacen(user, action: str, data: Dict[str, Any]):
        """Inicia el flujo de selección de almacén."""
        ctx = StateMachine.get_context(user)
        ctx.state = ConversationState.AWAITING_ALMACEN
        ctx.action = action
        ctx.data = data
        ctx.current_question = "¿Desde qué almacén se realiza esta operación?"
        StateMachine.save_context(user, ctx)
        return ctx
    
    @staticmethod
    def ask_for_price(user, action: str, data: Dict[str, Any], item_index: int):
        """Inicia el flujo de solicitud de precio."""
        ctx = StateMachine.get_context(user)
        ctx.state = ConversationState.AWAITING_PRICE
        ctx.action = action
        ctx.data = data
        ctx.data["price_item_index"] = item_index
        ctx.current_question = f"¿Cuál es el precio para el item #{item_index + 1}?"
        StateMachine.save_context(user, ctx)
        return ctx
    
    @staticmethod
    def set_awaiting_confirmation(user, action: str, data: Dict[str, Any], message_id: int):
        """Establece el estado de espera de confirmación."""
        ctx = StateMachine.get_context(user)
        ctx.state = ConversationState.AWAITING_CONFIRMATION
        ctx.action = action
        ctx.data = data
        ctx.message_id = message_id
        StateMachine.save_context(user, ctx)
        return ctx
    
    @staticmethod
    def is_in_flow(user) -> bool:
        """Verifica si el usuario está en medio de un flujo conversacional."""
        ctx = StateMachine.get_context(user)
        return ctx.state != ConversationState.IDLE and ctx.state != ConversationState.AWAITING_CONFIRMATION
    
    @staticmethod
    def handle_user_response(user, text: str) -> Optional[Dict[str, Any]]:
        """
        Procesa la respuesta del usuario en medio de un flujo.
        Retorna un dict con la acción a tomar o None si no hay flujo activo.
        """
        ctx = StateMachine.get_context(user)
        
        if ctx.state == ConversationState.IDLE or ctx.state == ConversationState.AWAITING_CONFIRMATION:
            return None
        
        ctx.update_activity()
        
        # Flujo de creación de cliente
        if ctx.state == ConversationState.AWAITING_CLIENT_INFO:
            return StateMachine._handle_client_creation_response(user, ctx, text)
        
        # Flujo de selección de almacén
        if ctx.state == ConversationState.AWAITING_ALMACEN:
            return StateMachine._handle_almacen_response(user, ctx, text)
        
        # Flujo de solicitud de precio
        if ctx.state == ConversationState.AWAITING_PRICE:
            return StateMachine._handle_price_response(user, ctx, text)
        
        # Flujo de datos extra (edición de campos)
        if ctx.state == ConversationState.AWAITING_EXTRA_DATA:
            return StateMachine._handle_extra_data_response(user, ctx, text)
        
        return None
    
    @staticmethod
    def _handle_client_creation_response(user, ctx: ConversationContext, text: str) -> Dict[str, Any]:
        """Procesa la respuesta para creación de cliente."""
        text_lower = text.lower().strip()
        
        # Usuario quiere crear el cliente
        if text_lower in ["sí", "si", "sí, crearlo", "si, crearlo", "crear", "crear cliente"]:
            StateMachine.clear_context(user)
            return {
                "action": "create_client",
                "client_name": ctx.data.get("pending_client_name"),
                "original_action": ctx.action,
                "original_data": ctx.data
            }
        
        # Usuario no quiere crear el cliente
        if text_lower in ["no", "no, usar genérico", "usar genérico", "cancelar"]:
            StateMachine.clear_context(user)
            return {
                "action": "use_generic_client",
                "original_action": ctx.action,
                "original_data": ctx.data
            }
        
        # Usuario proporciona nombre correcto
        # Asumimos que cualquier otra respuesta es el nombre correcto del cliente
        StateMachine.clear_context(user)
        return {
            "action": "retry_with_correct_name",
            "correct_name": text.strip(),
            "original_action": ctx.action,
            "original_data": ctx.data
        }
    
    @staticmethod
    def _handle_almacen_response(user, ctx: ConversationContext, text: str) -> Dict[str, Any]:
        """Procesa la respuesta para selección de almacén."""
        StateMachine.clear_context(user)
        return {
            "action": "set_almacen",
            "almacen_name": text.strip(),
            "original_action": ctx.action,
            "original_data": ctx.data
        }
    
    @staticmethod
    def _handle_price_response(user, ctx: ConversationContext, text: str) -> Dict[str, Any]:
        """Procesa la respuesta para solicitud de precio."""
        try:
            price = float(text.strip())
            item_index = ctx.data.get("price_item_index", 0)
            
            # Actualizar el precio en los datos
            if "items" in ctx.data and item_index < len(ctx.data["items"]):
                ctx.data["items"][item_index]["precio"] = price
            
            StateMachine.clear_context(user)
            return {
                "action": "set_price",
                "price": price,
                "item_index": item_index,
                "original_action": ctx.action,
                "original_data": ctx.data
            }
        except ValueError:
            # Precio inválido, mantener el estado
            return {
                "action": "invalid_price",
                "message": "Precio inválido. Por favor ingresa un número válido."
            }
    
    @staticmethod
    def _handle_extra_data_response(user, ctx: ConversationContext, text: str) -> Dict[str, Any]:
        """Procesa la respuesta para edición de campos (cliente, precio, gasto, almacén)."""
        edit_action = ctx.action
        original_context = ctx.data.get("original_context", {})
        message_id = ctx.data.get("message_id")
        
        # Limpiar el flujo de edición
        StateMachine.clear_context(user)
        
        return {
            "action": "edit_response",
            "edit_action": edit_action,
            "value": text.strip(),
            "original_context": original_context,
            "message_id": message_id
        }
