from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
# Crear una única instancia de SQLAlchemy
db = SQLAlchemy()
jwt = JWTManager()