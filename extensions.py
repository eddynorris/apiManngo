import os
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from supabase import create_client, Client
import google.generativeai as genai
from flasgger import Swagger

# Crear instancias de extensiones
db = SQLAlchemy()
jwt = JWTManager()
swagger = Swagger()

# Inicializar cliente de Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
if supabase_url and supabase_key:
    supabase: Client = create_client(supabase_url, supabase_key)
else:
    supabase = None

# Inicializar cliente de Google AI
google_api_key = os.getenv("GOOGLE_API_KEY")
if google_api_key:
    genai.configure(api_key=google_api_key)