import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_restful import Api
from flask_talisman import Talisman

from scripts.offline_test_env import should_skip_dotenv, validate_testing_database_url

# Cargar variables de entorno ANTES de importar extensiones
# Esto es crítico para que extensiones.py pueda leer las variables
if not should_skip_dotenv():
    env_file = '.env.production' if os.environ.get('FLASK_ENV') == 'production' else '.env'
    load_dotenv(env_file)

# Importar extensiones y recursos
from common import AUTH_VERSION_CLAIM
from extensions import db, jwt, migrate, swagger
from models import Users
from resources import init_resources
from scripts.sync_supabase import sync_supabase_command

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Verificar entorno
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
IS_PRODUCTION = FLASK_ENV == 'production'
logger.info(f"🔧 Entorno: {FLASK_ENV}")
logger.info(f"🚀 Modo producción: {IS_PRODUCTION}")


def _leer_limite_ventas(nombre, defecto, maximo=1_000_000):
    """Lee un tope de ventas configurable, rechazando valores fuera de rango."""
    crudo = os.environ.get(nombre)
    if crudo is None or not str(crudo).strip():
        return defecto
    try:
        valor = int(str(crudo).strip())
    except (TypeError, ValueError):
        raise RuntimeError(
            f"{nombre} debe ser un entero positivo (configuración de límites de ventas)"
        ) from None
    if not 1 <= valor <= maximo:
        raise RuntimeError(
            f"{nombre} debe estar entre 1 y {maximo} (configuración de límites de ventas)"
        )
    return valor


def _resolver_storage_limiter(env=None):
    """Devuelve el backend configurado para Flask-Limiter.

    Se prioriza ``LIMITER_STORAGE_URI`` por compatibilidad con el despliegue
    existente y se acepta ``RATELIMIT_STORAGE_URI`` como nombre nativo de
    Flask-Limiter.
    """
    actual = os.environ if env is None else env
    valor = (
        actual.get('LIMITER_STORAGE_URI')
        or actual.get('RATELIMIT_STORAGE_URI')
        or 'memory://'
    )
    return str(valor).strip()


def _validar_storage_limiter(storage_uri, *, is_production, flask_env):
    """Exige Redis en producción y evita un fallback silencioso a memoria."""
    uri = str(storage_uri or '').strip()
    if not uri:
        uri = 'memory://'
    if is_production:
        esquema = uri.split('://', 1)[0].lower() if '://' in uri else ''
        if esquema not in {'redis', 'rediss'}:
            raise RuntimeError(
                "En producción LIMITER_STORAGE_URI/RATELIMIT_STORAGE_URI debe "
                "apuntar a Redis (redis:// o rediss://). No se permite memoria "
                "ni fallback silencioso con varios workers."
            )
    elif flask_env == 'testing' and uri.startswith('memory://'):
        # Memoria permitida solo porque FLASK_ENV=testing fue configurado explícitamente.
        pass
    return uri

# Validar configuraciones críticas al arranque (SEG-01, SEG-02, SEG-07)
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    raise RuntimeError("DATABASE_URL es obligatoria (ver .env.example)")

if should_skip_dotenv():
    validate_testing_database_url(db_url)

jwt_secret = os.environ.get('JWT_SECRET_KEY')
if not jwt_secret or len(jwt_secret) < 32:
    raise RuntimeError("JWT_SECRET_KEY es obligatoria y debe tener al menos 32 caracteres")

if IS_PRODUCTION and os.environ.get('FLASK_DEBUG') == '1':
    raise RuntimeError("No se permite ejecutar en modo DEBUG (FLASK_DEBUG=1) en producción (SEG-07)")

app = Flask(__name__)

# Configuración de CORS (SEG-06)
raw_origins = os.environ.get('ALLOWED_ORIGINS', '')
origins = [o.strip() for o in raw_origins.split(',') if o.strip()]

if IS_PRODUCTION:
    if not origins:
        raise RuntimeError("ALLOWED_ORIGINS es obligatoria en producción (CORS cerrado)")
    logger.info(f"CORS configurado para orígenes: {origins}")
    CORS(app, resources={r"/*": {"origins": origins}})
else:
    # Para desarrollo permitimos localhost por defecto si no se especifican orígenes
    if not origins:
        origins = ["http://localhost:5173"]
    logger.info(f"CORS configurado para desarrollo, permitiendo orígenes: {origins}")
    CORS(app, resources={r"/*": {"origins": origins}})

# Configuración de la base de datos
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configurar options del engine de forma condicional (evitar pooling en SQLite de pruebas)
if "sqlite" in app.config['SQLALCHEMY_DATABASE_URI']:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True
    }
else:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': int(os.environ.get('DB_POOL_SIZE', 5)),
        'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', 10)),
        'pool_recycle': int(os.environ.get('DB_POOL_RECYCLE', 300)),
        'pool_pre_ping': True,
    }

# Configuración S3
app.config['S3_BUCKET'] = os.environ.get('S3_BUCKET')
app.config['S3_REGION'] = os.environ.get('AWS_REGION')

if not app.config['S3_BUCKET'] or not app.config['S3_REGION']:
    if IS_PRODUCTION:
        logger.error("Configuración de S3 requerida en producción.")
    else:
        logger.info("S3 no configurado - usando almacenamiento local para desarrollo.")

# Configuración de Archivos
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 50 * 1024 * 1024))
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'pdf'}

# Configuración JWT
jwt_expires_str = os.environ.get('JWT_EXPIRES_SECONDS', '43200')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = int(jwt_expires_str.split('#')[0].strip())
app.config['JWT_ALGORITHM'] = 'HS256'
app.config['JWT_SECRET_KEY'] = jwt_secret
app.config['JWT_BLACKLIST_ENABLED'] = True
app.config['PROPAGATE_EXCEPTIONS'] = True

# Configuración Limiter
limiter_storage_uri = _resolver_storage_limiter()
app.config['RATELIMIT_STORAGE_URI'] = _validar_storage_limiter(
    limiter_storage_uri,
    is_production=IS_PRODUCTION,
    flask_env=FLASK_ENV,
)
app.config['RATELIMIT_STRATEGY'] = 'fixed-window'
app.config['RATELIMIT_SWALLOW_ERRORS'] = False
app.config['RATELIMIT_IN_MEMORY_FALLBACK_ENABLED'] = False
app.config['RATELIMIT_IN_MEMORY_FALLBACK'] = None
if FLASK_ENV == 'testing':
    app.config['RATELIMIT_ENABLED'] = False

# Topes de volumen para respuestas masivas de ventas
app.config['MAX_VENTAS_ALL'] = _leer_limite_ventas('MAX_VENTAS_ALL', 1000)
app.config['MAX_VENTAS_EXPORT'] = _leer_limite_ventas('MAX_VENTAS_EXPORT', 5000)

# Configuración Swagger
app.config['SWAGGER'] = {
    'title': 'API Manngo',
    'uiversion': 3
}

# Inicializar extensiones
db.init_app(app)
migrate.init_app(app, db)
jwt.init_app(app)


def _entero_positivo_o_none(valor):
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ValueError("booleano no válido")
    if isinstance(valor, int):
        if valor > 0:
            return valor
        raise ValueError("entero no válido")
    if isinstance(valor, str) and valor.isascii() and valor.isdigit():
        numero = int(valor)
        if numero > 0:
            return numero
    raise ValueError("ID no válido")


@jwt.token_in_blocklist_loader
def token_revocado_callback(jwt_header, jwt_payload):
    """Revoca tokens de usuarios borrados o cuyo estado de autorización cambió."""
    identity = jwt_payload.get('sub')
    try:
        usuario_id = _entero_positivo_o_none(identity)
    except ValueError:
        return True
    if usuario_id is None:
        return True

    usuario = db.session.get(Users, usuario_id)
    if usuario is None:
        return True

    version_actual = int(usuario.auth_version or 0)
    version_token = jwt_payload.get(AUTH_VERSION_CLAIM)
    if version_token is None:
        if version_actual != 0:
            return True
    else:
        if isinstance(version_token, bool):
            return True
        try:
            if int(version_token) != version_actual:
                return True
        except (TypeError, ValueError):
            return True

    if 'username' in jwt_payload and jwt_payload.get('username') != usuario.username:
        return True

    if 'rol' in jwt_payload and jwt_payload.get('rol') != usuario.rol:
        return True

    if 'almacen_id' in jwt_payload:
        try:
            if _entero_positivo_o_none(jwt_payload.get('almacen_id')) != _entero_positivo_o_none(usuario.almacen_id):
                return True
        except ValueError:
            return True

    return False


Compress(app)  # Compresión gzip automática para respuestas
api = Api(app)

# Inicializar Limiter
def get_key_func():
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        if identity:
            return str(identity)
    except Exception:
        pass
    return get_remote_address()

def crear_limiter(target_app):
    """Crea el Limiter leyendo el backend efectivo desde la configuración."""
    return Limiter(
        key_func=get_key_func,
        app=target_app,
        default_limits=[os.environ.get('DEFAULT_RATE_LIMIT', '200 per day;50 per hour')],
    )


limiter = crear_limiter(app)

# Verificar el backend efectivo del limiter (sin abrir conexiones)
if IS_PRODUCTION:
    from limits.storage import MemoryStorage

    if limiter._storage is None or isinstance(limiter._storage, MemoryStorage):
        raise RuntimeError(
            "Flask-Limiter no tiene un backend Redis efectivo en producción. "
            "Configura LIMITER_STORAGE_URI/RATELIMIT_STORAGE_URI con redis:// o rediss://."
        )
    logger.info("Limiter backend efectivo: %s", type(limiter._storage).__name__)

# Configurar Talisman
talisman = Talisman(
    app,
    content_security_policy={
        'default-src': '\'self\'',
        'img-src': ['*', 'data:'],
        'script-src': '\'self\'',
        'style-src': ['\'self\'', '\'unsafe-inline\''],
    },
    content_security_policy_nonce_in=['script-src'],
    force_https=IS_PRODUCTION,
    strict_transport_security=IS_PRODUCTION,
    session_cookie_secure=IS_PRODUCTION,
    session_cookie_http_only=True
)

# CLI Commands
app.cli.add_command(sync_supabase_command)

# JWT Error Handling
@jwt.unauthorized_loader
def unauthorized_callback(callback):
    return jsonify({'message': 'Se requiere autenticación', 'error': 'authorization_required'}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({'message': 'El token ha expirado', 'error': 'token_expired'}), 401

@jwt.invalid_token_loader
def invalid_token_callback(error):
    return jsonify({'message': 'Verificación de firma fallida', 'error': 'invalid_token'}), 401

@jwt.revoked_token_loader
def revoked_token_callback(jwt_header, jwt_payload):
    return jsonify({'message': 'El token ya no es válido', 'error': 'token_revoked'}), 401

# Error Handlers
@app.errorhandler(500)
def handle_internal_server_error(e):
    logger.exception(f"Internal server error: {e}")
    return jsonify({"error": "Ocurrió un error interno del servidor"}), 500

@app.errorhandler(404)
def handle_not_found_error(e):
    return jsonify({"error": "Recurso no encontrado"}), 404

@app.errorhandler(405)
def handle_method_not_allowed(e):
    return jsonify({"error": "Método no permitido"}), 405

@app.errorhandler(413)
def handle_request_entity_too_large(e):
    return jsonify({
        "error": "Request Entity Too Large",
        "message": "El archivo excede el límite permitido"
    }), 413

# Health Check
@app.route('/health')
@limiter.exempt
def health_check():
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify({"status": "ok", "database": "connected"}), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "database": "disconnected"}), 503

# Config Info
@app.route('/config')
@limiter.exempt
def config_info():
    if IS_PRODUCTION:
        return jsonify({"error": "Endpoint no disponible en producción"}), 404
    return jsonify({
        "flask_env": FLASK_ENV,
        "is_production": IS_PRODUCTION,
        "database_type": "sqlite" if "sqlite" in app.config['SQLALCHEMY_DATABASE_URI'] else "postgresql",
        "rate_limit": os.environ.get('DEFAULT_RATE_LIMIT')
    }), 200

# Registrar Recursos con Contexto
with app.app_context():
    init_resources(api, limiter=limiter)
    swagger.init_app(app)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG') == '1')
