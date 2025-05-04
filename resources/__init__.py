from .almacen_resource import AlmacenResource
from .auth_resource import AuthResource
from .cliente_resource import ClienteResource, ClienteFormDataResource
from .gasto_resource import GastoResource
from .inventario_resource import InventarioResource
from .lote_resource import LoteResource
from .merma_resource import MermaResource
from .movimiento_resource import MovimientoResource
from .pago_resource import PagoResource
from .presentacion_resource import PresentacionResource
from .producto_resource import ProductoResource
from .proveedor_resource import ProveedorResource
from .user_resource import UserResource
from .venta_resource import VentaResource, VentaFormDataResource
from .ventadetalle_resource import VentaDetalleResource
from .pedido_resource import PedidoResource, PedidoFormDataResource
from .deposito_bancario_resource import DepositoBancarioResource

__all__ = [
    'AuthResource',
    'UserResource',
    'ProductoResource',
    'AlmacenResource',
    'ClienteResource',
    'ClienteFormDataResource',
    'GastoResource',
    'MovimientoResource',
    'VentaResource',
    'VentaFormDataResource',
    'InventarioResource',
    'LoteResource',
    'MermaResource',
    'PresentacionResource',
    'ProveedorResource',
    'VentaDetalleResource',
    'PagoResource',
    'PedidoResource',
    'PedidoFormDataResource',
    'DepositoBancarioResource'
]

def init_resources(api):
    # Autenticación y Usuarios
    api.add_resource(AuthResource, '/auth')
    api.add_resource(UserResource, '/users', '/users/<int:user_id>')

    # Recursos Principales
    api.add_resource(ProductoResource, '/productos', '/productos/<int:producto_id>')
    api.add_resource(PresentacionResource, '/presentaciones', '/presentaciones/<int:presentacion_id>')
    api.add_resource(AlmacenResource, '/almacenes', '/almacenes/<int:almacen_id>')
    api.add_resource(ClienteResource, '/clientes', '/clientes/<int:cliente_id>')
    api.add_resource(ClienteFormDataResource, '/clientes/form-data')
    api.add_resource(ProveedorResource, '/proveedores', '/proveedores/<int:proveedor_id>')
    api.add_resource(LoteResource, '/lotes', '/lotes/<int:lote_id>')
    api.add_resource(InventarioResource, '/inventario', '/inventario/<int:inventario_id>')
    api.add_resource(MovimientoResource, '/movimientos', '/movimientos/<int:movimiento_id>')
    api.add_resource(VentaResource, '/ventas', '/ventas/<int:venta_id>')
    api.add_resource(VentaFormDataResource, '/ventas/form-data')
    api.add_resource(VentaDetalleResource, '/venta_detalles', '/venta_detalles/<int:detalle_id>')
    api.add_resource(PagoResource, '/pagos', '/pagos/<int:pago_id>')
    api.add_resource(GastoResource, '/gastos', '/gastos/<int:gasto_id>')
    api.add_resource(MermaResource, '/mermas', '/mermas/<int:merma_id>')
    api.add_resource(PedidoResource, '/pedidos', '/pedidos/<int:pedido_id>')
    api.add_resource(PedidoFormDataResource, '/pedidos/form-data')
    api.add_resource(DepositoBancarioResource, '/depositos', '/depositos/<int:deposito_id>')