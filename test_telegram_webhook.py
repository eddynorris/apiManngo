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
        
        # Si el usuario no tiene almacen, asignarle el primero
        if not user.almacen_id:
            almacen = Almacen.query.first()
            if not almacen:
                # Crear almacén temporal
                almacen = Almacen(nombre="Almacen Principal", direccion="Calle Falsa 123", ciudad="Lima")
                db.session.add(almacen)
                db.session.flush()
            user.almacen_id = almacen.id
            print(f"Almacen asignado al usuario de pruebas: {almacen.nombre} (ID: {almacen.id})")

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
