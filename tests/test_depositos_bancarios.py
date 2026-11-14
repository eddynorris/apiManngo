#!/usr/bin/env python3
"""
Script de prueba para la funcionalidad de depósitos bancarios
Ejecuta este script para verificar que todo funcione correctamente
"""

__test__ = False


from datetime import datetime, timedelta

import requests

# Configuración del servidor
BASE_URL = "http://localhost:5000/api"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_JWT_TOKEN_HERE"  # Reemplazar con token real
}

def test_crear_pago_con_deposito():
    """Prueba crear un pago con información de depósito"""
    print("\n🧪 Probando: Crear pago con depósito completo...")

    data = {
        "venta_id": 1,  # Ajustar según tu base de datos
        "monto": "1000.00",
        "metodo_pago": "transferencia",
        "referencia": "TXN123456",
        "fecha": datetime.now().isoformat(),
        "monto_depositado": "1000.00",
        "depositado": True,
        "fecha_deposito": datetime.now().isoformat()
    }

    try:
        response = requests.post(f"{BASE_URL}/pagos",
                               headers=HEADERS,
                               json=data)

        if response.status_code == 201:
            result = response.json()
            print(f"✅ Pago creado exitosamente. ID: {result.get('id')}")
            print(f"   Monto en gerencia: {result.get('monto_en_gerencia', 'N/A')}")
            return result.get('id')
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")
        return None

def test_crear_pago_sin_deposito():
    """Prueba crear un pago sin depósito (efectivo en gerencia)"""
    print("\n🧪 Probando: Crear pago sin depósito (efectivo)...")

    data = {
        "venta_id": 1,  # Ajustar según tu base de datos
        "monto": "500.00",
        "metodo_pago": "efectivo",
        "fecha": datetime.now().isoformat(),
        "depositado": False
    }

    try:
        response = requests.post(f"{BASE_URL}/pagos",
                               headers=HEADERS,
                               json=data)

        if response.status_code == 201:
            result = response.json()
            print(f"✅ Pago creado exitosamente. ID: {result.get('id')}")
            print(f"   Monto en gerencia: {result.get('monto_en_gerencia', 'N/A')}")
            return result.get('id')
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")
        return None

def test_registrar_deposito_bancario(pago_ids):
    """Prueba registrar un depósito bancario para múltiples pagos"""
    print("\n🧪 Probando: Registrar depósito bancario...")

    if not pago_ids:
        print("⚠️  No hay pagos para depositar")
        return

    data = {
        "pago_ids": pago_ids,
        "monto_depositado": "400.00",  # Depósito parcial
        "fecha_deposito": datetime.now().isoformat()
    }

    try:
        response = requests.post(f"{BASE_URL}/depositos-bancarios",
                               headers=HEADERS,
                               json=data)

        if response.status_code == 200:
            result = response.json()
            print("✅ Depósito registrado exitosamente")
            print(f"   Pagos actualizados: {result.get('pagos_actualizados')}")
            print(f"   Monto total depositado: {result.get('monto_total_depositado')}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")

def test_obtener_resumen_depositos():
    """Prueba obtener el resumen de depósitos"""
    print("\n🧪 Probando: Obtener resumen de depósitos...")

    # Filtros de ejemplo
    params = {
        "fecha_desde": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        "fecha_hasta": datetime.now().strftime("%Y-%m-%d")
    }

    try:
        response = requests.get(f"{BASE_URL}/depositos-bancarios",
                              headers=HEADERS,
                              params=params)

        if response.status_code == 200:
            result = response.json()
            resumen = result.get('resumen', {})

            print("✅ Resumen obtenido exitosamente:")
            print(f"   Total pagos: ${resumen.get('total_pagos', '0')}")
            print(f"   Total depositado: ${resumen.get('total_depositado', '0')}")
            print(f"   Total en gerencia: ${resumen.get('total_en_gerencia', '0')}")
            print(f"   Pagos depositados: {resumen.get('cantidad_pagos_depositados', 0)}")
            print(f"   Pagos pendientes: {resumen.get('cantidad_pagos_pendientes', 0)}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")

def test_obtener_pago_detalle(pago_id):
    """Prueba obtener el detalle de un pago específico"""
    if not pago_id:
        return

    print(f"\n🧪 Probando: Obtener detalle del pago {pago_id}...")

    try:
        response = requests.get(f"{BASE_URL}/pagos/{pago_id}",
                              headers=HEADERS)

        if response.status_code == 200:
            result = response.json()
            print("✅ Detalle del pago obtenido:")
            print(f"   ID: {result.get('id')}")
            print(f"   Monto: ${result.get('monto')}")
            print(f"   Monto depositado: ${result.get('monto_depositado', 'N/A')}")
            print(f"   Monto en gerencia: ${result.get('monto_en_gerencia', 'N/A')}")
            print(f"   Depositado: {result.get('depositado')}")
            print(f"   Fecha depósito: {result.get('fecha_deposito', 'N/A')}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")

def run_all_tests():
    """Ejecuta todas las pruebas"""
    print("🚀 Iniciando pruebas de funcionalidad de depósitos bancarios...")
    print("\n⚠️  IMPORTANTE: Asegúrate de:")
    print("   1. Tener el servidor corriendo en http://localhost:5000")
    print("   2. Reemplazar YOUR_JWT_TOKEN_HERE con un token válido")
    print("   3. Ajustar los venta_id según tu base de datos")

    # Verificar configuración
    if "YOUR_JWT_TOKEN_HERE" in HEADERS["Authorization"]:
        print("\n❌ ERROR: Debes configurar un token JWT válido en HEADERS")
        return

    # Ejecutar pruebas
    pago_ids = []

    # Crear pagos de prueba
    pago_id_1 = test_crear_pago_con_deposito()
    if pago_id_1:
        pago_ids.append(pago_id_1)

    pago_id_2 = test_crear_pago_sin_deposito()
    if pago_id_2:
        pago_ids.append(pago_id_2)

    # Probar depósito bancario
    if len(pago_ids) > 1:
        test_registrar_deposito_bancario([pago_ids[1]])  # Solo el pago sin depósito

    # Obtener resumen
    test_obtener_resumen_depositos()

    # Obtener detalles de pagos
    for pago_id in pago_ids:
        test_obtener_pago_detalle(pago_id)

    print("\n🎉 Pruebas completadas!")

def test_validaciones():
    """Prueba las validaciones del sistema"""
    print("\n🧪 Probando validaciones...")

    # Prueba 1: Monto depositado mayor al monto del pago
    print("\n   Prueba 1: Monto depositado excede monto del pago")
    data = {
        "pago_ids": [1],  # Ajustar según tu base de datos
        "monto_depositado": "99999.00",
        "fecha_deposito": datetime.now().isoformat()
    }

    try:
        response = requests.post(f"{BASE_URL}/depositos-bancarios",
                               headers=HEADERS,
                               json=data)

        if response.status_code == 400:
            print("   ✅ Validación funcionando correctamente")
        else:
            print(f"   ❌ Validación falló: {response.status_code}")

    except Exception as e:
        print(f"   ❌ Error: {str(e)}")

    # Prueba 2: Campos requeridos faltantes
    print("\n   Prueba 2: Campos requeridos faltantes")
    data = {
        "pago_ids": [1]
        # Faltan monto_depositado y fecha_deposito
    }

    try:
        response = requests.post(f"{BASE_URL}/depositos-bancarios",
                               headers=HEADERS,
                               json=data)

        if response.status_code == 400:
            print("   ✅ Validación funcionando correctamente")
        else:
            print(f"   ❌ Validación falló: {response.status_code}")

    except Exception as e:
        print(f"   ❌ Error: {str(e)}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Pruebas de depósitos bancarios')
    parser.add_argument('--validaciones', action='store_true',
                       help='Solo ejecutar pruebas de validaciones')
    parser.add_argument('--token', type=str,
                       help='Token JWT para autenticación')
    parser.add_argument('--url', type=str, default="http://localhost:5000/api",
                       help='URL base del API')

    args = parser.parse_args()

    if args.token:
        HEADERS["Authorization"] = f"Bearer {args.token}"

    if args.url:
        BASE_URL = args.url

    if args.validaciones:
        test_validaciones()
    else:
        run_all_tests()
