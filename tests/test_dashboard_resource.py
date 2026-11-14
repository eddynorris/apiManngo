
# La fixture 'test_client' se inyecta automáticamente desde conftest.py
# Ya no necesitamos importar Decimal, pytest, os, app, db, ni los modelos aquí.
# Tampoco necesitamos la función setup_test_data ni el fixture local.

def get_auth_token(client, username, password):
    """Obtiene un token JWT para un usuario."""
    response = client.post('/auth', json={'username': username, 'password': password})
    return response.get_json()['access_token']

def test_dashboard_saldo_pendiente_admin_view_con_detalles(test_client):
    """
    Prueba que un admin vea el saldo pendiente de todos los clientes con el detalle
    completo de ventas y pagos.
    """
    token = get_auth_token(test_client, 'admin', 'adminpass')
    headers = {'Authorization': f'Bearer {token}'}

    response = test_client.get('/dashboard', headers=headers)
    assert response.status_code == 200

    data = response.get_json()
    # Convertimos la lista a un diccionario para facilitar las búsquedas
    clientes_deuda = {c['nombre']: c for c in data['clientes_con_saldo_pendiente']}

    # --- Aserciones Generales ---
    assert len(clientes_deuda) == 3 # Deben aparecer Cliente A, B y C
    assert 'Cliente D (Sin Deuda)' not in clientes_deuda

    # --- Aserciones para Cliente A ---
    cliente_a = clientes_deuda.get('Cliente A (Deuda en Almacén 1)')
    assert cliente_a is not None
    assert cliente_a['saldo_pendiente_total'] == 150.0 # 100 + 50
    assert len(cliente_a['ventas_pendientes']) == 2 # Tiene dos ventas con deuda

    # --- Aserciones para Cliente B ---
    cliente_b = clientes_deuda.get('Cliente B (Deuda en Almacén 2)')
    assert cliente_b is not None
    assert cliente_b['saldo_pendiente_total'] == 200.0
    assert len(cliente_b['ventas_pendientes']) == 1

    # --- Aserciones DETALLADAS para Cliente C (caso más complejo) ---
    cliente_c = clientes_deuda.get('Cliente C (Deuda Mixta)')
    assert cliente_c is not None
    assert cliente_c['saldo_pendiente_total'] == 120.0 # (100 - 30) + 50 = 70 + 50
    assert len(cliente_c['ventas_pendientes']) == 2 # Una venta en cada almacén

    # Encontrar la venta con pago parcial
    venta_parcial = next((v for v in cliente_c['ventas_pendientes'] if v['estado_pago'] == 'parcial'), None)
    assert venta_parcial is not None
    assert venta_parcial['total_venta'] == 100.0
    assert venta_parcial['saldo_pendiente_venta'] == 70.0
    assert len(venta_parcial['pagos']) == 1 # Tiene un pago registrado
    assert venta_parcial['pagos'][0]['monto'] == 30.0

def test_dashboard_saldo_pendiente_user_view_con_detalles(test_client):
    """
    Prueba que un vendedor vea solo las deudas de su almacén (Almacén 1)
    con el detalle correcto de ventas y pagos.
    """
    token = get_auth_token(test_client, 'vendedor', 'ventapass')
    headers = {'Authorization': f'Bearer {token}'}

    response = test_client.get('/dashboard', headers=headers)
    assert response.status_code == 200

    data = response.get_json()
    # Convertimos la lista a un diccionario para facilitar las búsquedas
    clientes_deuda = {c['nombre']: c for c in data['clientes_con_saldo_pendiente']}

    # --- Aserciones Generales ---
    # Solo deben aparecer clientes con deuda en el Almacén 1
    assert len(clientes_deuda) == 2
    assert 'Cliente B (Deuda en Almacén 2)' not in clientes_deuda # No debe aparecer

    # --- Aserciones para Cliente A ---
    cliente_a = clientes_deuda.get('Cliente A (Deuda en Almacén 1)')
    assert cliente_a is not None
    assert cliente_a['saldo_pendiente_total'] == 150.0
    assert len(cliente_a['ventas_pendientes']) == 2

    # --- Aserciones DETALLADAS para Cliente C ---
    # Para el vendedor del Almacén 1, solo debe aparecer la deuda de ese almacén
    cliente_c = clientes_deuda.get('Cliente C (Deuda Mixta)')
    assert cliente_c is not None
    assert cliente_c['saldo_pendiente_total'] == 70.0 # Solo la deuda del Almacén 1 (100 - 30)
    assert len(cliente_c['ventas_pendientes']) == 1 # Solo debe aparecer UNA venta

    # Verificar que la venta sea la correcta (la del pago parcial)
    venta_almacen_1 = cliente_c['ventas_pendientes'][0]
    assert venta_almacen_1['total_venta'] == 100.0
    assert venta_almacen_1['saldo_pendiente_venta'] == 70.0
    assert len(venta_almacen_1['pagos']) == 1
