import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from resources.cliente_resource import ClienteProyeccionResource


class MockPaginated:
    def __init__(self, items):
        self.items = items
        self.total = len(items)
        self.page = 1
        self.per_page = len(items)
        self.pages = 1


class VP:
    def __init__(self, id, nombre, telefono, ciudad, ultima_fecha_compra, proxima_compra_estimada,
                 frecuencia_compra_dias, dias_retraso=0, estado_proyeccion='programado'):
        self.id = id
        self.nombre = nombre
        self.telefono = telefono
        self.ciudad = ciudad
        self.ultima_fecha_compra = ultima_fecha_compra
        self.proxima_compra_estimada = proxima_compra_estimada
        self.frecuencia_compra_dias = frecuencia_compra_dias
        self.dias_retraso = dias_retraso
        self.estado_proyeccion = estado_proyeccion
        self.monto_total_comprado = 0
        self.total_ventas = 0
        self.promedio_compra = 0


class TestClienteProyecciones(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.resource = ClienteProyeccionResource()

    @patch('resources.cliente_resource.db')
    def test_lista_por_defecto_solo_proyeccion_calculable(self, mock_db):
        items = [
            VP(1, 'A', '111', 'X', None, None, 0),  # no calculable
            VP(2, 'B', '222', 'Y', None, None, 5),  # no calculable
            VP(3, 'C', '333', 'Z', MagicMock(), MagicMock(), 7),  # calculable
        ]

        mock_query = MagicMock()
        # filter() chaining returns the same mock
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.paginate.return_value = MockPaginated([items[2]])
        mock_db.session.query.return_value = mock_query

        with self.app.test_request_context('/clientes/proyecciones'):
            resp, status = self.resource._get_lista_proyecciones()
            self.assertEqual(status, 200)
            self.assertIn('data', resp)
            self.assertGreaterEqual(len(resp['data']), 1)
            row = resp['data'][0]
            # Sólo 6 columnas + codigo (se usa para detalle)
            self.assertSetEqual(set(row.keys()), {
                'codigo','nombre','telefono','ciudad','ultima_fecha_compra','proxima_fecha_estimada','estado_proyeccion'
            })

    def test_detalle_por_codigo(self):
        mock_cliente = MagicMock()
        mock_cliente.id = 3
        mock_cliente.frecuencia_compra_dias = 7
        mock_cliente.ventas = []

        vp = VP(3, 'C', '333', 'Z', MagicMock(), MagicMock(), 7)

        with self.app.app_context():
            with patch('resources.cliente_resource.Cliente.query') as mock_cliente_query, \
                 patch('resources.cliente_resource.db') as mock_db:
                mock_cliente_query.options.return_value.get_or_404.return_value = mock_cliente
                mock_db.session.query.return_value.filter.return_value.first.return_value = vp

                with self.app.test_request_context('/clientes/proyecciones?codigo=3'):
                    resp, status = self.resource._get_detalle_cliente(cliente_id=None)
                    self.assertEqual(status, 200)
                    self.assertIn('codigo', resp)
                    self.assertEqual(resp['codigo'], '3')
                    self.assertIn('historial_ventas', resp)
                    self.assertIn('estadisticas', resp)
                    self.assertIn('proyeccion_detallada', resp)


if __name__ == '__main__':
    unittest.main()
