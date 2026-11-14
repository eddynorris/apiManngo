import os
import pytest
from app import app
from utils.file_handlers import save_file, get_presigned_url, delete_file
import io
import requests

def test_supabase_storage_operations():
    """
    Verifica que las operaciones de guardar, obtener URL pre-firmada
    y eliminar archivo en Supabase Storage funcionen óptimamente.
    """
    with app.app_context():
        # 1. Preparar un archivo PDF ficticio para pruebas
        dummy_file = io.BytesIO(b"%PDF-1.4 ... Test Content for Supabase Storage ...")
        dummy_file.filename = "test_integration_document.pdf"
        dummy_file.content_type = "application/pdf"

        # 2. Guardar archivo en el bucket 'comprobantes'
        storage_key = save_file(dummy_file, "comprobantes")
        assert storage_key is not None, "No se pudo guardar el archivo en Supabase Storage"
        assert storage_key.startswith("comprobantes/"), f"La clave de almacenamiento no tiene el formato esperado: {storage_key}"

        # 3. Obtener la URL pre-firmada
        signed_url = get_presigned_url(storage_key)
        assert signed_url is not None, "No se pudo generar la URL pre-firmada"
        assert "supabase" in signed_url.lower(), f"La URL pre-firmada no apunta a Supabase: {signed_url}"

        # 4. Descargar el archivo utilizando la URL pre-firmada y verificar integridad
        response = requests.get(signed_url)
        assert response.status_code == 200, f"Error al acceder a la URL firmada. Status: {response.status_code}"
        assert response.content == b"%PDF-1.4 ... Test Content for Supabase Storage ...", "El contenido recuperado no coincide con el enviado"

        # 5. Eliminar el archivo
        deleted = delete_file(storage_key)
        assert deleted is True, "No se pudo eliminar el archivo de Supabase Storage"

        import time
        time.sleep(1)

        # 6. Verificar que el archivo ya no es accesible (debe responder con error)
        response_after = requests.get(signed_url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        assert response_after.status_code in {400, 403, 404}, f"El recurso sigue accesible después de eliminarlo. Status: {response_after.status_code}"
