import unittest
from datetime import datetime
from flask import Flask
from extensions import db
from models import Cliente
from resources.cliente_resource import ClienteProyeccionResource
from sqlalchemy import func


class ClienteProyeccionResourceDateFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        cls.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        db.init_app(cls.app)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.create_all()

        # Seed data
        c1 = Cliente(nombre='Alice', ultima_fecha_compra=datetime(2025, 1, 10))
        c2 = Cliente(nombre='Bob', ultima_fecha_compra=datetime(2025, 2, 15))
        c3 = Cliente(nombre='Charlie', ultima_fecha_compra=None)
        db.session.add_all([c1, c2, c3])
        db.session.commit()

        cls.resource = ClienteProyeccionResource()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.ctx.pop()

    def test_parse_date_value_formats(self):
        r = self.resource
        self.assertEqual(r._parse_date_value('2025-01-10').isoformat(), '2025-01-10')
        self.assertEqual(r._parse_date_value('10/01/2025').isoformat(), '2025-01-10')
        self.assertEqual(r._parse_date_value('10-01-2025').isoformat(), '2025-01-10')
        self.assertEqual(r._parse_date_value('2025/01/10').isoformat(), '2025-01-10')

    def test_single_date_filter(self):
        query = db.session.query(Cliente)
        # SQLite: usar func.date para comparación por día
        filtered = self.resource._apply_date_filters(
            query,
            func.date(Cliente.ultima_fecha_compra),
            None,
            '2025-02-15',
            None,
            None
        )
        results = filtered.all()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].nombre, 'Bob')

    def test_date_range_filter(self):
        query = db.session.query(Cliente)
        filtered = self.resource._apply_date_filters(
            query,
            func.date(Cliente.ultima_fecha_compra),
            None,
            None,
            '2025-01-01',
            '2025-01-31'
        )
        results = filtered.all()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].nombre, 'Alice')

    def test_coalesce_ultima_venta_null_uses_ultima_fecha_compra(self):
        query = db.session.query(Cliente)
        # Simular columna ultima_venta nula con func.nullif para mantener compatibilidad
        ultima_venta_col = func.nullif(func.date(''), '')  # generará NULL
        filtered = self.resource._apply_date_filters(
            query,
            func.date(Cliente.ultima_fecha_compra),
            ultima_venta_col,
            None,
            '2025-01-01',
            '2025-02-28'
        )
        results = filtered.all()
        names = {r.nombre for r in results}
        self.assertIn('Alice', names)
        self.assertIn('Bob', names)

    def test_invalid_date_range_raises(self):
        with self.assertRaises(ValueError):
            self.resource._parse_date_args({
                'fecha_desde': '2025-02-01',
                'fecha_hasta': '2025-01-01'
            })


if __name__ == '__main__':
    unittest.main()