import os
import time
import click
from flask.cli import with_appcontext
from extensions import db, supabase
from models import (
    Venta, Cliente, Producto, VentaDetalle, Gasto, Pago, Movimiento, 
    Inventario, Lote, Merma, Proveedor, Pedido, PedidoDetalle, 
    PresentacionProducto, Almacen, Users
)
import google.generativeai as genai
from sqlalchemy.orm import joinedload

# Configuración del modelo de embedding (debe ser la misma que en chat_resource.py)
EMBEDDING_MODEL = "models/text-embedding-004"

def get_embedding(text):
    """Genera un embedding para un texto dado."""
    if not text:
        return None
    text = text.replace("\n", " ")
    try:
        return genai.embed_content(model=EMBEDDING_MODEL, content=text)['embedding']
    except Exception as e:
        print(f"Error al generar embedding: {e}")
        # Simple backoff
        time.sleep(1)
        return None

def format_venta_for_embedding(venta):
    """Formatea un objeto Venta a un string de texto para el embedding."""
    detalles_str = ", ".join([
        f"{d.cantidad} de {d.presentacion.nombre if d.presentacion else 'N/A'} a ${d.precio_unitario:.2f} cada uno"
        for d in venta.detalles
    ])
    fecha_str = venta.fecha.strftime('%Y-%m-%d') if venta.fecha else 'N/A'
    cliente_nombre = venta.cliente.nombre if venta.cliente else 'N/A'
    almacen_nombre = venta.almacen.nombre if venta.almacen else 'N/A'
    return (
        f"Venta ID {venta.id} realizada el {fecha_str} "
        f"al cliente '{cliente_nombre}' en el almacén '{almacen_nombre}'. "
        f"Total: ${venta.total:.2f}. Estado de pago: {venta.estado_pago}. "
        f"Detalles: {detalles_str}."
    )

def format_cliente_for_embedding(cliente):
    """Formatea un objeto Cliente a un string de texto para el embedding."""
    saldo_str = f"Actualmente tiene una deuda de ${cliente.saldo_pendiente:.2f}." if cliente.saldo_pendiente > 0 else "No tiene deudas pendientes."
    ultima_compra_str = f"Su última compra fue el {cliente.ultima_fecha_compra.strftime('%Y-%m-%d')}." if cliente.ultima_fecha_compra else "No ha realizado compras."
    return (
        f"Cliente: {cliente.nombre}, de {cliente.ciudad or 'N/A'}. Teléfono: {cliente.telefono or 'N/A'}. "
        f"{saldo_str} {ultima_compra_str} Frecuencia de compra: {cliente.frecuencia_compra_dias or 'N/A'} días."
    )

def format_producto_for_embedding(producto):
    """Formatea un objeto Producto y sus presentaciones a un string de texto."""
    presentaciones_str = ", ".join([
        f"{p.nombre} ({p.capacidad_kg} kg) a ${p.precio_venta:.2f}"
        for p in producto.presentaciones
    ])
    return (
        f"Producto ID {producto.id}: {producto.nombre}. Descripción: {producto.descripcion}. "
        f"Precio de compra: ${producto.precio_compra:.2f}. "
        f"Presentaciones disponibles: {presentaciones_str}."
    )

def format_gasto_for_embedding(gasto):
    """Formatea un objeto Gasto a un string de texto para el embedding."""
    usuario_nombre = gasto.usuario.username if gasto.usuario else 'N/A'
    almacen_nombre = gasto.almacen.nombre if gasto.almacen else 'N/A'
    fecha_str = gasto.fecha.strftime('%Y-%m-%d') if gasto.fecha else 'N/A'
    return (
        f"Gasto ID {gasto.id} registrado el {fecha_str} en la categoría '{gasto.categoria}'. "
        f"Monto: ${gasto.monto:.2f}. Descripción: {gasto.descripcion}. "
        f"Registrado por '{usuario_nombre}' en el almacén '{almacen_nombre}'."
    )

def format_pago_for_embedding(pago):
    """Formatea un objeto Pago a un string de texto para el embedding."""
    usuario_nombre = pago.usuario.username if pago.usuario else 'N/A'
    fecha_str = pago.fecha.strftime('%Y-%m-%d') if pago.fecha else 'N/A'
    return (
        f"Pago ID {pago.id} de ${pago.monto:.2f} para la venta ID {pago.venta_id} el {fecha_str}. "
        f"Método de pago: {pago.metodo_pago}. Registrado por '{usuario_nombre}'."
    )

def format_movimiento_for_embedding(movimiento):
    """Formatea un objeto Movimiento a un string de texto para el embedding."""
    usuario_nombre = movimiento.usuario.username if movimiento.usuario else 'N/A'
    presentacion_nombre = movimiento.presentacion.nombre if movimiento.presentacion else 'N/A'
    fecha_str = movimiento.fecha.strftime('%Y-%m-%d') if movimiento.fecha else 'N/A'
    return (
        f"Movimiento de inventario ID {movimiento.id} del tipo '{movimiento.tipo}'. "
        f"Fecha: {fecha_str}. Cantidad: {movimiento.cantidad} unidades de '{presentacion_nombre}'. "
        f"Motivo: {movimiento.motivo}. Realizado por '{usuario_nombre}'."
    )

def format_inventario_for_embedding(item):
    """Formatea un objeto Inventario a un string de texto para el embedding."""
    presentacion_nombre = item.presentacion.nombre if item.presentacion else 'N/A'
    almacen_nombre = item.almacen.nombre if item.almacen else 'N/A'
    return (
        f"Registro de inventario ID {item.id}: Hay {item.cantidad} unidades de '{presentacion_nombre}' "
        f"en el almacén '{almacen_nombre}'. Stock mínimo establecido en {item.stock_minimo} unidades."
    )

def format_lote_for_embedding(lote):
    """Formatea un objeto Lote a un string de texto para el embedding."""
    producto_nombre = lote.producto.nombre if lote.producto else 'N/A'
    proveedor_nombre = lote.proveedor.nombre if lote.proveedor else 'N/A'
    fecha_ingreso_str = lote.fecha_ingreso.strftime('%Y-%m-%d') if lote.fecha_ingreso else 'N/A'
    return (
        f"Lote ID {lote.id} de '{producto_nombre}' del proveedor '{proveedor_nombre}'. "
        f"Ingresó el {fecha_ingreso_str} con {lote.peso_humedo_kg} kg húmedos. "
        f"Peso seco: {lote.peso_seco_kg} kg. Disponible: {lote.cantidad_disponible_kg} kg."
    )

def format_merma_for_embedding(merma):
    """Formatea un objeto Merma a un string de texto para el embedding."""
    fecha_registro_str = merma.fecha_registro.strftime('%Y-%m-%d') if merma.fecha_registro else 'N/A'
    return (
        f"Merma ID {merma.id} del lote {merma.lote_id} registrada el {fecha_registro_str}. "
        f"Cantidad: {merma.cantidad_kg} kg. Convertido a briquetas: {'sí' if merma.convertido_a_briquetas else 'no'}."
    )

def format_proveedor_for_embedding(proveedor):
    """Formatea un objeto Proveedor a un string de texto para el embedding."""
    return (
        f"Proveedor ID {proveedor.id}: {proveedor.nombre}. Teléfono: {proveedor.telefono}. Dirección: {proveedor.direccion}."
    )

def format_pedido_for_embedding(pedido):
    """Formatea un objeto Pedido a un string de texto para el embedding."""
    cliente_nombre = pedido.cliente.nombre if pedido.cliente else 'N/A'
    almacen_nombre = pedido.almacen.nombre if pedido.almacen else 'N/A'
    vendedor_nombre = pedido.vendedor.username if pedido.vendedor else 'N/A'
    fecha_entrega_str = pedido.fecha_entrega.strftime('%Y-%m-%d') if pedido.fecha_entrega else 'N/A'
    fecha_creacion_str = pedido.fecha_creacion.strftime('%Y-%m-%d') if pedido.fecha_creacion else 'N/A'
    detalles_str = ", ".join([
        f"{d.cantidad} de {d.presentacion.nombre if d.presentacion else 'N/A'} a ${d.precio_estimado:.2f} cada uno"
        for d in pedido.detalles
    ])
    return (
        f"Pedido ID {pedido.id} para '{cliente_nombre}' a entregar el {fecha_entrega_str}. "
        f"Creado el {fecha_creacion_str} desde '{almacen_nombre}' por '{vendedor_nombre}'. "
        f"Estado: {pedido.estado}. Total estimado: ${getattr(pedido, 'total_estimado', 0.00):.2f}. Detalles: {detalles_str}."
    )

def format_presentacion_producto_for_embedding(presentacion):
    """Formatea un objeto PresentacionProducto a un string de texto para el embedding."""
    producto_nombre = presentacion.producto.nombre if presentacion.producto else 'N/A'
    return (
        f"Presentación de producto ID {presentacion.id}: '{presentacion.nombre}' para el producto '{producto_nombre}'. "
        f"Tipo: {presentacion.tipo}. Capacidad: {presentacion.capacidad_kg} kg. Precio de venta: ${presentacion.precio_venta:.2f}."
    )

def format_almacen_for_embedding(almacen):
    """Formatea un objeto Almacen a un string de texto para el embedding."""
    return (
        f"Almacén ID {almacen.id}: '{almacen.nombre}', ubicado en {almacen.direccion or 'N/A'}, {almacen.ciudad or 'N/A'}."
    )


MODEL_FORMATTERS = {
    'venta': {
        'model': Venta,
        'formatter': format_venta_for_embedding,
        'options': [
            joinedload(Venta.cliente),
            joinedload(Venta.almacen),
            joinedload(Venta.detalles).joinedload(VentaDetalle.presentacion)
        ]
    },
    'cliente': {
        'model': Cliente,
        'formatter': format_cliente_for_embedding,
        'options': []
    },
    'producto': {
        'model': Producto,
        'formatter': format_producto_for_embedding,
        'options': [joinedload(Producto.presentaciones)]
    },
    'gasto': {
        'model': Gasto,
        'formatter': format_gasto_for_embedding,
        'options': [joinedload(Gasto.usuario), joinedload(Gasto.almacen)]
    },
    'pago': {
        'model': Pago,
        'formatter': format_pago_for_embedding,
        'options': [joinedload(Pago.usuario)]
    },
    'movimiento': {
        'model': Movimiento,
        'formatter': format_movimiento_for_embedding,
        'options': [joinedload(Movimiento.presentacion), joinedload(Movimiento.usuario)]
    },
    'inventario': {
        'model': Inventario,
        'formatter': format_inventario_for_embedding,
        'options': [joinedload(Inventario.presentacion), joinedload(Inventario.almacen)]
    },
    'lote': {
        'model': Lote,
        'formatter': format_lote_for_embedding,
        'options': [joinedload(Lote.producto), joinedload(Lote.proveedor)]
    },
    'merma': {
        'model': Merma,
        'formatter': format_merma_for_embedding,
        'options': []
    },
    'proveedor': {
        'model': Proveedor,
        'formatter': format_proveedor_for_embedding,
        'options': []
    },
    'pedido': {
        'model': Pedido,
        'formatter': format_pedido_for_embedding,
        'options': [
            joinedload(Pedido.cliente),
            joinedload(Pedido.detalles).joinedload(PedidoDetalle.presentacion)
        ]
    },
    'presentacionproducto': {
        'model': PresentacionProducto,
        'formatter': format_presentacion_producto_for_embedding,
        'options': [joinedload(PresentacionProducto.producto)]
    },
    'almacen': {
        'model': Almacen,
        'formatter': format_almacen_for_embedding,
        'options': []
    },
}


@click.command('sync-supabase')
@with_appcontext
@click.argument('models', nargs=-1)
def sync_supabase_command(models):
    """Sincroniza los datos de los modelos especificados a Supabase."""
    if not models:
        models = MODEL_FORMATTERS.keys()

    for model_name in models:
        if model_name not in MODEL_FORMATTERS:
            print(f"Modelo '{model_name}' no reconocido. Modelos disponibles: {list(MODEL_FORMATTERS.keys())}")
            continue

        print(f"--- Sincronizando modelo: {model_name} ---")
        
        config = MODEL_FORMATTERS[model_name]
        query = db.session.query(config['model'])
        if config['options']:
            query = query.options(*config['options'])
        
        records = query.all()
        
        documents_to_upsert = []
        for i, record in enumerate(records):
            # Añadir un pequeño retardo para evitar la limitación de velocidad de la API de embedding
            if i > 0 and i % 10 == 0: # Pausa cada 10 registros
                print("Haciendo una pausa de 5 segundos para evitar la limitación de velocidad...")
                time.sleep(5)

            content = config['formatter'](record)
            embedding = get_embedding(content)
            
            if content and embedding:
                documents_to_upsert.append({
                    'content': content,
                    'embedding': embedding,
                    'source': model_name, # Para saber de qué tabla vino
                    'record_id': record.id # Para futuras actualizaciones
                })
                print(f"  - Preparado: {model_name} ID {record.id}")

        if documents_to_upsert:
            try:
                print(f"Insertando {len(documents_to_upsert)} documentos en Supabase...")
                # Asegúrate de que tu tabla en Supabase se llame 'documents'
                supabase.table('documents').upsert(documents_to_upsert).execute()
                print(f"¡Sincronización de {model_name} completada!")
            except Exception as e:
                print(f"Error al insertar datos en Supabase para {model_name}: {e}")
        else:
            print(f"No hay documentos para sincronizar para el modelo {model_name}.")


def add_commands(app):
    app.cli.add_command(sync_supabase_command)