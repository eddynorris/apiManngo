import google.generativeai as genai
import os
import json
import logging
import re
from datetime import datetime
from utils.date_utils import get_peru_now

logger = logging.getLogger(__name__)

class GeminiService:
    def __init__(self):
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("GOOGLE_API_KEY no configurada.")
        else:
            genai.configure(api_key=api_key)
            
        # Usamos gemini-1.5-flash por ser estable, rápido y económico para function calling
        self.model_name = "gemini-flash-lite-latest"
        
        # Definición de herramientas
        self.tools = [
            {
                "function_declarations": [
                    {
                        "name": "interpretar_operacion",
                        "description": "Interpreta una operación comercial compleja que puede incluir ventas, pagos y gastos.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "cliente_nombre": {
                                    "type": "STRING",
                                    "description": "Nombre del cliente para la venta."
                                },
                                "items": {
                                    "type": "ARRAY",
                                    "description": "Lista de productos vendidos.",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "producto_nombre": {
                                                "type": "STRING",
                                                "description": "Nombre del producto o presentación."
                                            },
                                            "cantidad": {
                                                "type": "INTEGER",
                                                "description": "Cantidad vendida."
                                            },
                                            "precio": {
                                                "type": "NUMBER",
                                                "description": "Precio unitario explícito si se menciona (ej: 'a 50 soles'). Si no, null."
                                            }
                                        },
                                        "required": ["producto_nombre", "cantidad"]
                                    }
                                },
                                "pagos": {
                                    "type": "ARRAY",
                                    "description": "Lista de pagos explícitos (si se mencionan montos específicos).",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "monto": {
                                                "type": "NUMBER",
                                                "description": "Monto del pago."
                                            },
                                            "metodo_pago": {
                                                "type": "STRING",
                                                "description": "Método de pago.",
                                                "enum": ["efectivo", "yape_plin", "transferencia", "tarjeta", "deposito", "otro"]
                                            },
                                            "es_deposito": {
                                                "type": "BOOLEAN",
                                                "description": "True si es depósito directo."
                                            }
                                        },
                                        "required": ["monto"]
                                    }
                                },
                                "condicion_pago": {
                                    "type": "STRING",
                                    "description": "Indica si el pago es total, al crédito o parcial manual.",
                                    "enum": ["completo", "credito", "parcial"]
                                },
                                "porcentaje_abono": {
                                    "type": "INTEGER",
                                    "description": "Porcentaje del total a pagar (ej: 50 para 'la mitad'). Null si no aplica."
                                },
                                "gasto_asociado": {
                                    "type": "OBJECT",
                                    "description": "Gasto operativo mencionado.",
                                    "properties": {
                                        "descripcion": { "type": "STRING" },
                                        "monto": { "type": "NUMBER" },
                                        "categoria": {
                                            "type": "STRING",
                                            "enum": ["logistica", "personal", "otros"]
                                        }
                                    },
                                    "required": ["descripcion", "monto", "categoria"]
                                }
                            },
                            "required": ["cliente_nombre", "items"]
                        }
                    }
                ]
            }
        ]
    
    def _sanitize_input(self, text):
        """
        Sanitización robusta contra prompt injection y malformed input.
        """
        if not text or not isinstance(text, str):
            raise ValueError("Input inválido")
        
        # 1. Límite de longitud
        MAX_LENGTH = 500
        if len(text) > MAX_LENGTH:
            logger.warning(f"Input truncado: {len(text)} -> {MAX_LENGTH}")
            text = text[:MAX_LENGTH]
        
        # 2. Normalización de espacios
        text = " ".join(text.split())
        
        # 3. Detección de patrones de jailbreak (mejorado)
        jailbreak_patterns = [
            # Patrones en español
            r"(?i)(ignora|olvida|borra).{0,15}(instrucciones|reglas|sistema)",
            r"(?i)(actúa|comportate|responde).{0,15}como.{0,15}(si|un)",
            r"(?i)tu.{0,10}(nuevo|verdadero).{0,10}(rol|trabajo|propósito)",
            r"(?i)(desactiva|deshabilita|apaga).{0,15}(filtros|restricciones)",
            
            # Patrones en inglés
            r"(?i)(ignore|forget|disregard).{0,15}(previous|above|instructions)",
            r"(?i)(act|pretend|behave).{0,15}as.{0,15}(if|a|an)",
            r"(?i)your.{0,10}(new|real).{0,10}(role|purpose|job)",
            r"(?i)(disable|turn off).{0,15}(safety|filters)",
            
            # Patrones de inyección de comandos
            r"(?i)(system|admin|root).{0,10}(prompt|instruction|mode)",
            r"(?i)(ahora|now).{0,15}(eres|you are).{0,15}(un|a)",
            
            # Intentos de escapar del contexto
            r"[\[\]<>{}].*instruc",  # Intentos con delimitadores
            r"(?i)(print|echo|show).{0,10}(system|prompt|instruction)"
        ]
        
        for pattern in jailbreak_patterns:
            if re.search(pattern, text):
                logger.warning(f"⚠️ Prompt injection detectado: {text[:100]}")
                raise ValueError("Comando rechazado por seguridad. Evita instrucciones al sistema.")
        
        # 4. Validar que no sea solo caracteres especiales
        if len(re.sub(r'[^a-zA-Z0-9áéíóúñÁÉÍÓÚÑ]', '', text)) < 3:
            raise ValueError("Comando demasiado corto o inválido")
        
        return text
    
    def _validate_output(self, args):
        """
        Validación mejorada de la respuesta de Gemini.
        """
        # Validar estructura básica
        if not isinstance(args, dict):
            raise ValueError("Respuesta malformada de Gemini")
        
        # Validar cliente
        if 'cliente_nombre' in args:
            cliente = args['cliente_nombre']
            if not cliente or len(cliente) < 2:
                raise ValueError("Nombre de cliente inválido")
            if len(cliente) > 100:
                logger.warning(f"Nombre de cliente truncado: {cliente}")
                args['cliente_nombre'] = cliente[:100]
        
        # Validar items
        if 'items' in args and isinstance(args['items'], list):
            if len(args['items']) > 50:  # Límite razonable
                logger.warning("Demasiados items, truncando a 50")
                args['items'] = args['items'][:50]
            
            for item in args['items']:
                # Validar cantidad
                cantidad = item.get('cantidad', 0)
                if not isinstance(cantidad, (int, float)) or cantidad <= 0:
                    logger.warning(f"Cantidad inválida corregida: {cantidad} -> 1")
                    item['cantidad'] = 1
                elif cantidad > 10000:  # Límite de cordura
                    logger.warning(f"Cantidad sospechosa: {cantidad}")
                    item['cantidad'] = 10000
                
                # Validar precio
                precio = item.get('precio')
                if precio is not None:
                    if not isinstance(precio, (int, float)) or precio < 0:
                        logger.warning(f"Precio inválido ignorado: {precio}")
                        item['precio'] = None
                    elif precio > 100000:  # Límite de cordura
                        logger.warning(f"Precio sospechoso: {precio}")
                        item['precio'] = None
                
                # Validar nombre de producto
                prod_nombre = item.get('producto_nombre', '')
                if len(prod_nombre) < 2 or len(prod_nombre) > 200:
                    raise ValueError(f"Nombre de producto inválido: {prod_nombre}")
        
        # Validar pagos
        if 'pagos' in args and isinstance(args['pagos'], list):
            for pago in args['pagos']:
                monto = pago.get('monto', 0)
                if not isinstance(monto, (int, float)) or monto < 0:
                    raise ValueError(f"Monto de pago negativo o inválido: {monto}")
                if monto > 1000000:  # Límite de cordura
                    logger.warning(f"Monto de pago sospechoso: {monto}")
        
        # Validar gasto asociado
        if 'gasto_asociado' in args and args['gasto_asociado']:
            gasto = args['gasto_asociado']
            if gasto.get('monto', 0) < 0:
                raise ValueError("Monto de gasto negativo")
            if gasto.get('monto', 0) > 100000:
                logger.warning(f"Gasto sospechoso: {gasto.get('monto')}")
        
        return args
    
    def _build_system_prompt(self):
        """
        Construye un system prompt optimizado con estructura clara y ejemplos.
        """
        fecha_actual = get_peru_now().strftime('%Y-%m-%d %H:%M')
        
        return f"""Eres el asistente de voz de Manngo, un sistema de gestión de ventas de carbón/briquetas.
Tu ÚNICA función es extraer datos estructurados de comandos de voz transcritos.

════════════════════════════════════════════════════════════════
📅 CONTEXTO ACTUAL
════════════════════════════════════════════════════════════════
Fecha/Hora: {fecha_actual}
Ubicación: Perú (moneda: Soles S/)

════════════════════════════════════════════════════════════════
🎯 REGLAS DE INTERPRETACIÓN
════════════════════════════════════════════════════════════════

1️⃣ IDENTIFICACIÓN DE CLIENTE
   • Extrae el nombre del cliente mencionado
   • Si no se menciona cliente: usa "Cliente Genérico"

2️⃣ PRODUCTOS POR PESO (CRÍTICO)
   • Este negocio vende productos diferenciados por KILOGRAMOS
   • Presentaciones comunes: 3kg, 4kg, 5kg, 10kg, 20kg, 30kg
   • SIEMPRE incluye el peso en el nombre del producto
   
   Ejemplos de mapeo:
   "saco de 20"           → "20kg" o "saco 20kg"
   "bolsa de diez"        → "10kg" o "bolsa 10kg"  
   "un saco grande"       → "30kg" (saco más grande típico)
   "saco chico"           → "10kg" (saco más chico típico)
   "tres de 20"           → cantidad=3, producto="20kg"

3️⃣ PRECIOS EXPLÍCITOS
   • Si dice "a 50 soles", "por 80", etc. → asigna precio al item
   • Si NO menciona precio → deja en null

4️⃣ CONDICIONES DE PAGO
   ┌─────────────────────────────────────────────────────────┐
   │ COMPLETO: "pago completo", "ya pagó todo", "canceló",  │
   │           "al contado", "pagó en efectivo"              │
   │           → condicion_pago = "completo"                 │
   │           → NO agregues monto (se calcula automático)   │
   ├─────────────────────────────────────────────────────────┤
   │ CRÉDITO:  "al crédito", "fiado", "luego paga",         │
   │           "anotado", "debe"                             │
   │           → condicion_pago = "credito"                  │
   │           → pagos = []                                  │
   ├─────────────────────────────────────────────────────────┤
   │ PARCIAL:  "pagó 500", "dejó 300", "dio 100"            │
   │           → condicion_pago = "parcial"                  │
   │           → agrega el monto específico a pagos[]        │
   ├─────────────────────────────────────────────────────────┤
   │ RELATIVO: "pagó la mitad", "dejó el 50%", "un tercio"   │
   │           → condicion_pago = "parcial"                  │
   │           → porcentaje_abono = 50 (para mitad), 33, etc.│
   └─────────────────────────────────────────────────────────┘

5️⃣ MÉTODOS DE PAGO
   • "con yape" / "por yape" → "yape_plin"
   • "en efectivo" / "cash"  → "efectivo"
   • "transferencia"         → "transferencia"
   • "con tarjeta"           → "tarjeta"
   • Si dice "pago completo con yape":
     → condicion_pago="completo", agrega pago con monto=0 y metodo="yape_plin"

6️⃣ GASTOS OPERATIVOS
   • "costó 30 el envío"     → gasto_asociado con categoría "logistica"
   • "le pagué 50 al ayudante" → categoría "personal"
   • Categorías: logistica, personal, otros

════════════════════════════════════════════════════════════════
📚 EJEMPLOS DE INTERPRETACIÓN
════════════════════════════════════════════════════════════════

Entrada: "vendí 3 sacos de 20 a juan pérez pago completo"
Salida:
{{
  "cliente_nombre": "Juan Pérez",
  "items": [
    {{"producto_nombre": "20kg", "cantidad": 3, "precio": null}}
  ],
  "condicion_pago": "completo",
  "pagos": []
}}

────────────────────────────────────────────────────────────────

Entrada: "dos bolsas de diez para maría al crédito"
Salida:
{{
  "cliente_nombre": "María",
  "items": [
    {{"producto_nombre": "10kg", "cantidad": 2, "precio": null}}
  ],
  "condicion_pago": "credito",
  "pagos": []
}}

────────────────────────────────────────────────────────────────

Entrada: "carlos compró 5 sacos de 30 a 150 soles pagó 500 con yape"
Salida:
{{
  "cliente_nombre": "Carlos",
  "items": [
    {{"producto_nombre": "30kg", "cantidad": 5, "precio": 150}}
  ],
  "condicion_pago": "parcial",
  "pagos": [
    {{"monto": 500, "metodo_pago": "yape_plin", "es_deposito": false}}
  ]
}}

────────────────────────────────────────────────────────────────

Entrada: "vendí un saco grande a rosa pago completo con efectivo y costó 20 el delivery"
Salida:
{{
  "cliente_nombre": "Rosa",
  "items": [
    {{"producto_nombre": "30kg", "cantidad": 1, "precio": null}}
  ],
  "condicion_pago": "completo",
  "pagos": [],
  "gasto_asociado": {{
    "descripcion": "delivery",
    "monto": 20,
    "categoria": "logistica"
  }}
}}

════════════════════════════════════════════════════════════════
⚠️ RESTRICCIONES CRÍTICAS
════════════════════════════════════════════════════════════════
• NUNCA inventes información que no esté en el comando
• NUNCA uses tu conocimiento del mundo para asumir precios
• NUNCA cambies los nombres de personas mencionadas
• SIEMPRE prioriza los KILOGRAMOS en nombres de productos
• Si algo es ambiguo, extrae lo más literal posible

Tu output DEBE ser SOLO el function call, sin texto adicional."""

    def process_command(self, text):
        """
        Procesa un comando de texto usando Gemini con prompt optimizado.
        """
        try:
            # 1. Sanitización robusta
            clean_text = self._sanitize_input(text)
            
            # 2. Crear modelo con system instruction optimizado
            model = genai.GenerativeModel(
                self.model_name,
                tools=self.tools,
                system_instruction=self._build_system_prompt()
            )
            
            # 3. Generar respuesta
            response = model.generate_content(clean_text)
            
            # 4. Inicializar resultado por defecto
            result = {
                "action": "none",
                "message": "No entendí el comando. Intenta reformular."
            }
            
            # 5. Procesar respuesta
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        fn_call = part.function_call
                        
                        # Convertir argumentos a diccionario nativo
                        def recursive_to_dict(obj):
                            if hasattr(obj, 'items'):
                                return {k: recursive_to_dict(v) for k, v in obj.items()}
                            elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
                                return [recursive_to_dict(v) for v in obj]
                            else:
                                return obj
                        
                        args_dict = recursive_to_dict(fn_call.args)
                        
                        # 6. Validación de output
                        try:
                            args_dict = self._validate_output(args_dict)
                        except ValueError as ve:
                            logger.error(f"Validation error: {ve}")
                            return {
                                "action": "error",
                                "message": f"Error de validación: {str(ve)}"
                            }
                        
                        result = {
                            "action": fn_call.name,
                            "args": args_dict,
                            "message": f"Procesando: {fn_call.name}"
                        }
                        break
                    elif hasattr(part, 'text') and part.text:
                        result["message"] = part.text
            
            return result
        
        except ValueError as ve:
            logger.warning(f"Security/validation block: {ve}")
            return {
                "action": "security_block",
                "message": str(ve)
            }
        except Exception as e:
            logger.error(f"Error en GeminiService: {e}", exc_info=True)
            return {
                "action": "error",
                "message": "Error interno al procesar comando de voz."
            }

# Instancia global
gemini_service = GeminiService()