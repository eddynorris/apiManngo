from .almacen_resource import AlmacenResource
from .auth_resource import AuthResource
from .cliente_resource import ClienteResource
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
from .venta_resource import VentaResource
from .ventadetalle_resource import VentaDetalleResource
from .pedido_resource import PedidoResource
from .pedido_resource import PedidoConversionResource

__all__ = [
    'AuthResource',
    'UserResource',
    'ProductoResource',
    'AlmacenResource',
    'ClienteResource',
    'GastoResource',
    'MovimientoResource',
    'VentaResource',
    'InventarioResource',
    'LoteResource',
    'MermaResource',
    'PresentacionResource',
    'ProveedorResource',
    'VentaDetalleResource',
    'PagoResource',
    'PedidoResource',
    'PedidoConversionResource'
]