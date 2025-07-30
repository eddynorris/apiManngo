import os
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from supabase import create_client, Client
import google.generativeai as genai
from dotenv import load_dotenv

# Cargar variables de entorno según el entorno
# Esta lógica busca primero un .env.local y, si no lo encuentra, usa .env
env_file = os.environ.get('ENV_FILE', '.env.local')
if not os.path.exists(env_file):
    env_file = '.env'
load_dotenv(dotenv_path=env_file)

# Crear instancias de extensiones
db = SQLAlchemy()
jwt = JWTManager()

# Inicializar cliente de Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# Inicializar cliente de Google AI
google_api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=google_api_key)