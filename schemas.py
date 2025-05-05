from marshmallow import Schema, fields, EXCLUDE
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema
from models import (
    Users, Producto, Almacen, Cliente, Gasto, Movimiento, 
    Venta, VentaDetalle, Proveedor, Pago, Inventario,
    PresentacionProducto, Lote, Merma, PedidoDetalle, Pedido, DepositoBancario  # Nuevos modelos
)
from extensions import db

# ------------------------- ESQUEMAS BASE -------------------------
class AlmacenSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = Almacen
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 
        exclude = ("inventario", "ventas")  # Excluir relaciones recursivas

class UserSchema(SQLAlchemyAutoSchema):
    almacen = fields.Nested(AlmacenSchema, only=("id", "nombre"))

    class Meta:
        model = Users
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 
        exclude = ("movimientos",)
        include_fk = True

class ProveedorSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = Proveedor
        load_instance = True
        sqla_session = db.session 
        unknown = EXCLUDE


class ProductoSchema(SQLAlchemyAutoSchema):
    presentaciones = fields.List(fields.Nested("PresentacionSchema", exclude=("producto",)), dump_only=True)
    precio_compra = fields.Decimal(as_string=True)

    class Meta:
        model = Producto
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 


class PresentacionSchema(SQLAlchemyAutoSchema):
    producto = fields.Nested("ProductoSchema", only=("id", "nombre"), dump_only=True)
    precio_venta = fields.Decimal(as_string=True)
    capacidad_kg = fields.Decimal(as_string=True)
    foto_url = fields.String(dump_only=True)
    
    class Meta:
        model = PresentacionProducto
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 
        include_fk = True  # Incluir producto_id

class LoteSchema(SQLAlchemyAutoSchema):
    proveedor = fields.Nested(ProveedorSchema, only=("id", "nombre"), dump_only=True)
    producto = fields.Nested(ProductoSchema, only=("id", "nombre"), dump_only=True)
    peso_humedo_kg = fields.Decimal(as_string=True)
    peso_seco_kg = fields.Decimal(as_string=True)
    cantidad_disponible_kg = fields.Decimal(as_string=True)

    class Meta:
        model = Lote
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session
        include_fk = True

class MermaSchema(SQLAlchemyAutoSchema):
    lote = fields.Nested(LoteSchema, only=("id", "cantidad_disponible_kg"), dump_only=True)
    usuario = fields.Nested(UserSchema(only=("id", "username")), dump_only=True)
    cantidad_kg = fields.Decimal(as_string=True)

    class Meta:
        model = Merma
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 
        include_fk = True

class InventarioSchema(SQLAlchemyAutoSchema):
    presentacion = fields.Nested(PresentacionSchema, only=("id", "nombre", "capacidad_kg"))
    almacen = fields.Nested(AlmacenSchema, only=("id", "nombre"))
    lote = fields.Nested(LoteSchema, only=("id", "descripcion", "cantidad_disponible_kg"))

    class Meta:
        model = Inventario
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session
        include_fk = True

class ClienteSchema(SQLAlchemyAutoSchema):
    saldo_pendiente = fields.Decimal(as_string=True, dump_only=True)
    ultima_fecha_compra = fields.DateTime(format="%Y-%m-%d")

    class Meta:
        model = Cliente
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 


class MovimientoSchema(SQLAlchemyAutoSchema):
    presentacion = fields.Nested(PresentacionSchema, only=("id", "nombre"))
    lote = fields.Nested(LoteSchema, only=("id", "cantidad_disponible_kg", "descripcion"))
    usuario = fields.Nested(UserSchema, only=("id", "username"))
    cantidad = fields.Decimal(as_string=True)

    class Meta:
        model = Movimiento
        load_instance = True
        unknown = EXCLUDE
        include_fk = True
        sqla_session = db.session 

class VentaDetalleSchema(SQLAlchemyAutoSchema):
    presentacion = fields.Nested(PresentacionSchema, only=("id", "nombre", "precio_venta", "url_foto"))
    precio_unitario = fields.Decimal(as_string=True)
    total_linea = fields.Decimal(as_string=True, dump_only=True)

    class Meta:
        model = VentaDetalle
        load_instance = True
        unknown = EXCLUDE   
        sqla_session = db.session 
        include_fk = True
        exclude = ("venta_id",)

class VentaSchema(SQLAlchemyAutoSchema):
    cliente = fields.Nested(ClienteSchema, only=("id", "nombre"))
    almacen = fields.Nested(AlmacenSchema, only=("id", "nombre"))
    vendedor = fields.Nested(UserSchema, only=("id", "username"))
    detalles = fields.List(fields.Nested(VentaDetalleSchema))
    consumo_diario_kg = fields.Decimal(as_string=True)
    saldo_pendiente = fields.Decimal(as_string=True, dump_only=True)
    total = fields.Decimal(as_string=True)

    class Meta:
        model = Venta
        load_instance = True
        sqla_session = db.session
        include_relationships = True
        include_fk = True 
        unknown = EXCLUDE

class PagoSchema(SQLAlchemyAutoSchema):
    venta = fields.Nested(VentaSchema, only=("id", "total"), dump_only=True)
    usuario = fields.Nested(UserSchema, only=("id", "username"), dump_only=True)
    monto = fields.Decimal(as_string=True)
    comprobante_url = fields.String(dump_only=True)

    class Meta:
        model = Pago
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session 
        include_fk = True

class GastoSchema(SQLAlchemyAutoSchema):
    almacen = fields.Nested(AlmacenSchema, only=("id", "nombre"))
    usuario = fields.Nested(UserSchema, only=("id", "username"))
    monto = fields.Decimal(as_string=True)


    class Meta:
        model = Gasto
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session
        include_fk = True

class PedidoDetalleSchema(SQLAlchemyAutoSchema):
    presentacion = fields.Nested(PresentacionSchema, only=("id", "nombre", "precio_venta", "url_foto"))
    precio_estimado = fields.Decimal(as_string=True)

    class Meta:
        model = PedidoDetalle
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session
        include_fk = True
        exclude = ("pedido_id",)

class PedidoSchema(SQLAlchemyAutoSchema):
    cliente = fields.Nested(ClienteSchema, only=("id", "nombre"))   
    almacen = fields.Nested(AlmacenSchema, only=("id", "nombre"))
    vendedor = fields.Nested(UserSchema, only=("id", "username"))
    detalles = fields.List(fields.Nested(PedidoDetalleSchema))
    total_estimado = fields.Decimal(as_string=True, dump_only=True)

    class Meta:
        model = Pedido
        load_instance = True
        sqla_session = db.session
        include_relationships = True
        include_fk = True
        unknown = EXCLUDE

class DepositoBancarioSchema(SQLAlchemyAutoSchema):
    almacen = fields.Nested(AlmacenSchema, only=("id", "nombre"))
    usuario = fields.Nested(UserSchema, only=("id", "username"))
    monto_depositado = fields.Decimal(as_string=True)
    comprobante_url = fields.String(dump_only=True)  # Para la URL pre-firmada
    
    class Meta:
        model = DepositoBancario
        load_instance = True
        unknown = EXCLUDE
        sqla_session = db.session
        include_fk = True

# Inicializar esquemas
user_schema = UserSchema()
users_schema = UserSchema(many=True)

presentacion_schema = PresentacionSchema()
presentaciones_schema = PresentacionSchema(many=True)

lote_schema = LoteSchema()
lotes_schema = LoteSchema(many=True)

merma_schema = MermaSchema()
mermas_schema = MermaSchema(many=True)

proveedor_schema = ProveedorSchema()
proveedores_schema = ProveedorSchema(many=True)

producto_schema = ProductoSchema()
productos_schema = ProductoSchema(many=True)

almacen_schema = AlmacenSchema()
almacenes_schema = AlmacenSchema(many=True)

cliente_schema = ClienteSchema()
clientes_schema = ClienteSchema(many=True)

gasto_schema = GastoSchema()
gastos_schema = GastoSchema(many=True)

movimiento_schema = MovimientoSchema()
movimientos_schema = MovimientoSchema(many=True)

venta_schema = VentaSchema()
ventas_schema = VentaSchema(many=True)

pago_schema = PagoSchema()
pagos_schema = PagoSchema(many=True)

venta_detalle_schema = VentaDetalleSchema()
ventas_detalle_schema = VentaDetalleSchema(many=True)

inventario_schema = InventarioSchema()
inventarios_schema = InventarioSchema(many=True)

pedido_schema = PedidoSchema()
pedidos_schema = PedidoSchema(many=True)

pedido_detalle_schema = PedidoDetalleSchema()
pedidos_detalle_schema = PedidoDetalleSchema(many=True)

deposito_bancario_schema = DepositoBancarioSchema()
depositos_bancarios_schema = DepositoBancarioSchema(many=True)