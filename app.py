import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from flask_restful import Api
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from dotenv import load_dotenv
import os

# Cargar variables de entorno ANTES de importar extensiones
# Esto es crítico para que extensiones.py pueda leer las variables
env_file = '.env.production' if os.environ.get('FLASK_ENV') == 'production' else '.env'
load_dotenv(env_file)

# Importar extensiones y recursos
from extensions import db, jwt, swagger
from scripts.sync_supabase import sync_supabase_command
from resources import init_resources

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



app = Flask(__name__)

# Configuración de CORS
allowed_origins = os.environ.get('ALLOWED_ORIGINS', '*')
origins = allowed_origins.split(',') if ',' in allowed_origins else allowed_origins

if IS_PRODUCTION:
    logger.info(f"CORS configurado para orígenes: {origins}")
    CORS(app, resources={r"/*": {"origins": origins}})
else:
    CORS(app, resources={r"/*": {"origins": "*"}})
    logger.info("CORS configurado para desarrollo, permitiendo todos los orígenes ('*').")

# Configuración de la base de datos
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:123456@localhost/manngo_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'insecure-dev-key')
app.config['JWT_BLACKLIST_ENABLED'] = False

if app.config['JWT_SECRET_KEY'] == 'insecure-dev-key' and IS_PRODUCTION:
    raise ValueError("JWT_SECRET_KEY no configurada en producción!")

# Configuración Limiter
app.config['RATELIMIT_STORAGE_URL'] = os.environ.get('LIMITER_STORAGE_URI', 'memory://')
app.config['RATELIMIT_STRATEGY'] = 'fixed-window'

# Configuración Swagger
app.config['SWAGGER'] = {
    'title': 'API Manngo',
    'uiversion': 3
}

# Inicializar extensiones
db.init_app(app)
jwt.init_app(app)
swagger.init_app(app)
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

limiter = Limiter(
    key_func=get_key_func,
    app=app,
    default_limits=[os.environ.get('DEFAULT_RATE_LIMIT', '200 per day;50 per hour')],
)

# Verificar storage limiter
storage_url = app.config.get('RATELIMIT_STORAGE_URL', '')
if 'memory' in storage_url and IS_PRODUCTION:
    logger.warning("Flask-Limiter usando memoria en producción. Considere Redis.")

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
    return jsonify({"status": "ok"}), 200

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=not IS_PRODUCTION)