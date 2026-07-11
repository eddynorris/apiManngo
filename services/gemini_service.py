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
            
        self.model_name = "gemini-flash-lite-latest"
        
        # Definición de herramientas
        self.tools = [
            {
                "function_declarations": [
                    {
                        "name": "interpretar_operacion",
                        "description": "Interpreta una venta comercial compleja que puede incluir cliente, items, pagos y gastos asociados.",
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
                                                "description": "Nombre del producto o presentacion."
                                            },
                                            "cantidad": {
                                                "type": "INTEGER",
                                                "description": "Cantidad vendida."
                                            },
                                            "precio": {
                                                "type": "NUMBER",
                                                "description": "Precio unitario explicito si se menciona (ej: 'a 50 soles'). Si no, null."
                                            }
                                        },
                                        "required": ["producto_nombre", "cantidad"]
                                    }
                                },
                                "pagos": {
                                    "type": "ARRAY",
                                    "description": "Lista de pagos explicitos.",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "monto": {
                                                "type": "NUMBER",
                                                "description": "Monto del pago."
                                            },
                                            "metodo_pago": {
                                                "type": "STRING",
                                                "description": "Metodo de pago.",
                                                "enum": ["efectivo", "yape_plin", "transferencia", "tarjeta", "deposito", "otro"]
                                            },
                                            "es_deposito": {
                                                "type": "BOOLEAN",
                                                "description": "True si es deposito directo."
                                            }
                                        },
                                        "required": ["monto"]
                                    }
                                },
                                "condicion_pago": {
                                    "type": "STRING",
                                    "description": "Indica si el pago es total, al credito o parcial.",
                                    "enum": ["completo", "credito", "parcial"]
                                },
                                "porcentaje_abono": {
                                    "type": "INTEGER",
                                    "description": "Porcentaje del total a pagar (ej: 50 para 'la mitad')."
                                },
                                "gasto_asociado": {
                                    "type": "OBJECT",
                                    "description": "Gasto operativo mencionado.",
                                    "properties": {
                                        "descripcion": { "type": "STRING" },
                                        "monto": { "type": "NUMBER" },
                                        "categoria": {
                                            "type": "STRING",
                                            "enum": ["logistica", "personal", "insumos", "otros"]
                                        }
                                    },
                                    "required": ["descripcion", "monto", "categoria"]
                                }
                            },
                            "required": ["cliente_nombre", "items"]
                        }
                    },
                    {
                        "name": "registrar_gasto",
                        "description": "Registra un gasto operativo independiente del negocio.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "descripcion": {
                                    "type": "STRING",
                                    "description": "Descripcion o concepto del gasto."
                                },
                                "monto": {
                                    "type": "NUMBER",
                                    "description": "Monto total del gasto."
                                },
                                "categoria": {
                                    "type": "STRING",
                                    "description": "Categoria del gasto.",
                                    "enum": ["logistica", "personal", "insumos", "otros"]
                                }
                            },
                            "required": ["descripcion", "monto", "categoria"]
                        }
                    },
                    {
                        "name": "registrar_pago",
                        "description": "Registra un abono/pago de deuda para una venta existente o de un cliente.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "cliente_nombre": {
                                    "type": "STRING",
                                    "description": "Nombre del cliente que abona o paga."
                                },
                                "monto": {
                                    "type": "NUMBER",
                                    "description": "Monto del abono."
                                },
                                "metodo_pago": {
                                    "type": "STRING",
                                    "description": "Metodo de pago utilizado.",
                                    "enum": ["efectivo", "yape_plin", "transferencia", "tarjeta", "deposito", "otro"]
                                },
                                "referencia": {
                                    "type": "STRING",
                                    "description": "Codigo o numero de referencia del comprobante."
                                }
                            },
                            "required": ["monto", "metodo_pago"]
                        }
                    },
                    {
                        "name": "registrar_deposito",
                        "description": "Registra el deposito en banco de un pago o saldo recibido en efectivo/gerencia.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "monto_depositado": {
                                    "type": "NUMBER",
                                    "description": "Monto depositado en la cuenta corporativa."
                                },
                                "referencia": {
                                    "type": "STRING",
                                    "description": "Numero de operacion del deposito."
                                }
                            },
                            "required": ["monto_depositado"]
                        }
                    },
                    {
                        "name": "registrar_produccion",
                        "description": "Registra la produccion de un producto terminado (anadiendo inventario final).",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "producto_nombre": {
                                    "type": "STRING",
                                    "description": "Nombre de la presentacion final producida (ej: 'saco 20kg', 'briquetas 5kg')."
                                },
                                "cantidad_a_producir": {
                                    "type": "NUMBER",
                                    "description": "Cantidad de unidades producidas."
                                }
                            },
                            "required": ["producto_nombre", "cantidad_a_producir"]
                        }
                    }
                ]
            }
        ]
    
    def _sanitize_input(self, text):
        """
        Sanitizacion robusta contra prompt injection and malformed input.
        """
        if not text or not isinstance(text, str):
            raise ValueError("Input invalido")
        
        # 1. Limite de longitud
        MAX_LENGTH = 500
        if len(text) > MAX_LENGTH:
            logger.warning(f"Input truncado: {len(text)} -> {MAX_LENGTH}")
            text = text[:MAX_LENGTH]
        
        # 2. Normalizacion de espacios
        text = " ".join(text.split())
        
        # 3. Deteccion de patrones de jailbreak (mejorado)
        jailbreak_patterns = [
            # Patrones en espanol
            r"(?i)(ignora|olvida|borra).{0,15}(instrucciones|reglas|sistema)",
            r"(?i)(actua|comportate|responde).{0,15}como.{0,15}(si|un)",
            r"(?i)tu.{0,10}(nuevo|verdadero).{0,10}(rol|trabajo|proposito)",
            r"(?i)(desactiva|deshabilita|apaga).{0,15}(filtros|restricciones)",
            
            # Patrones en ingles
            r"(?i)(ignore|forget|disregard).{0,15}(previous|above|instructions)",
            r"(?i)(act|pretend|behave).{0,15}as.{0,15}(if|a|an)",
            r"(?i)your.{0,10}(new|real).{0,10}(role|purpose|job)",
            r"(?i)(disable|turn off).{0,15}(safety|filters)",
            
            # Patrones de inyeccion de comandos
            r"(?i)(system|admin|root).{0,10}(prompt|instruction|mode)",
            r"(?i)(ahora|now).{0,15}(eres|you are).{0,15}(un|a)",
            
            # Intentos de escapar del contexto
            r"[\[\]<>{}].*instruc",  # Intentos con delimitadores
            r"(?i)(print|echo|show).{0,10}(system|prompt|instruction)"
        ]
        
        for pattern in jailbreak_patterns:
            if re.search(pattern, text):
                logger.warning(f"Prompt injection detectado: {text[:100]}")
                raise ValueError("Comando rechazado por seguridad. Evita instrucciones al sistema.")
        
        # 4. Validar que no sea solo caracteres especiales
        if len(re.sub(r'[^a-zA-Z0-9]', '', text)) < 3:
            raise ValueError("Comando demasiado corto o invalido")
        
        return text

    def _validate_output(self, args):
        """
        Validacion mejorada de la respuesta de Gemini.
        """
        # Validar estructura basica
        if not isinstance(args, dict):
            raise ValueError("Respuesta malformada de Gemini")
        
        # Validar cliente
        if 'cliente_nombre' in args:
            cliente = args['cliente_nombre']
            if cliente:
                if len(cliente) < 2:
                    raise ValueError("Nombre de cliente invalido")
                if len(cliente) > 100:
                    logger.warning(f"Nombre de cliente truncado: {cliente}")
                    args['cliente_nombre'] = cliente[:100]
        
        # Validar items
        if 'items' in args and isinstance(args['items'], list):
            if len(args['items']) > 50:
                logger.warning("Demasiados items, truncando a 50")
                args['items'] = args['items'][:50]
            
            for item in args['items']:
                cantidad = item.get('cantidad', 0)
                if not isinstance(cantidad, (int, float)) or cantidad <= 0:
                    logger.warning(f"Cantidad invalida corregida: {cantidad} -> 1")
                    item['cantidad'] = 1
                elif cantidad > 10000:
                    logger.warning(f"Cantidad sospechosa: {cantidad}")
                    item['cantidad'] = 10000
                
                precio = item.get('precio')
                if precio is not None:
                    if not isinstance(precio, (int, float)) or precio < 0:
                        logger.warning(f"Precio invalido ignorado: {precio}")
                        item['precio'] = None
                    elif precio > 100000:
                        logger.warning(f"Precio sospechoso: {precio}")
                        item['precio'] = None
                
                prod_nombre = item.get('producto_nombre', '')
                if len(prod_nombre) < 2 or len(prod_nombre) > 200:
                    raise ValueError(f"Nombre de producto invalido: {prod_nombre}")
        
        # Validar pagos
        if 'pagos' in args and isinstance(args['pagos'], list):
            for pago in args['pagos']:
                monto = pago.get('monto', 0)
                if not isinstance(monto, (int, float)) or monto < 0:
                    raise ValueError(f"Monto de pago negativo o invalido: {monto}")
        
        # Validar gasto asociado o standalone
        if 'gasto_asociado' in args and args['gasto_asociado']:
            gasto = args['gasto_asociado']
            if gasto.get('monto', 0) < 0:
                raise ValueError("Monto de gasto negativo")
        
        if 'monto' in args and 'descripcion' in args: # Para registrar_gasto o registrar_pago
            monto = args.get('monto')
            if isinstance(monto, (int, float)) and monto < 0:
                raise ValueError("Monto negativo no permitido")

        if 'monto_depositado' in args: # Para registrar_deposito
            monto_dep = args.get('monto_depositado')
            if isinstance(monto_dep, (int, float)) and monto_dep < 0:
                raise ValueError("Monto depositado negativo no permitido")
                
        if 'cantidad_a_producir' in args: # Para registrar_produccion
            cant = args.get('cantidad_a_producir')
            if isinstance(cant, (int, float)) and cant <= 0:
                raise ValueError("La cantidad a producir debe ser mayor a cero")
        
        return args
    
    def _build_system_prompt(self):
        """
        Construye un system prompt optimizado con estructura clara y ejemplos.
        """
        fecha_actual = get_peru_now().strftime('%Y-%m-%d %H:%M')
        
        return f"""Eres el asistente comercial inteligente de Manngo, un sistema de gestion de ventas de carbon/briquetas.
Tu funcion es extraer intenciones y datos estructurados a partir de comandos de usuarios.
Deberas seleccionar la funcion/herramienta correcta en base al texto del usuario.

=== CONTEXTO ACTUAL ===
Fecha/Hora: {fecha_actual}
Ubicacion: Peru (moneda: Soles S/)

=== REGLAS DE SELECCION DE HERRAMIENTAS ===

1. Ventas (interpretar_operacion):
   * Se activa cuando el usuario menciona vender o despachar productos a un cliente.
   * REGLA DE KILOGRAMOS: Este negocio vende productos diferenciados por KILOGRAMOS.
     Presentaciones comunes: 3kg, 4kg, 5kg, 10kg, 20kg, 30kg.
     Mapeo: 'saco de 20' -> '20kg', 'bolsa de diez' -> '10kg', 'saco grande' -> '30kg', 'saco chico' -> '10kg'.
   * Ejemplo: "vendi 3 sacos de 20 a juan perez pago completo"
   * Ejemplo: "2 bolsas de 10 para maria al credito"

2. Gastos (registrar_gasto):
   * Se activa cuando se menciona un gasto, pago a ayudantes o flete independiente de una venta.
   * Categorias validas: logistica, personal, insumos, otros.
   * Ejemplo: "gaste 50 soles de flete"
   * Ejemplo: "pagado 100 soles de combustible categoria logistica"
   * Ejemplo: "le di 30 soles de almuerzo al ayudante" (Categoria: personal)

3. Pagos / Abonos (registrar_pago):
   * Se activa cuando un cliente realiza un abono o paga una deuda pendiente.
   * Metodos: efectivo, yape_plin, transferencia, tarjeta, deposito, otro.
   * Ejemplo: "juan perez pago 200 soles por yape"
   * Ejemplo: "abono de maria de 150 soles en efectivo"

4. Depositos Bancarios (registrar_deposito):
   * Se activa cuando se realiza el deposito bancario del efectivo que estaba en gerencia/caja.
   * Ejemplo: "depositados 500 soles al banco con referencia 74829"
   * Ejemplo: "se deposito 1000 soles del efectivo de ayer, op 12345"

5. Produccion (registrar_produccion):
   * Se activa cuando se reporta la produccion de briquetas o ensacado de productos terminados.
   * Ejemplo: "se produjeron 50 sacos de briquetas de 5kg"
   * Ejemplo: "ensacamos 20 sacos de 30kg"

=== RESTRICCIONES CRITICAS ===
* NUNCA inventes informacion que no este en el comando.
* Si el texto coincide con una produccion, selecciona registrar_produccion.
* Si coincide con un abono a deuda, selecciona registrar_pago.
* Si coincide con depositar dinero en el banco, selecciona registrar_deposito.
* Si es una venta compleja (productos + cliente), selecciona interpretar_operacion.
* Tu output DEBE ser SOLO el function call correspondiente, sin texto adicional."""

    def process_command(self, text):
        """
        Procesa un comando de texto usando Gemini con prompt optimizado.
        """
        try:
            # 1. Sanitizacion robusta
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
                "message": "No entendi el comando. Intenta reformular."
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
                        
                        # 6. Validacion de output
                        try:
                            args_dict = self._validate_output(args_dict)
                        except ValueError as ve:
                            logger.error(f"Validation error: {ve}")
                            return {
                                "action": "error",
                                "message": f"Error de validacion: {str(ve)}"
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
                "message": f"Error interno al procesar comando de voz: {str(e)}"
            }

# Instancia global
gemini_service = GeminiService()
