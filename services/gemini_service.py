import google.generativeai as genai
import os
import json
import logging
import re
import time
from google.api_core.exceptions import GoogleAPICallError, RetryError
from utils.date_utils import get_peru_now

logger = logging.getLogger(__name__)


def _to_native(obj):
    """Convierte recursivamente los args de Gemini (proto Struct) a tipos nativos de Python."""
    if hasattr(obj, "items"):
        return {k: _to_native(v) for k, v in obj.items()}
    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        return [_to_native(v) for v in obj]
    return obj


class GeminiService:
    MODEL_NAME = "gemini-flash-lite-latest"
    MAX_INPUT_LENGTH = 500
    REQUEST_TIMEOUT_S = 20          # evita requests colgados indefinidamente
    MAX_HISTORY_TURNS = 12          # ~6 intercambios user/model, evita crecimiento ilimitado de tokens
    RETRY_DELAY_S = 1               # espera antes de reintentar ante fallo transitorio

    # Patrones de jailbreak precompilados una sola vez (no en cada llamada)
    _JAILBREAK_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
        r"(ignora|olvida|borra).{0,15}(instrucciones|reglas|sistema)",
        r"(actua|comportate|responde).{0,15}como.{0,15}(si|un)",
        r"tu.{0,10}(nuevo|verdadero).{0,10}(rol|trabajo|proposito)",
        r"(desactiva|deshabilita|apaga).{0,15}(filtros|restricciones)",
        r"(ignore|forget|disregard).{0,15}(previous|above|instructions)",
        r"(act|pretend|behave).{0,15}as.{0,15}(if|a|an)",
        r"your.{0,10}(new|real).{0,10}(role|purpose|job)",
        r"(disable|turn off).{0,15}(safety|filters)",
        r"(system|admin|root).{0,10}(prompt|instruction|mode)",
        r"(ahora|now).{0,15}(eres|you are).{0,15}(un|a)",
        r"[\[\]<>{}].*instruc",
        r"(print|echo|show).{0,10}(system|prompt|instruction)",
    ]]

    # Declaraciones de funciones: definidas una única vez a nivel de clase
    # (antes se reconstruía este diccionario grande en cada instanciación).
    TOOLS = [
        {
            "function_declarations": [
                {
                    "name": "interpretar_operacion",
                    "description": "Interpreta una venta comercial compleja que puede incluir cliente, items, pagos y gastos asociados.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "cliente_nombre": {"type": "STRING", "description": "Nombre del cliente para la venta."},
                            "items": {
                                "type": "ARRAY",
                                "description": "Lista de productos vendidos.",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "producto_nombre": {"type": "STRING", "description": "Nombre del producto o presentacion."},
                                        "cantidad": {"type": "INTEGER", "description": "Cantidad vendida."},
                                        "precio": {"type": "NUMBER", "description": "Precio unitario explicito si se menciona (ej: 'a 50 soles'). Si no, null."}
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
                                        "monto": {"type": "NUMBER", "description": "Monto del pago."},
                                        "metodo_pago": {"type": "STRING", "description": "Metodo de pago.", "enum": ["efectivo", "yape_plin", "transferencia", "tarjeta", "deposito", "otro"]},
                                        "es_deposito": {"type": "BOOLEAN", "description": "True si es deposito directo."}
                                    },
                                    "required": ["monto"]
                                }
                            },
                            "condicion_pago": {
                                "type": "STRING",
                                "description": "Indica si el pago es total (completo), al credito (credito) o parcial (parcial). Si el usuario NO menciona que pagó, abonó o un método de pago, DEBE ser 'credito'. No asumas pago completo por defecto.",
                                "enum": ["completo", "credito", "parcial"]
                            },
                            "porcentaje_abono": {"type": "INTEGER", "description": "Porcentaje del total a pagar (ej: 50 para 'la mitad')."},
                            "fecha": {"type": "STRING", "description": "Fecha personalizada de la venta o pedido en formato YYYY-MM-DD. Resolver a partir de fechas relativas como 'ayer', 'hace 2 días', 'el lunes', etc. en base a la fecha actual."},
                            "estado": {
                                "type": "STRING",
                                "description": "Estado del pedido o venta. Si el usuario menciona que es un pedido, encargo o solicitud pendiente, usar 'pedido'. De lo contrario, usar 'completado'.",
                                "enum": ["pedido", "completado"]
                            },
                            "gasto_asociado": {
                                "type": "OBJECT",
                                "description": "Gasto operativo mencionado.",
                                "properties": {
                                    "descripcion": {"type": "STRING"},
                                    "monto": {"type": "NUMBER"},
                                    "categoria": {"type": "STRING", "enum": ["logistica", "personal", "insumos", "otros"]}
                                },
                                "required": ["descripcion", "monto", "categoria"]
                            }
                        },
                        "required": ["cliente_nombre", "items"]
                    }
                },
                {
                    "name": "registrar_gasto",
                    "description": "Registra uno o multiples gastos operativos independientes del negocio.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "gastos": {
                                "type": "ARRAY",
                                "description": "Lista de gastos a registrar.",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "descripcion": {"type": "STRING", "description": "Descripcion o concepto del gasto."},
                                        "monto": {"type": "NUMBER", "description": "Monto total del gasto."},
                                        "categoria": {"type": "STRING", "description": "Categoria del gasto.", "enum": ["logistica", "personal", "insumos", "otros"]}
                                    },
                                    "required": ["descripcion", "monto", "categoria"]
                                }
                            },
                            "fecha": {"type": "STRING", "description": "Fecha personalizada del gasto en formato YYYY-MM-DD. Resolver a partir de fechas relativas (ej. 'ayer', 'el lunes') usando la fecha actual."}
                        },
                        "required": ["gastos"]
                    }
                },
                {
                    "name": "registrar_pago",
                    "description": "Registra un abono/pago de deuda para una venta existente o de un cliente.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "cliente_nombre": {"type": "STRING", "description": "Nombre del cliente que abona o paga."},
                            "monto": {"type": "NUMBER", "description": "Monto del abono."},
                            "metodo_pago": {"type": "STRING", "description": "Metodo de pago utilizado.", "enum": ["efectivo", "yape_plin", "transferencia", "tarjeta", "deposito", "otro"]},
                            "referencia": {"type": "STRING", "description": "Codigo o numero de referencia del comprobante."},
                            "fecha": {"type": "STRING", "description": "Fecha personalizada del pago/abono en formato YYYY-MM-DD. Resolver a partir de fechas relativas (ej. 'ayer', 'el lunes') usando la fecha actual."}
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
                            "monto_depositado": {"type": "NUMBER", "description": "Monto depositado en la cuenta corporativa."},
                            "referencia": {"type": "STRING", "description": "Numero de operacion del deposito."}
                        },
                        "required": ["monto_depositado"]
                    }
                },
                {
                    "name": "registrar_produccion",
                    "description": "Registra la produccion de uno o multiples productos terminados (anadiendo inventario final).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "producciones": {
                                "type": "ARRAY",
                                "description": "Lista de productos a producir.",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "producto_nombre": {"type": "STRING", "description": "Nombre de la presentacion final producida (ej: 'saco 20kg', 'briquetas 5kg')."},
                                        "cantidad_a_producir": {"type": "NUMBER", "description": "Cantidad de unidades producidas."}
                                    },
                                    "required": ["producto_nombre", "cantidad_a_producir"]
                                }
                            }
                        },
                        "required": ["producciones"]
                    }
                },
                {
                    "name": "registrar_compra_insumos",
                    "description": "Registra la compra o ingreso de insumos/productos al inventario, asociando un gasto operativo.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "items": {
                                "type": "ARRAY",
                                "description": "Lista de insumos/productos comprados.",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "producto_nombre": {"type": "STRING", "description": "Nombre del insumo o producto (ej: 'sacos 20kg', 'hilo blanco')."},
                                        "cantidad": {"type": "NUMBER", "description": "Cantidad comprada o ingresada."},
                                        "monto_compra": {"type": "NUMBER", "description": "Monto total pagado por esta compra (costo del gasto). Si no se menciona, null."}
                                    },
                                    "required": ["producto_nombre", "cantidad"]
                                }
                            }
                        },
                        "required": ["items"]
                    }
                },
                {
                    "name": "solicitar_guia_remision",
                    "description": "Prepara un borrador de guia de remision remitente (GRE) para traslados o envios de productos.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "items": {
                                "type": "ARRAY",
                                "description": "Lista de productos a trasladar.",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "producto_nombre": {"type": "STRING", "description": "Nombre de la presentacion o producto."},
                                        "cantidad": {"type": "NUMBER", "description": "Cantidad de unidades."}
                                    },
                                    "required": ["producto_nombre", "cantidad"]
                                }
                            },
                            "destinatario_documento": {"type": "STRING", "description": "RUC (11 digitos) o DNI (8 digitos) del cliente o destinatario."},
                            "motivo_traslado": {"type": "STRING", "description": "Motivo del traslado. Por defecto 'venta' si no se especifica. Opciones comunes: 'venta', 'traslado', 'compra', 'devolucion'."},
                            "placa_vehiculo": {"type": "STRING", "description": "Placa del vehiculo de transporte (ej: ABC-123). Si no se menciona, null."},
                            "conductor_documento": {"type": "STRING", "description": "DNI del conductor (8 digitos). Si no se menciona, null."}
                        },
                        "required": ["items", "destinatario_documento"]
                    }
                },
                {
                    "name": "registrar_cliente",
                    "description": "Crea o registra un nuevo cliente en el sistema.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "nombre": {"type": "STRING", "description": "Nombre o razón social del cliente."},
                            "telefono": {"type": "STRING", "description": "Número de teléfono/celular de 9 dígitos del cliente."},
                            "documento": {"type": "STRING", "description": "Número de RUC o DNI del cliente (opcional)."},
                            "direccion": {"type": "STRING", "description": "Dirección de entrega o residencia del cliente (opcional)."}
                        },
                        "required": ["nombre", "telefono"]
                    }
                },
                {
                    "name": "registrar_ventas_lote",
                    "description": "Registra múltiples ventas acumuladas a la vez en un lote.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "fecha": {"type": "STRING", "description": "Fecha del lote de ventas en formato YYYY-MM-DD. Si no se menciona fecha relativa o absoluta, dejar null o no enviarla."},
                            "ventas": {
                                "type": "ARRAY",
                                "description": "Lista de ventas a registrar en este lote.",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "cliente_nombre": {"type": "STRING", "description": "Nombre del cliente."},
                                        "items": {
                                            "type": "ARRAY",
                                            "description": "Lista de productos/presentaciones vendidas al cliente.",
                                            "items": {
                                                "type": "OBJECT",
                                                "properties": {
                                                    "producto_nombre": {"type": "STRING", "description": "Nombre de la presentación (ej: '20kg', '10kg', '5kg')."},
                                                    "cantidad": {"type": "NUMBER", "description": "Cantidad vendida."}
                                                },
                                                "required": ["producto_nombre", "cantidad"]
                                            }
                                        }
                                    },
                                    "required": ["cliente_nombre", "items"]
                                }
                            }
                        },
                        "required": ["ventas"]
                    }
                }
            ]
        }
    ]

    # System prompt ESTATICO (sin la fecha) para permitir el cacheo implícito
    # de contexto de Gemini: como no cambia entre llamadas, el modelo puede
    # reutilizar el prefijo (system_instruction + tools) y facturar menos
    # tokens de entrada. La fecha, que sí cambia, se inyecta en el mensaje
    # del usuario en cada turno (ver process_command).
    SYSTEM_PROMPT = """Comercial Manngo (carbón/briquetas). Extrae intenciones y datos estructurados.
Moneda: Soles (S/). Perú.

Reglas:
- Si mencionan fechas relativas ('ayer', 'el lunes', 'hace 3 días', etc.) o absolutas ('15 de julio'), resuélvelas a formato YYYY-MM-DD usando la fecha de referencia dada al inicio del mensaje del usuario, y envíalas en el parámetro 'fecha'.
- Si NO se menciona explícitamente haber pagado, abonado, cobrado o un método de pago, la condición de pago DEBE ser 'credito' (no asumas 'completo').

Mapeo de Herramientas:
1. interpretar_operacion (Venta/Pedido/Despacho): "vendi 3 sacos de 20 a juan perez" o "pedido de 10 sacos de 5kg para maria"
   * Regla de Kg: Mapear 'saco de 20' -> '20kg', 'bolsa de diez' -> '10kg', 'saco grande' -> '30kg', 'saco chico' -> '10kg'.
2. registrar_gasto (Gastos independientes): "ayudante 30 soles" o "combustible 100 soles categoria logistica"
3. registrar_pago (Abono de deuda): "juan perez pago 200 soles por yape"
4. registrar_deposito (Depósito de caja al banco): "depositados 500 soles al banco ref 74829"
5. registrar_produccion (Producción/Ensacado): "hice 60 sacos de 20kg y 100 de 5kg"
6. registrar_compra_insumos (Compra/Ingreso de insumos): "compre 500 sacos de 20kg y 30 hilos"
7. solicitar_guia_remision (Guía de remisión/traslado SUNAT): "guia de 20 sacos de 20kg al RUC 20601234567"
8. registrar_cliente (Crear o registrar cliente): "crear cliente Juan Perez celular 987654321"
9. registrar_ventas_lote (Registrar múltiples ventas dictadas juntas): "Ventas 24/06/2026: Cliente A 4 sacos de 20kg, Cliente B 1 saco de 20kg"

Restricción: NUNCA inventes información. Tu output DEBE ser únicamente el function call correspondiente sin texto adicional."""

    def __init__(self):
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("GOOGLE_API_KEY no configurada.")
        else:
            genai.configure(api_key=api_key)

        # El modelo se construye una sola vez: system_instruction y tools son
        # estáticos, así que no hay razón para recrearlo en cada request.
        self._model = genai.GenerativeModel(
            self.MODEL_NAME,
            tools=self.TOOLS,
            system_instruction=self.SYSTEM_PROMPT,
        )

    def _sanitize_input(self, text):
        """Sanitizacion robusta contra prompt injection y entradas malformadas."""
        if not text or not isinstance(text, str):
            raise ValueError("Input invalido")

        if len(text) > self.MAX_INPUT_LENGTH:
            logger.warning(f"Input truncado: {len(text)} -> {self.MAX_INPUT_LENGTH}")
            text = text[:self.MAX_INPUT_LENGTH]

        text = " ".join(text.split())

        for pattern in self._JAILBREAK_PATTERNS:
            if pattern.search(text):
                logger.warning(f"Prompt injection detectado: {text[:100]}")
                raise ValueError("Comando rechazado por seguridad. Evita instrucciones al sistema.")

        if len(re.sub(r'[^a-zA-Z0-9]', '', text)) < 3:
            raise ValueError("Comando demasiado corto o invalido")

        return text

    def _validate_output(self, action, args):
        """Validacion de la respuesta estructurada devuelta por Gemini."""
        if not isinstance(args, dict):
            raise ValueError("Respuesta malformada de Gemini")

        if action == 'registrar_cliente':
            telefono = args.get('telefono')
            if telefono:
                digits = re.sub(r'\D', '', str(telefono))
                if len(digits) != 9:
                    raise ValueError("El teléfono del cliente debe tener exactamente 9 dígitos")
                args['telefono'] = digits
            nombre = args.get('nombre')
            if not nombre or len(nombre) < 2:
                raise ValueError("El nombre del cliente debe tener al menos 2 caracteres")

        if 'cliente_nombre' in args:
            cliente = args['cliente_nombre']
            if cliente:
                if len(cliente) < 2:
                    raise ValueError("Nombre de cliente invalido")
                if len(cliente) > 100:
                    logger.warning(f"Nombre de cliente truncado: {cliente}")
                    args['cliente_nombre'] = cliente[:100]

        for field in ('items', 'producciones', 'gastos'):
            values = args.get(field)
            if not isinstance(values, list):
                continue
            if len(values) > 50:
                logger.warning(f"Demasiados {field}, truncando a 50")
                values = args[field] = values[:50]

            for item in values:
                if field == 'gastos':
                    monto = item.get('monto', 0)
                    if not isinstance(monto, (int, float)) or monto < 0:
                        raise ValueError(f"Monto de gasto invalido: {monto}")
                    desc = item.get('descripcion', '')
                    if not desc or len(desc) < 2:
                        raise ValueError("Descripcion de gasto invalida")

                elif field == 'producciones':
                    cant = item.get('cantidad_a_producir', 0)
                    if not isinstance(cant, (int, float)) or cant <= 0:
                        raise ValueError(f"Cantidad a producir invalida: {cant}")
                    if len(item.get('producto_nombre', '')) < 2:
                        raise ValueError("Nombre de producto invalido")

                elif field == 'items':
                    cantidad = item.get('cantidad', 0)
                    if not isinstance(cantidad, (int, float)) or cantidad <= 0:
                        logger.warning(f"Cantidad invalida corregida: {cantidad} -> 1")
                        item['cantidad'] = 1
                    elif cantidad > 10000:
                        logger.warning(f"Cantidad sospechosa: {cantidad}")
                        item['cantidad'] = 10000

                    precio = item.get('precio')
                    if precio is not None and (not isinstance(precio, (int, float)) or precio < 0 or precio > 100000):
                        logger.warning(f"Precio invalido ignorado: {precio}")
                        item['precio'] = None

                    monto_compra = item.get('monto_compra')
                    if monto_compra is not None and (not isinstance(monto_compra, (int, float)) or monto_compra < 0):
                        item['monto_compra'] = None

                    prod_nombre = item.get('producto_nombre', '')
                    if len(prod_nombre) < 2 or len(prod_nombre) > 200:
                        raise ValueError(f"Nombre de producto invalido: {prod_nombre}")

        if action == 'solicitar_guia_remision' and isinstance(args.get('items'), list):
            for item in args['items']:
                cant = item.get('cantidad', 0)
                if not isinstance(cant, (int, float)) or cant <= 0:
                    raise ValueError("La cantidad a trasladar debe ser mayor a cero")
                prod_nombre = item.get('producto_nombre', '')
                if len(prod_nombre) < 2 or len(prod_nombre) > 200:
                    raise ValueError(f"Nombre de producto invalido para la guia: {prod_nombre}")

        if isinstance(args.get('pagos'), list):
            for pago in args['pagos']:
                monto = pago.get('monto', 0)
                if not isinstance(monto, (int, float)) or monto < 0:
                    raise ValueError(f"Monto de pago negativo o invalido: {monto}")

        gasto_asociado = args.get('gasto_asociado')
        if gasto_asociado and gasto_asociado.get('monto', 0) < 0:
            raise ValueError("Monto de gasto negativo")

        monto_dep = args.get('monto_depositado')
        if isinstance(monto_dep, (int, float)) and monto_dep < 0:
            raise ValueError("Monto depositado negativo no permitido")

        return args

    def _generate_with_retry(self, contents):
        """Llama a Gemini con timeout y un reintento ante fallos transitorios
        (red, timeout, 5xx de la API). Errores no transitorios (validación,
        auth, etc.) se propagan de inmediato sin reintentar."""
        try:
            return self._model.generate_content(
                contents,
                request_options={"timeout": self.REQUEST_TIMEOUT_S},
            )
        except (GoogleAPICallError, RetryError, TimeoutError, ConnectionError) as e:
            logger.warning(f"Fallo transitorio en Gemini, reintentando: {e}")
            time.sleep(self.RETRY_DELAY_S)
            return self._model.generate_content(
                contents,
                request_options={"timeout": self.REQUEST_TIMEOUT_S},
            )

    def process_command(self, text, history=None):
        """Procesa un comando de texto usando Gemini, con historial de conversación opcional."""
        try:
            clean_text = self._sanitize_input(text)

            contents = []
            if history and isinstance(history, list):
                # Ventana acotada: sin límite, el historial crece indefinidamente
                # y cada request manda más tokens (costo y latencia crecientes).
                for h in history[-self.MAX_HISTORY_TURNS:]:
                    role, parts = h.get("role"), h.get("parts")
                    if role in ("user", "model") and parts:
                        contents.append({
                            "role": role,
                            "parts": parts if isinstance(parts, list) else [parts]
                        })

            # La fecha se inyecta aquí (no en el system prompt) para mantener
            # el system prompt estático y aprovechar el cacheo de contexto.
            fecha_actual = get_peru_now().strftime('%Y-%m-%d %H:%M')
            contents.append({
                "role": "user",
                "parts": [f"[Fecha/hora actual: {fecha_actual}] {clean_text}"]
            })

            response = self._generate_with_retry(contents)

            if not response.candidates:
                default_msg = "No entendí el comando. Intenta reformular."
                return {
                    "action": "none",
                    "message": default_msg,
                    "history_entry": {"user": clean_text, "model": default_msg}
                }

            parts = response.candidates[0].content.parts
            text_response = next((p.text for p in parts if getattr(p, 'text', None)), None)
            fn_part = next((p for p in parts if getattr(p, 'function_call', None)), None)

            if fn_part:
                fn_call = fn_part.function_call
                args_dict = _to_native(fn_call.args)

                try:
                    args_dict = self._validate_output(fn_call.name, args_dict)
                except ValueError as ve:
                    logger.error(f"Validation error: {ve}")
                    msg = f"Error de validacion: {ve}"
                    return {
                        "action": "error",
                        "message": msg,
                        "history_entry": {"user": clean_text, "model": msg}
                    }

                model_summary = text_response or (
                    f"Solicitó ejecutar la función {fn_call.name} con los datos: "
                    f"{json.dumps(args_dict, ensure_ascii=False)}"
                )
                return {
                    "action": fn_call.name,
                    "args": args_dict,
                    "message": text_response or f"Procesando: {fn_call.name}",
                    "history_entry": {"user": clean_text, "model": model_summary}
                }

            default_msg = text_response or "No entendí el comando. Intenta reformular."
            return {
                "action": "none",
                "message": default_msg,
                "history_entry": {"user": clean_text, "model": default_msg}
            }

        except ValueError as ve:
            logger.warning(f"Security/validation block: {ve}")
            return {
                "action": "security_block",
                "message": str(ve),
                "history_entry": {"user": text, "model": f"Bloqueado por seguridad: {ve}"}
            }
        except Exception as e:
            logger.error(f"Error en GeminiService: {e}", exc_info=True)
            return {
                "action": "error",
                "message": f"Error interno al procesar comando: {e}",
                "history_entry": {"user": text, "model": f"Error interno: {e}"}
            }


# Instancia global
gemini_service = GeminiService()