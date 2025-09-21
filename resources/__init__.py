from .almacen_resource import AlmacenResource
from .auth_resource import AuthResource
from .chat_resource import ChatResource
from .cliente_resource import ClienteExportResource, ClienteResource, ClienteProyeccionResource, ClienteProyeccionExportResource
from .dashboard_resource import DashboardResource
from .gasto_resource import GastoResource, GastoExportResource
from .produccion_resource import ProduccionResource, ProduccionEnsamblajeResource
from .inventario_resource import InventarioResource, InventarioGlobalResource
from .transferencia_resource import TransferenciaInventarioResource # <-- Importado desde el nuevo archivo
from .lote_resource import LoteResource
from .merma_resource import MermaResource
from .movimiento_resource import MovimientoResource
from .pago_resource import PagoResource, PagosPorVentaResource, PagoBatchResource, DepositoBancarioResource as PagoDepositoBancarioResource, PagoExportResource, CierreCajaResource
from .pedido_resource import PedidoResource, PedidoConversionResource, PedidoFormDataResource
from .presentacion_resource import PresentacionResource
from .producto_resource import ProductoResource
from .proveedor_resource import ProveedorResource
from .receta_resource import RecetaResource
from .reporte_financiero_resource import ReporteVentasPresentacionResource, ResumenFinancieroResource
from .reporte_produccion_resource import ReporteProduccionBriquetasResource, ReporteProduccionGeneralResource
from .user_resource import UserResource
from .venta_resource import VentaResource, VentaFormDataResource, VentaExportResource, VentaFilterDataResource
from .ventadetalle_resource import VentaDetalleResource

__all__ = [
    'AlmacenResource',
    'AuthResource',
    'ChatResource',
    'ClienteExportResource',
    'ClienteProyeccionResource',
    'ClienteProyeccionExportResource',
    'ClienteResource',
    'DashboardResource',
    'GastoResource',
    'GastoExportResource',
    'InventarioResource',
    'InventarioGlobalResource',
    'TransferenciaInventarioResource',
    'LoteResource',
    'MermaResource',
    'MovimientoResource',
    'PagoResource',
    'PagosPorVentaResource',
    'PagoBatchResource',
    'PagoDepositoBancarioResource',
    'PagoExportResource',
    'CierreCajaResource',
    'PedidoResource',
    'PedidoConversionResource',
    'PedidoFormDataResource',
    'PresentacionResource',
    'ProduccionEnsamblajeResource',
    'ProduccionResource',
    'ProductoResource',
    'ProveedorResource',
    'RecetaResource',
    'ReporteVentasPresentacionResource',
    'ResumenFinancieroResource',
    'ReporteProduccionBriquetasResource',
    'ReporteProduccionGeneralResource',
    'UserResource',
    'VentaResource',
    'VentaFormDataResource',
    'VentaExportResource',
    'VentaFilterDataResource',
    'VentaDetalleResource',
]

def init_resources(api):
    # Autenticación y Usuarios
    api.add_resource(AuthResource, '/auth')
    api.add_resource(UserResource, '/usuarios', '/usuarios/<int:user_id>')
    
    # Recursos Principales
    api.add_resource(ProductoResource, '/productos', '/productos/<int:producto_id>')
    api.add_resource(PresentacionResource, '/presentaciones', '/presentaciones/<int:presentacion_id>')
    api.add_resource(AlmacenResource, '/almacenes', '/almacenes/<int:almacen_id>')
    api.add_resource(ClienteResource, '/clientes', '/clientes/<int:cliente_id>')
    api.add_resource(ClienteProyeccionResource, '/clientes/proyecciones', '/clientes/proyecciones/<int:cliente_id>')
    api.add_resource(ClienteExportResource, '/clientes/exportar')
    api.add_resource(ClienteProyeccionExportResource, '/clientes/proyecciones/exportar')
    api.add_resource(ProveedorResource, '/proveedores', '/proveedores/<int:proveedor_id>')
    api.add_resource(LoteResource, '/lotes', '/lotes/<int:lote_id>')
    
    # Inventario y Movimientos
    api.add_resource(InventarioResource, '/inventarios', '/inventarios/<int:inventario_id>')
    api.add_resource(InventarioGlobalResource, '/inventario/reporte-global')
    api.add_resource(TransferenciaInventarioResource, '/inventario/transferir')

    api.add_resource(MovimientoResource, '/movimientos', '/movimientos/<int:movimiento_id>')
    
    # Ventas
    api.add_resource(VentaResource, '/ventas', '/ventas/<int:venta_id>')
    api.add_resource(VentaFormDataResource, '/ventas/form-data')
    api.add_resource(VentaDetalleResource, '/ventas/<int:venta_id>/detalles')
    api.add_resource(VentaExportResource, '/ventas/exportar')
    api.add_resource(VentaFilterDataResource, '/ventas/filtros')

    # Pagos
    api.add_resource(PagoResource, '/pagos', '/pagos/<int:pago_id>')
    api.add_resource(PagosPorVentaResource, '/pagos/venta/<int:venta_id>')
    api.add_resource(PagoBatchResource, '/pagos/batch')
    api.add_resource(PagoDepositoBancarioResource, '/pagos/depositos')
    api.add_resource(PagoExportResource, '/pagos/exportar')
    api.add_resource(CierreCajaResource, '/pagos/cierrecaja')
    
    # Gastos
    api.add_resource(GastoResource, '/gastos', '/gastos/<int:gasto_id>')
    api.add_resource(GastoExportResource, '/gastos/exportar')
    
    # Otros
    api.add_resource(MermaResource, '/mermas', '/mermas/<int:merma_id>')
    api.add_resource(PedidoResource, '/pedidos', '/pedidos/<int:pedido_id>')
    api.add_resource(PedidoConversionResource, '/pedidos/<int:pedido_id>/convertir')
    api.add_resource(PedidoFormDataResource, '/pedidos/form-data')
    
    # --- Producción y Recetas ---
    # Gestión de Recetas (Admin)
    api.add_resource(RecetaResource, '/recetas', '/recetas/<int:id>')
    # Endpoint principal para registrar producción (para operadores)
    api.add_resource(ProduccionResource, '/produccion')
    # Endpoint de motor interno para ensamblaje (uso interno)
    api.add_resource(ProduccionEnsamblajeResource, '/produccion/ensamblaje')

    # Dashboard y Reportes
    api.add_resource(DashboardResource, '/dashboard')
    api.add_resource(ReporteVentasPresentacionResource, '/reportes/ventas-presentacion')
    api.add_resource(ResumenFinancieroResource, '/reportes/resumen-financiero')
    api.add_resource(ReporteProduccionBriquetasResource, '/reportes/produccion-briquetas')
    api.add_resource(ReporteProduccionGeneralResource, '/reportes/produccion-general')
    
    # Chat
    api.add_resource(ChatResource, '/chat')
