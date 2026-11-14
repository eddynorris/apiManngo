import unittest
from unittest.mock import patch, MagicMock
import os
from services.gemini_service import GeminiService

class TestGeminiService(unittest.TestCase):
    def setUp(self):
        # Mock environment variables before initializing GeminiService
        with patch.dict('os.environ', {'GOOGLE_API_KEY': 'fake-key'}):
            with patch('google.generativeai.configure') as mock_configure:
                self.gemini_service = GeminiService()

    @patch('google.generativeai.GenerativeModel')
    def test_process_command_interpretar_operacion(self, mock_model_class):
        # Mock del modelo y la respuesta
        mock_model = mock_model_class.return_value
        self.gemini_service._model = mock_model
        mock_response = MagicMock()
        
        # Simular una llamada a función 'interpretar_operacion'
        mock_part = MagicMock()
        mock_part.function_call.name = 'interpretar_operacion'
        mock_part.function_call.args = {
            'cliente_nombre': 'Juan Perez',
            'items': [{'producto_nombre': 'Carbón', 'cantidad': 10}],
            'pagos': [{'monto': 200, 'metodo_pago': 'efectivo'}],
            'gasto_asociado': {'descripcion': 'Taxi', 'monto': 20, 'categoria': 'logistica'}
        }
        
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].content.parts = [mock_part]
        mock_model.generate_content.return_value = mock_response

        # Ejecutar
        result = self.gemini_service.process_command("Venta a Juan Perez 10 carbón, pago 200 efectivo y gaste 20 en taxi")

        # Verificar
        self.assertEqual(result['action'], 'interpretar_operacion')
        self.assertEqual(result['args']['cliente_nombre'], 'Juan Perez')
        self.assertEqual(len(result['args']['items']), 1)
        self.assertEqual(result['args']['gasto_asociado']['monto'], 20)

    @patch('google.generativeai.GenerativeModel')
    def test_process_command_unknown(self, mock_model_class):
        mock_model = mock_model_class.return_value
        self.gemini_service._model = mock_model
        mock_response = MagicMock()
        
        # Simular respuesta de texto sin función
        mock_part = MagicMock()
        del mock_part.function_call # Asegurar que no tenga function_call
        mock_part.text = "No entendí lo que dijiste."
        
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].content.parts = [mock_part]
        mock_model.generate_content.return_value = mock_response

        result = self.gemini_service.process_command("Hola mundo")
        
        self.assertEqual(result['action'], 'none')
        self.assertEqual(result['message'], "No entendí lo que dijiste.")

    @patch('google.generativeai.GenerativeModel')
    def test_process_command_error(self, mock_model_class):
        # Setup mock to raise exception
        mock_model = mock_model_class.return_value
        self.gemini_service._model = mock_model
        mock_model.generate_content.side_effect = Exception("API Error")

        # Execute
        result = self.gemini_service.process_command("Error test")

        # Verify
        self.assertEqual(result['action'], 'error')
        self.assertIn("API Error", result['message'])

if __name__ == '__main__':
    unittest.main()
