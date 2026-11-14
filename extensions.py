import os

from flasgger import Swagger
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from supabase import Client, create_client

# Crear instancias de extensiones
db = SQLAlchemy()
jwt = JWTManager()
migrate = Migrate()
swagger = Swagger()

# Inicializar cliente de Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
testing_env = os.getenv("FLASK_ENV") == "testing" or os.getenv("MANNGO_OFFLINE_TESTS") == "1"
if not testing_env and supabase_url and supabase_key:
    supabase: Client = create_client(supabase_url, supabase_key)
else:
    supabase = None

# Nota: Google Generative AI (genai) se configura en services/gemini_service.py
# para evitar configuración duplicada
