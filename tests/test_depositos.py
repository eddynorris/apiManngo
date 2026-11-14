import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from flask import Flask

from resources.reporte_financiero_resource import (
    DepositosHistorialResource,
    ReporteUnificadoResource,
    ResumenFinancieroResource,
)


class TestDepositos(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        patcher = patch('flask_jwt_extended.view_decorators.verify_jwt_in_request', lambda *args, **kwargs: None)
        self.addCleanup(patcher.stop)
        patcher.start()

    @patch('resources.reporte_financiero_resource.db')
    def test_resumen_financiero_depositado_total(self, mock_db):
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.group_by.return_value = q
        q.order_by.return_value = q
        q.subquery.return_value = MagicMock()
        q.first.return_value = (Decimal('0.00'), 0)
        q.scalar.return_value = Decimal('0.00')
        q.all.return_value = []
        mock_db.session.query.return_value = q

        resource = ResumenFinancieroResource()
        with self.app.test_request_context('/reportes/resumen-financiero?fecha_inicio=2025-01-01&fecha_fin=2025-12-31'):
            resp, status = resource.get()
            self.assertEqual(status, 200)
            self.assertIn('depositado_total', resp)
            self.assertIsInstance(resp['depositado_total'], str)

    @patch('resources.reporte_financiero_resource.db')
    def test_unificado_depositado_total(self, mock_db):
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.group_by.return_value = q
        q.order_by.return_value = q
        q.subquery.return_value = MagicMock()
        q.first.return_value = (Decimal('0.00'), 0)
        q.scalar.return_value = Decimal('0.00')
        q.all.return_value = []
        mock_db.session.query.return_value = q

        resource = ReporteUnificadoResource()
        with self.app.test_request_context('/reportes/unificado?fecha_inicio=2025-01-01&fecha_fin=2025-12-31'):
            resp, status = resource.get()
            self.assertEqual(status, 200)
            self.assertIn('resumen_financiero', resp)
            self.assertIn('depositado_total', resp['resumen_financiero'])
            self.assertIn('historial_depositos', resp)
            self.assertIsInstance(resp['historial_depositos'], list)

    @patch('resources.reporte_financiero_resource.db')
    def test_historial_validacion_fechas(self, mock_db):
        resource = DepositosHistorialResource()
        with self.app.test_request_context('/reportes/depositos-historial?fecha_inicio=2025-02-01&fecha_fin=2025-01-01'):
            resp, status = resource.get()
            self.assertEqual(status, 400)
            self.assertIn('error', resp)


if __name__ == '__main__':
    unittest.main()
