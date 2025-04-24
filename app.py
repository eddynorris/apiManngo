from flask import Flask, jsonify, send_from_directory
from flask_restful import Api
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
import watchtower
import boto3
from resources.auth_resource import AuthResource, RegisterResource
from resources.producto_resource import ProductoResource
from resources.proveedor_resource import ProveedorResource
from resources.almacen_resource import AlmacenResource
from resources.cliente_resource import ClienteResource
from resources.pago_resource import PagoResource
from resources.gasto_resource import GastoResource
from resources.movimiento_resource import MovimientoResource
from resources.venta_resource import VentaResource
from resources.user_resource import UserResource
from resources.inventario_resource import InventarioResource
from resources.lote_resource import LoteResource
from resources.merma_resource import MermaResource
from resources.presentacion_resource import PresentacionResource
from resources.pedido_resource import PedidoResource, PedidoConversionResource

from extensions import db, jwt
import os
import logging
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Determinar entorno (production, development, etc.)
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
IS_PRODUCTION = FLASK_ENV == 'production'

# Configurar logging
logging.basicConfig(
    level=logging.INFO if IS_PRODUCTION else logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configurar Watchtower para enviar logs a CloudWatch en producción
if IS_PRODUCTION:
    CLOUDWATCH_LOG_GROUP = os.environ.get('CLOUDWATCH_LOG_GROUP')
    AWS_REGION = os.environ.get('AWS_REGION') # Necesario para Watchtower y Boto3
    if CLOUDWATCH_LOG_GROUP and AWS_REGION:
        try:
            # Asegurar credenciales AWS configuradas (variables de entorno o rol IAM)
            boto3_session = boto3.Session(region_name=AWS_REGION)
            # Configurar handler de Watchtower
            watchtower_handler = watchtower.CloudWatchLogHandler(
                log_group_name=CLOUDWATCH_LOG_GROUP,
                boto3_session=boto3_session,
                create_log_group=False # Asume que el grupo ya existe
            )
            # Añadir handler al logger raíz
            logging.getLogger().addHandler(watchtower_handler)
            logger.info(f"Logging configurado para enviar a CloudWatch grupo: {CLOUDWATCH_LOG_GROUP} en región {AWS_REGION}")
        except Exception as e:
            logger.error(f"Error al configurar Watchtower: {e}")
    else:
        logger.warning("Watchtower no configurado: Faltan CLOUDWATCH_LOG_GROUP o AWS_REGION.")

app = Flask(__name__)

# Configuración de CORS - en producción limitar orígenes
allowed_origins = os.environ.get('ALLOWED_ORIGINS', '*' if not IS_PRODUCTION else '')
if not allowed_origins and IS_PRODUCTION:
    logger.warning("ALLOWED_ORIGINS no está configurado para producción. CORS estará desactivado.")
    CORS(app, origins=[]) # Desactivar si no hay orígenes configurados en prod
elif allowed_origins == '*':
    logger.warning("CORS configurado con '*' - Considera limitar orígenes en producción.")
    CORS(app, resources={r"/*": {"origins": "*"}})
else:
    CORS(app, resources={r"/*": {"origins": allowed_origins.split(',')}})

# Configuración de la base de datos desde variables de entorno
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:123456@localhost/manngo_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configuración para S3 (Reemplaza UPLOAD_FOLDER)
app.config['S3_BUCKET'] = os.environ.get('S3_BUCKET')
app.config['S3_REGION'] = os.environ.get('AWS_REGION') # Reutilizar si es la misma región

if not app.config['S3_BUCKET'] or not app.config['S3_REGION']:
    logger.warning("Configuración de S3 incompleta (S3_BUCKET o AWS_REGION). El manejo de archivos podría fallar.")

# Configuración de límite de tamaño de archivo (se mantiene por si se usa en validación)
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024)) # Default 16MB max
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'pdf'} # Mantener para validación

# JWT config con valores seguros
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = int(os.environ.get('JWT_EXPIRES_SECONDS', 43200)) # Default 12 horas
app.config['JWT_ALGORITHM'] = 'HS256'
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY')
app.config['JWT_BLACKLIST_ENABLED'] = False # Considera si realmente necesitas blacklist o usa tokens de corta duración + refresh tokens

# Configuración para Flask-Limiter
app.config['RATELIMIT_STORAGE_URL'] = os.environ.get('LIMITER_STORAGE_URI', 'memory://') # Usar Redis en prod: redis://...
app.config['RATELIMIT_STRATEGY'] = 'fixed-window' # o 'moving-window'

if app.config['JWT_SECRET_KEY'] is None or app.config['JWT_SECRET_KEY'] == 'insecure-key':
    if IS_PRODUCTION:
        raise ValueError("JWT_SECRET_KEY no configurada en producción - ¡Configure una clave segura!")
    else:
        logger.warning("⚠️ Usando clave JWT insegura para desarrollo, no usar en producción")
        app.config['JWT_SECRET_KEY'] = 'insecure-dev-key'

# Inicializar extensiones
db.init_app(app)
jwt.init_app(app)
api = Api(app)

# Configurar Rate Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[os.environ.get('DEFAULT_RATE_LIMIT', '200 per day;50 per hour')],
    # storage_uri se toma de app.config['RATELIMIT_STORAGE_URL']
)

# Verificar la configuración de almacenamiento del limiter
storage_url_check = app.config.get('RATELIMIT_STORAGE_URL', '')
if 'redis' in storage_url_check and IS_PRODUCTION:
    logger.info(f"Flask-Limiter configurado con storage: {storage_url_check}")
elif 'memory' in storage_url_check and IS_PRODUCTION:
    logger.warning("Flask-Limiter está usando almacenamiento en memoria en producción. Considera usar Redis para un estado compartido.")

# Configurar Talisman para Headers de Seguridad (ajustar políticas según necesidad)
talisman = Talisman(
    app,
    content_security_policy={
        'default-src': '\'self\'',
        # Añadir otros dominios si se sirven assets/scripts de CDNs, etc.
        'img-src': ['*' , 'data:'], # Permitir imágenes de cualquier fuente y data URIs
        'script-src': '\'self\'',
        'style-src': ['\'self\'', '\'unsafe-inline\''], # Ajustar si se evita inline styles
    },
    content_security_policy_nonce_in=['script-src'], # Para scripts inline si es necesario
    force_https=IS_PRODUCTION, # Forzar HTTPS en producción
    strict_transport_security=IS_PRODUCTION, # HSTS en producción
    session_cookie_secure=IS_PRODUCTION,
    session_cookie_http_only=True
)
logger.info("Flask-Talisman inicializado.")

# JWT Error handling
@jwt.unauthorized_loader
def unauthorized_callback(callback):
    logger.warning(f"Unauthorized request: {callback}")
    return jsonify({
        'message': 'Se requiere autenticación',
        'error': 'authorization_required'
    }), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    logger.warning(f"Expired token: {jwt_payload}")
    return jsonify({
        'message': 'El token ha expirado',
        'error': 'token_expired'
    }), 401

@jwt.invalid_token_loader
def invalid_token_callback(error):
    logger.error(f"Invalid token: {error}")
    return jsonify({
        'message': 'Verificación de firma fallida',
        'error': 'invalid_token'
    }), 401

@app.errorhandler(500)
def handle_internal_server_error(e):
    logger.exception(f"Internal server error: {e}")
    return jsonify({
        "error": "Ocurrió un error interno del servidor",
        "details": str(e) if os.environ.get('FLASK_ENV') != 'production' else "Contacte al administrador"
    }), 500

@app.errorhandler(404)
def handle_not_found_error(e):
    return jsonify({"error": "Recurso no encontrado"}), 404

@app.errorhandler(405)
def handle_method_not_allowed(e):
    return jsonify({"error": "Método no permitido"}), 405

# Health check endpoint
@app.route('/health')
@limiter.exempt # Eximir health check del rate limiting
def health_check():
    # Podría expandirse para verificar conexión a BD, etc.
    return jsonify({"status": "ok"}), 200

# Registrar recursos (aplicar rate limiting si es necesario)
# Ejemplo de límite específico para login:
# limiter.limit("5 per minute")(AuthResource)
api.add_resource(AuthResource, '/auth')
api.add_resource(RegisterResource, '/registrar')
api.add_resource(UserResource, '/usuarios', '/usuarios/<int:user_id>')
api.add_resource(ProductoResource, '/productos', '/productos/<int:producto_id>')
api.add_resource(PagoResource, '/pagos', '/pagos/<int:pago_id>')
api.add_resource(ProveedorResource, '/proveedores', '/proveedores/<int:proveedor_id>')
api.add_resource(AlmacenResource, '/almacenes', '/almacenes/<int:almacen_id>')
api.add_resource(ClienteResource, '/clientes', '/clientes/<int:cliente_id>')
api.add_resource(GastoResource, '/gastos', '/gastos/<int:gasto_id>')
api.add_resource(MovimientoResource, '/movimientos', '/movimientos/<int:movimiento_id>')
api.add_resource(VentaResource, '/ventas', '/ventas/<int:venta_id>')
api.add_resource(InventarioResource, '/inventarios', '/inventarios/<int:inventario_id>')
api.add_resource(PresentacionResource, '/presentaciones', '/presentaciones/<int:presentacion_id>')
api.add_resource(MermaResource, '/mermas', '/mermas/<int:merma_id>')
api.add_resource(LoteResource, '/lotes', '/lotes/<int:lote_id>')
api.add_resource(PedidoResource, '/pedidos', '/pedidos/<int:pedido_id>')
api.add_resource(PedidoConversionResource, '/pedidos/<int:pedido_id>/convertir')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # El modo debug ya no se controla aquí directamente, sino con FLASK_ENV
    # Gunicorn manejará la ejecución en producción
    app.run(host='0.0.0.0', port=port, debug=not IS_PRODUCTION)