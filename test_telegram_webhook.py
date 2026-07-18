import os
import json
import sys
from decimal import Decimal
from unittest.mock import patch, MagicMock

# Configurar variables de entorno ficticias si no existen
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test_secret_123")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_bot_token_123")
os.environ.setdefault("GOOGLE_API_KEY", "dummy_key")

from app import app
from extensions import db
from models import Users, Cliente, PresentacionProducto, Inventario, Lote, Venta, Pago, Gasto, Movimiento, Receta, ComponenteReceta, Almacen

def run_tests():
    print("=== Iniciando pruebas de integracion del bot de Telegram ===")

    # Crear cliente de pruebas de Flask
    client = app.test_client()

    with app.app_context():
        # Asegurar la columna es_planta en la base de datos local de pruebas
        try:
            db.session.execute(db.text("ALTER TABLE almacenes ADD COLUMN IF NOT EXISTS es_planta BOOLEAN DEFAULT FALSE NOT NULL"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Nota: No se pudo agregar la columna es_planta: {e}")

        # Asegurar la columna telegram_history en la base de datos local de pruebas
        try:
            db.session.execute(db.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_history JSONB"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            try:
                db.session.execute(db.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_history JSON"))
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Asegurar la columna telegram_linking_code en la base de datos local de pruebas
        try:
            db.session.execute(db.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_linking_code VARCHAR(10) UNIQUE"))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Asegurar la columna telegram_linking_expires en la base de datos local de pruebas
        try:
            db.session.execute(db.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_linking_expires TIMESTAMP WITH TIME ZONE"))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # 1. Asegurar que exista al menos un usuario en la BD para probar
        user = Users.query.first()
        if not user:
            print("Error: No se encontro ningun usuario en la base de datos para realizar la prueba.")
            sys.exit(1)

        # Respaldar datos originales del usuario
        orig_chat_id = user.telegram_chat_id
        orig_context = user.telegram_context
        orig_almacen_id = user.almacen_id

        # Configurar Chat ID de pruebas y asegurar un almacén asociado
        TEST_CHAT_ID = 987654321
        user.telegram_chat_id = TEST_CHAT_ID
        
        # Asegurar un almacén asociado y configurarlo como Planta
        almacen = Almacen.query.get(user.almacen_id) if user.almacen_id else None
        if not almacen:
            almacen = Almacen.query.first()
            if not almacen:
                almacen = Almacen(nombre="Planta de Produccion", direccion="Calle Falsa 123", ciudad="Lima", es_planta=True)
                db.session.add(almacen)
                db.session.flush()
            user.almacen_id = almacen.id
            
        # Asegurar que solo este almacén sea Planta en la base de datos de pruebas
        Almacen.query.update({Almacen.es_planta: False})
        almacen.es_planta = True
        almacen.nombre = "Planta de Produccion"
        db.session.flush()
        print(f"Almacen asignado al usuario de pruebas (como Planta): {almacen.nombre} (ID: {almacen.id})")

        # Asegurar cliente genérico
        cliente_gen = Cliente.query.filter(Cliente.nombre.ilike("%genérico%")).first()
        if not cliente_gen:
            cliente_gen = Cliente(nombre="Cliente Generico", telefono="999999999", direccion="Lima", ciudad="Lima")
            db.session.add(cliente_gen)
            db.session.flush()

        # Asegurar producto y presentación para pruebas de stock
        presentacion = PresentacionProducto.query.filter_by(tipo="procesado").first()
        if not presentacion:
            # Crear producto y presentación
            from models import Producto
            prod = Producto(nombre="Carbon de Prueba", descripcion="Prueba", precio_compra=Decimal("20.00"))
            db.session.add(prod)
            db.session.flush()
            presentacion = PresentacionProducto(producto_id=prod.id, nombre="20kg", capacidad_kg=Decimal("20.0"), tipo="procesado", precio_venta=Decimal("50.00"))
            db.session.add(presentacion)
            db.session.flush()

        # Asegurar lote e inventario con stock
        inventario = Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=presentacion.id).first()
        if not inventario or inventario.cantidad < 10:
            lote = Lote.query.filter_by(es_produccion=False).first()
            if not lote:
                lote = Lote(producto_id=presentacion.producto_id, codigo_lote="LOTE-TEST", cantidad_disponible_kg=Decimal("1000.0"), es_produccion=False)
                db.session.add(lote)
                db.session.flush()
            
            if not inventario:
                inventario = Inventario(presentacion_id=presentacion.id, almacen_id=user.almacen_id, lote_id=lote.id, cantidad=Decimal("100"))
                db.session.add(inventario)
            else:
                inventario.cantidad = Decimal("100")
            db.session.flush()

        # Crear una receta básica para pruebas de producción si no existe, o rellenar de stock los insumos de la existente
        receta = Receta.query.filter_by(presentacion_id=presentacion.id).first()
        if not receta:
            prod_mp = presentacion.producto
            pres_mp = PresentacionProducto.query.filter_by(producto_id=prod_mp.id, tipo="insumo").first()
            if not pres_mp:
                pres_mp = PresentacionProducto(producto_id=prod_mp.id, nombre="Insumo Prueba", capacidad_kg=Decimal("1.0"), tipo="insumo", precio_venta=Decimal("10.00"))
                db.session.add(pres_mp)
                db.session.flush()
            
            receta = Receta(presentacion_id=presentacion.id, nombre=f"Receta {presentacion.nombre}", descripcion="Receta de pruebas")
            db.session.add(receta)
            db.session.flush()
            componente = ComponenteReceta(receta_id=receta.id, componente_presentacion_id=pres_mp.id, cantidad_necesaria=Decimal("1.0"), tipo_consumo="insumo")
            db.session.add(componente)
            db.session.flush()

        # Rellenar stock de todos los componentes de la receta
        for componente in receta.componentes:
            if componente.tipo_consumo == "insumo":
                inv_insumo = Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=componente.componente_presentacion_id).first()
                if not inv_insumo:
                    inv_insumo = Inventario(presentacion_id=componente.componente_presentacion_id, almacen_id=user.almacen_id, lote_id=None, cantidad=Decimal("1000"))
                    db.session.add(inv_insumo)
                else:
                    inv_insumo.cantidad = Decimal("1000")
            elif componente.tipo_consumo == "materia_prima":
                lote_mp = Lote.query.filter(Lote.producto_id == componente.componente_presentacion.producto_id, Lote.cantidad_disponible_kg > 0).first()
                if not lote_mp:
                    lote_mp = Lote(producto_id=componente.componente_presentacion.producto_id, codigo_lote=f"MP-{componente.id}", cantidad_disponible_kg=Decimal("10000"), es_produccion=False)
                    db.session.add(lote_mp)
                else:
                    lote_mp.cantidad_disponible_kg = Decimal("10000")

        db.session.commit()

        print(f"Usuario de pruebas: {user.username} (Telegram ID: {TEST_CHAT_ID})")
        print(f"Presentacion de prueba: {presentacion.nombre} (ID: {presentacion.id})")

        # Mock de requests.post para evitar llamadas reales a Telegram y Gemini
        # Pero llamaremos a Gemini realmente si el usuario tiene API_KEY configurada, 
        # si no, mockearemos la respuesta de GeminiService.process_command.
        real_gemini_key = os.environ.get("GOOGLE_API_KEY")
        use_real_gemini = real_gemini_key and "dummy" not in real_gemini_key

        try:
            # ----------------------------------------------------
            # PRUEBA 1: Flujo de Venta
            # ----------------------------------------------------
            print("\nTest 1: Flujo de Venta...")
            
            # Simular entrada de texto de venta
            webhook_url = f"/telegram/webhook/{os.environ.get('TELEGRAM_WEBHOOK_SECRET')}"
            
            # Mockear la llamada de envío a Telegram
            with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                 patch("services.telegram_service.TelegramService.edit_message") as mock_edit:
                
                # Payload simulado de Telegram para mensaje de texto
                message_payload = {
                    "update_id": 10001,
                    "message": {
                        "message_id": 999,
                        "from": {"id": TEST_CHAT_ID, "is_bot": False, "first_name": "Test"},
                        "chat": {"id": TEST_CHAT_ID, "type": "private"},
                        "date": 1441645532,
                        "text": f"vendi 3 sacos de {presentacion.nombre} a {cliente_gen.nombre} pago completo"
                    }
                }
                
                # Si no usamos Gemini real, mockeamos la interpretación
                if not use_real_gemini:
                    with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                        mock_gemini.return_value = {
                            "action": "interpretar_operacion",
                            "args": {
                                "cliente_nombre": cliente_gen.nombre,
                                "items": [{"producto_nombre": presentacion.nombre, "cantidad": 3}],
                                "condicion_pago": "completo"
                            }
                        }
                        res = client.post(webhook_url, json=message_payload)
                else:
                    res = client.post(webhook_url, json=message_payload)

                assert res.status_code == 200, f"Error en webhook post: {res.data}"
                assert mock_send.called, "No se envio mensaje de confirmacion a Telegram"
                
                # Verificar que el contexto se guardó correctamente en el usuario
                db.session.refresh(user)
                assert user.telegram_context is not None, "El contexto de la venta no se guardo"
                assert user.telegram_context["action"] == "venta"
                print("Ok Test 1.1: Interpretacion de Venta exitosa (Contexto guardado).")

                # Simular pulsación de botón "Confirmar Venta"
                callback_payload = {
                    "update_id": 10002,
                    "callback_query": {
                        "id": "cb_1",
                        "from": {"id": TEST_CHAT_ID},
                        "message": {
                            "message_id": 999,
                            "chat": {"id": TEST_CHAT_ID}
                        },
                        "data": "confirm:venta"
                    }
                }
                
                ventas_count_before = Venta.query.count()
                res_cb = client.post(webhook_url, json=callback_payload)
                
                assert res_cb.status_code == 200
                ventas_count_after = Venta.query.count()
                assert ventas_count_after == ventas_count_before + 1, "La venta no fue guardada en base de datos"
                assert mock_edit.called, "No se edito el mensaje de Telegram para mostrar exito"
                
                # Verificar que el contexto se limpió
                db.session.refresh(user)
                assert user.telegram_context is None, "El contexto no se limpio tras la confirmacion"
                print("Ok Test 1.2: Confirmacion de Venta exitosa (Venta registrada en BD).")

            # ----------------------------------------------------
            # PRUEBA 2: Flujo de Gasto Batch
            # ----------------------------------------------------
            print("\nTest 2: Flujo de Gasto en Lote (Batch)...")
            with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                 patch("services.telegram_service.TelegramService.edit_message") as mock_edit:
                
                message_payload = {
                    "update_id": 10003,
                    "message": {
                        "message_id": 1000,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": "Agrega los siguientes gastos: Willy pago por mes de junio 2000, 500 soles para el agua"
                    }
                }
                
                if not use_real_gemini:
                    with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                        mock_gemini.return_value = {
                            "action": "registrar_gasto",
                            "args": {
                                "gastos": [
                                    {"descripcion": "Willy pago por mes de junio", "monto": 2000.0, "categoria": "personal"},
                                    {"descripcion": "500 soles para el agua", "monto": 500.0, "categoria": "otros"}
                                ]
                            }
                        }
                        res = client.post(webhook_url, json=message_payload)
                else:
                    res = client.post(webhook_url, json=message_payload)

                assert res.status_code == 200
                db.session.refresh(user)
                assert user.telegram_context["action"] == "gasto"
                assert len(user.telegram_context["gastos"]) == 2
                print("Ok Test 2.1: Interpretacion de Gasto Batch exitosa.")

                callback_payload = {
                    "update_id": 10004,
                    "callback_query": {
                        "id": "cb_2",
                        "from": {"id": TEST_CHAT_ID},
                        "message": {"message_id": 1000, "chat": {"id": TEST_CHAT_ID}},
                        "data": "confirm:gasto"
                    }
                }
                
                gastos_before = Gasto.query.count()
                res_cb = client.post(webhook_url, json=callback_payload)
                assert res_cb.status_code == 200
                assert Gasto.query.count() == gastos_before + 2
                print("Ok Test 2.2: Confirmacion de Gasto Batch exitosa (Gastos registrados en BD).")

            # ----------------------------------------------------
            # PRUEBA 3: Flujo de Producción Multiproducto y LIFO
            # ----------------------------------------------------
            print("\nTest 3: Flujo de Produccion Multiproducto...")
            with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                 patch("services.telegram_service.TelegramService.edit_message") as mock_edit:
                
                message_payload = {
                    "update_id": 10005,
                    "message": {
                        "message_id": 1001,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": f"hice 5 de {presentacion.nombre} y 10 de {presentacion.nombre}"
                    }
                }
                
                if not use_real_gemini:
                    with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                        mock_gemini.return_value = {
                            "action": "registrar_produccion",
                            "args": {
                                "producciones": [
                                    {"producto_nombre": presentacion.nombre, "cantidad_a_producir": 5},
                                    {"producto_nombre": presentacion.nombre, "cantidad_a_producir": 10}
                                ]
                            }
                        }
                        res = client.post(webhook_url, json=message_payload)
                else:
                    res = client.post(webhook_url, json=message_payload)

                assert res.status_code == 200
                db.session.refresh(user)
                assert user.telegram_context["action"] == "produccion"
                assert len(user.telegram_context["producciones"]) == 2
                print("Ok Test 3.1: Interpretacion de Produccion Multiproducto exitosa.")

                # Ejecutar producción
                callback_payload = {
                    "update_id": 10006,
                    "callback_query": {
                        "id": "cb_3",
                        "from": {"id": TEST_CHAT_ID},
                        "message": {"message_id": 1001, "chat": {"id": TEST_CHAT_ID}},
                        "data": "confirm:produccion"
                    }
                }
                
                # Contar stock del producto antes y después de producir (sumado de todos los lotes)
                inv_before = float(db.session.query(db.func.sum(Inventario.cantidad)).filter_by(almacen_id=user.almacen_id, presentacion_id=presentacion.id).scalar() or 0)
                res_cb = client.post(webhook_url, json=callback_payload)
                assert res_cb.status_code == 200
                inv_after = float(db.session.query(db.func.sum(Inventario.cantidad)).filter_by(almacen_id=user.almacen_id, presentacion_id=presentacion.id).scalar() or 0)
                assert inv_after == inv_before + 15, f"No se incremento el stock. Antes: {inv_before}, Despues: {inv_after}"
                print("Ok Test 3.2: Confirmacion de Produccion Multiproducto exitosa (Stock incrementado).")

            # ----------------------------------------------------
            # PRUEBA 4: Flujo de Compra de Insumos (Gasto + Stock)
            # ----------------------------------------------------
            print("\nTest 4: Flujo de Compra de Insumos (Gasto + Stock)...")
            
            # Obtener una presentacion de tipo insumo (crear una si no existe)
            pres_mp = PresentacionProducto.query.filter_by(tipo="insumo").first()
            if not pres_mp:
                from models import Producto
                prod_insumo = Producto.query.filter_by(nombre="Insumo de Prueba Hilos").first()
                if not prod_insumo:
                    prod_insumo = Producto(nombre="Insumo de Prueba Hilos", descripcion="Hilos de embalaje de prueba", precio_compra=Decimal("5.00"))
                    db.session.add(prod_insumo)
                    db.session.flush()
                pres_mp = PresentacionProducto(producto_id=prod_insumo.id, nombre="Hilos de Prueba", capacidad_kg=Decimal("0.1"), tipo="insumo", precio_venta=Decimal("0.00"))
                db.session.add(pres_mp)
                db.session.flush()
            
            # Asegurar inventario inicial a cero para el test si no existe
            inv_insumo_init = Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=pres_mp.id, lote_id=None).first()
            if not inv_insumo_init:
                inv_insumo_init = Inventario(almacen_id=user.almacen_id, presentacion_id=pres_mp.id, lote_id=None, cantidad=Decimal("0"))
                db.session.add(inv_insumo_init)
                db.session.flush()
            
            with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                 patch("services.telegram_service.TelegramService.edit_message") as mock_edit:
                 
                message_payload = {
                    "update_id": 10007,
                    "message": {
                        "message_id": 1002,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": f"Compre 500 sacos de {pres_mp.nombre} a 1000 soles"
                    }
                }
                
                if not use_real_gemini:
                    with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                        mock_gemini.return_value = {
                            "action": "registrar_compra_insumos",
                            "args": {
                                "items": [
                                    {"producto_nombre": pres_mp.nombre, "cantidad": 500.0, "monto_compra": 1000.0}
                                ]
                            }
                        }
                        res = client.post(webhook_url, json=message_payload)
                else:
                    res = client.post(webhook_url, json=message_payload)

                assert res.status_code == 200
                db.session.refresh(user)
                assert user.telegram_context["action"] == "compra_insumos"
                assert len(user.telegram_context["items"]) == 1
                assert user.telegram_context["total_gasto"] == 1000.0
                print("Ok Test 4.1: Interpretacion de Compra de Insumos exitosa.")

                callback_payload = {
                    "update_id": 10008,
                    "callback_query": {
                        "id": "cb_4",
                        "from": {"id": TEST_CHAT_ID},
                        "message": {"message_id": 1002, "chat": {"id": TEST_CHAT_ID}},
                        "data": "confirm:compra_insumos"
                    }
                }
                
                gastos_before = Gasto.query.filter_by(categoria="insumos").count()
                inv_insumo_before = float(Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=pres_mp.id, lote_id=None).first().cantidad)
                
                res_cb = client.post(webhook_url, json=callback_payload)
                assert res_cb.status_code == 200
                
                gastos_after = Gasto.query.filter_by(categoria="insumos").count()
                inv_insumo_after = float(Inventario.query.filter_by(almacen_id=user.almacen_id, presentacion_id=pres_mp.id, lote_id=None).first().cantidad)
                
                assert gastos_after == gastos_before + 1, "No se registro el gasto de insumos"
                assert inv_insumo_after == inv_insumo_before + 500.0, "No se incremento el stock de insumos"
                print("Ok Test 4.2: Confirmacion de Compra de Insumos exitosa (Gasto y Stock registrados).")

                # Test 5: Vinculación Dinámica
                print("\nTest 5: Flujo de Vinculacion Dinamica...")
                from flask_jwt_extended import create_access_token
                jwt_token = create_access_token(identity=user.username)
                headers = {"Authorization": f"Bearer {jwt_token}"}
                
                # 5.1 Generar código
                res_gen = client.post("/telegram/vincular", headers=headers)
                assert res_gen.status_code == 200, "Fallo endpoint /telegram/vincular"
                gen_data = res_gen.get_json()
                linking_code = gen_data["codigo"]
                assert len(linking_code) == 6, "Codigo de vinculacion invalido"
                print("Ok Test 5.1: Generacion de codigo de vinculacion exitosa.")

                # 5.2 Simular envío de código por Telegram para vincular chat_id
                user.telegram_chat_id = None
                db.session.commit()

                link_payload = {
                    "update_id": 10009,
                    "message": {
                        "message_id": 1003,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": f"/start {linking_code}"
                    }
                }

                with patch("services.telegram_service.TelegramService.send_message") as mock_send:
                    res_link = client.post(webhook_url, json=link_payload)
                    assert res_link.status_code == 200, "Fallo webhook al procesar codigo de vinculacion"
                    
                    db.session.refresh(user)
                    assert user.telegram_chat_id == TEST_CHAT_ID, "No se vinculo el chat_id correctamente"
                    
                    # Verificar que se envio mensaje de exito
                    sent_messages = [call[0][1] for call in mock_send.call_args_list]
                    assert any("Vinculación Exitosa" in msg for msg in sent_messages), "No se envio mensaje de confirmacion de vinculacion"
                    print("Ok Test 5.2: Vinculacion automatica de Chat ID exitosa.")

                # Test 6: Flujo de Guía de Remisión (SUNAT)
                print("\nTest 6: Flujo de Guia de Remision (SUNAT)...")
                
                # Asegurar que el usuario tenga el chat_id asociado
                user.telegram_chat_id = TEST_CHAT_ID
                db.session.commit()

                message_payload = {
                    "update_id": 10010,
                    "message": {
                        "message_id": 1004,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": "generame una guia de remision de 20 sacos de 20kg y 5 de 10kg para el RUC 20601234567"
                    }
                }

                with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                     patch("services.telegram_service.TelegramService.edit_message") as mock_edit:

                    if not use_real_gemini:
                        with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                            mock_gemini.return_value = {
                                "action": "solicitar_guia_remision",
                                "args": {
                                    "items": [
                                        {"producto_nombre": "20kg", "cantidad": 20},
                                        {"producto_nombre": "10kg", "cantidad": 5}
                                    ],
                                    "destinatario_documento": "20601234567"
                                }
                            }
                            res = client.post(webhook_url, json=message_payload)
                    else:
                        res = client.post(webhook_url, json=message_payload)

                    assert res.status_code == 200
                    db.session.refresh(user)
                    assert user.telegram_context["action"] == "guia_remision"
                    assert len(user.telegram_context["items"]) == 2
                    print("Ok Test 6.1: Interpretacion de Guia de Remision exitosa.")

                    # Confirmar emisión de Guía en SUNAT
                    callback_payload = {
                        "update_id": 10011,
                        "callback_query": {
                            "id": "cb_6",
                            "from": {"id": TEST_CHAT_ID},
                            "message": {"message_id": 1004, "chat": {"id": TEST_CHAT_ID}},
                            "data": "confirm:guia_remision"
                        }
                    }

                    # Mockear respuestas de sunat_service
                    with patch("services.sunat_service.SunatService.emitir_guia_remision") as mock_emit, \
                         patch("services.sunat_service.SunatService.consultar_estado_ticket") as mock_status:
                        
                        mock_emit.return_value = {"numTicket": "TICKET-TEST-123456"}
                        mock_status.return_value = {
                            "codRespuesta": "0",
                            "desRespuesta": "La Guia de Remision fue Aceptada"
                        }

                        res_cb = client.post(webhook_url, json=callback_payload)
                        assert res_cb.status_code == 200
                        
                        # Verificar llamadas
                        mock_emit.assert_called_once()
                        mock_status.assert_called_once_with("TICKET-TEST-123456")
                        
                        # Verificar mensaje de exito editado
                        edited_calls = [call[0][2] for call in mock_edit.call_args_list]
                        assert any("Aceptada por SUNAT" in msg for msg in edited_calls), "No se encontro mensaje de aceptacion de SUNAT"
                        print("Ok Test 6.2: Confirmacion y Emision de Guia de Remision exitosa.")

                # Test 7: Flujo de Pedidos (Orden sin descuento de Stock y transición a Venta)
                print("\nTest 7: Flujo de Pedidos (Ordenes sin descuento de Stock)...")
                
                # Obtener stock inicial de la presentación de prueba
                stock_inicial = float(db.session.query(db.func.sum(Inventario.cantidad)).filter(
                    Inventario.almacen_id == user.almacen_id,
                    Inventario.presentacion_id == presentacion.id
                ).scalar() or 0.0)
                
                message_payload = {
                    "update_id": 10012,
                    "message": {
                        "message_id": 1005,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": f"pedido de 2 sacos de {presentacion.nombre} para {cliente_gen.nombre}"
                    }
                }

                with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                     patch("services.telegram_service.TelegramService.edit_message") as mock_edit:

                    if not use_real_gemini:
                        with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                            mock_gemini.return_value = {
                                "action": "interpretar_operacion",
                                "args": {
                                    "cliente_nombre": cliente_gen.nombre,
                                    "estado": "pedido",
                                    "items": [
                                        {"producto_nombre": presentacion.nombre, "cantidad": 2}
                                    ]
                                }
                            }
                            res = client.post(webhook_url, json=message_payload)
                    else:
                        res = client.post(webhook_url, json=message_payload)

                    assert res.status_code == 200
                    db.session.refresh(user)
                    assert user.telegram_context["action"] == "venta"
                    assert user.telegram_context["estado"] == "pedido"
                    print("STOCK DESPUES DE INTERPRETAR:", float(db.session.query(db.func.sum(Inventario.cantidad)).filter(Inventario.almacen_id == user.almacen_id, Inventario.presentacion_id == presentacion.id).scalar() or 0.0))
                    print("Ok Test 7.1: Interpretacion de Pedido exitosa.")

                    # Confirmar el pedido
                    callback_payload = {
                        "update_id": 10013,
                        "callback_query": {
                            "id": "cb_7",
                            "from": {"id": TEST_CHAT_ID},
                            "message": {"message_id": 1005, "chat": {"id": TEST_CHAT_ID}},
                            "data": "confirm:venta"
                        }
                    }
                    res_cb = client.post(webhook_url, json=callback_payload)
                    assert res_cb.status_code == 200
                    
                    # Buscar el pedido registrado en la base de datos
                    pedido_db = Venta.query.filter_by(estado='pedido').order_by(Venta.id.desc()).first()
                    assert pedido_db is not None, "El pedido no se registró en la base de datos"
                    assert pedido_db.fecha_pedido is not None, "fecha_pedido no se asignó"
                    assert pedido_db.fecha_entrega is not None, "fecha_entrega no se asignó"
                    assert pedido_db.fecha is None, "fecha de venta debe ser nula en un pedido"
                    
                    # Verificar que el stock NO disminuyó
                    stock_despues_cb = float(db.session.query(db.func.sum(Inventario.cantidad)).filter(Inventario.almacen_id == user.almacen_id, Inventario.presentacion_id == presentacion.id).scalar() or 0.0)
                    print("STOCK INICIAL SUMADO:", stock_inicial)
                    print("STOCK DESPUES CB SUMADO:", stock_despues_cb)
                    assert stock_despues_cb == stock_inicial, "El stock no debió disminuir en un pedido"
                    
                    # Verificar que no hay movimientos de salida de stock para este pedido
                    mov_count = Movimiento.query.filter_by(presentacion_id=presentacion.id, motivo=f"Venta ID: {pedido_db.id} (Telegram)").count()
                    assert mov_count == 0, "No debieron generarse movimientos para un pedido"
                    print("Ok Test 7.2: Confirmacion de Pedido exitosa (Sin alterar stock).")

                    # Simular la transición de Pedido a Completado usando el endpoint PUT VentaResource
                    from flask_jwt_extended import create_access_token
                    access_token = create_access_token(identity=str(user.id), additional_claims={"rol": "admin"})
                    headers = {
                        "Authorization": f"Bearer {access_token}"
                    }
                    
                    # Actualizar a 'completado' pasándole los detalles nuevos
                    update_payload = {
                        "estado": "completado",
                        "detalles": [
                            {"presentacion_id": presentacion.id, "cantidad": 2, "precio_unitario": 25.0}
                        ]
                    }
                    
                    res_put = client.put(f"/ventas/{pedido_db.id}", json=update_payload, headers=headers)
                    print("PUT RESPONSE STATUS:", res_put.status_code)
                    print("PUT RESPONSE DATA:", res_put.data)
                    assert res_put.status_code == 200
                    
                    # Refrescar y validar que la fecha de venta (completado) ya no sea nula
                    db.session.refresh(pedido_db)
                    assert pedido_db.fecha is not None, "La fecha de venta debe asignarse al completar el pedido"
                    
                    # Verificar que ahora el stock SÍ disminuyó en 2 unidades
                    stock_despues = float(db.session.query(db.func.sum(Inventario.cantidad)).filter(
                        Inventario.almacen_id == user.almacen_id,
                        Inventario.presentacion_id == presentacion.id
                    ).scalar() or 0.0)
                    print("STOCK INICIAL SUMADO:", stock_inicial)
                    print("STOCK DESPUES SUMADO:", stock_despues)
                    assert stock_despues == stock_inicial - 2.0, "El stock debió disminuir al completar el pedido"
                    
                    # Verificar que ahora sí existe el movimiento de salida
                    mov_venta = Movimiento.query.filter(
                        Movimiento.presentacion_id == presentacion.id,
                        Movimiento.tipo == 'salida',
                        Movimiento.motivo.like(f"Venta ID: {pedido_db.id}%")
                    ).first()
                    assert mov_venta is not None, "Debió registrarse el movimiento de salida al completar la venta"
                    print("Ok Test 7.3: Transicion de Pedido a Completado exitosa (Stock descontado y fechas validadas).")

                # Test 8: Creación de Clientes
                print("\nTest 8: Registro de Clientes (Auto-registro por celular y creación dedicada)...")
                
                # Parte 8.1: Auto-creación de cliente durante una venta/pedido
                TEST_NEW_PHONE = "911222333"
                # Limpiar cualquier cliente existente con ese teléfono
                Cliente.query.filter_by(telefono=TEST_NEW_PHONE).delete()
                db.session.commit()
                
                message_payload_auto = {
                    "update_id": 10014,
                    "message": {
                        "message_id": 1006,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": f"pedido de 2 sacos de {presentacion.nombre} para Carlos al celular {TEST_NEW_PHONE}"
                    }
                }
                
                with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                     patch("services.telegram_service.TelegramService.edit_message") as mock_edit:
                     
                    if not use_real_gemini:
                        with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                            mock_gemini.return_value = {
                                "action": "interpretar_operacion",
                                "args": {
                                    "cliente_nombre": "Carlos",
                                    "estado": "pedido",
                                    "items": [
                                        {"producto_nombre": presentacion.nombre, "cantidad": 2}
                                    ]
                                }
                            }
                            res = client.post(webhook_url, json=message_payload_auto)
                    else:
                        res = client.post(webhook_url, json=message_payload_auto)
                        
                    assert res.status_code == 200
                    
                    # Verificar que el cliente fue creado automáticamente en la base de datos
                    cliente_auto = Cliente.query.filter_by(telefono=TEST_NEW_PHONE).first()
                    assert cliente_auto is not None, "El cliente nuevo no se creó automáticamente"
                    assert cliente_auto.nombre == "Carlos", "El nombre del cliente auto-creado es incorrecto"
                    print("Ok Test 8.1: Auto-registro de cliente nuevo vía celular exitoso.")

                # Parte 8.2: Creación dedicada de cliente con registrar_cliente
                TEST_DEDICATED_PHONE = "999000111"
                # Limpiar cliente
                Cliente.query.filter_by(telefono=TEST_DEDICATED_PHONE).delete()
                db.session.commit()
                
                message_payload_dedicated = {
                    "update_id": 10015,
                    "message": {
                        "message_id": 1007,
                        "from": {"id": TEST_CHAT_ID},
                        "chat": {"id": TEST_CHAT_ID},
                        "text": f"crear cliente Carlos Torres celular {TEST_DEDICATED_PHONE} direccion Calle Lima 123"
                    }
                }
                
                with patch("services.telegram_service.TelegramService.send_message") as mock_send, \
                     patch("services.telegram_service.TelegramService.edit_message") as mock_edit:
                     
                    if not use_real_gemini:
                        with patch("services.gemini_service.GeminiService.process_command") as mock_gemini:
                            mock_gemini.return_value = {
                                "action": "registrar_cliente",
                                "args": {
                                    "nombre": "Carlos Torres",
                                    "telefono": TEST_DEDICATED_PHONE,
                                    "direccion": "Calle Lima 123"
                                }
                            }
                            res = client.post(webhook_url, json=message_payload_dedicated)
                    else:
                        res = client.post(webhook_url, json=message_payload_dedicated)
                        
                    assert res.status_code == 200
                    db.session.refresh(user)
                    assert user.telegram_context["action"] == "cliente"
                    assert user.telegram_context["nombre"] == "Carlos Torres"
                    print("Ok Test 8.2.1: Solicitud de registro de cliente dedicada exitosa (Contexto guardado).")
                    
                    # Confirmar la creación del cliente
                    callback_payload_dedicated = {
                        "update_id": 10016,
                        "callback_query": {
                            "id": "cb_8",
                            "from": {"id": TEST_CHAT_ID},
                            "message": {"message_id": 1007, "chat": {"id": TEST_CHAT_ID}},
                            "data": "confirm:cliente"
                        }
                    }
                    res_cb = client.post(webhook_url, json=callback_payload_dedicated)
                    assert res_cb.status_code == 200
                    
                    cliente_dedicated = Cliente.query.filter_by(telefono=TEST_DEDICATED_PHONE).first()
                    assert cliente_dedicated is not None, "El cliente no se guardó en la base de datos al confirmar"
                    assert cliente_dedicated.nombre == "Carlos Torres", "El nombre es incorrecto"
                    assert "Calle Lima 123" in cliente_dedicated.direccion, "La dirección es incorrecta"
                    print("Ok Test 8.2.2: Confirmación de registro de cliente dedicada exitosa.")

            print("\n[OK] Todas las pruebas de integracion del bot de Telegram se completaron exitosamente!")

        finally:
            # Restaurar datos originales del usuario de pruebas
            user.telegram_chat_id = orig_chat_id
            user.telegram_context = orig_context
            user.almacen_id = orig_almacen_id
            db.session.commit()
            print("Limpieza completa. Base de datos restaurada.")

if __name__ == "__main__":
    run_tests()
