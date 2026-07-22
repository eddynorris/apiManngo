import os
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from supabase import create_client, Client
from flasgger import Swagger

# Crear instancias de extensiones
db = SQLAlchemy()
jwt = JWTManager()
migrate = Migrate()
swagger = Swagger()

# Inicializar cliente de Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
if supabase_url and supabase_key:
    supabase: Client = create_client(supabase_url, supabase_key)
else:
    supabase = None

# Nota: Google Generative AI (genai) se configura en services/gemini_service.py
# para evitar configuración duplicada