from flask import request
from flask_restful import Resource
import google.generativeai as genai
from extensions import supabase # Importar cliente de Supabase centralizado

# El cliente de genai se configura en extensions.py, solo lo usamos aquí

EMBEDDING_MODEL = "models/text-embedding-004"
GENERATION_MODEL = "gemini-2.5-flash"

def get_embedding(text):
    text = text.replace("\n", " ")
    return genai.embed_content(model=EMBEDDING_MODEL, content=text)['embedding']

class ChatResource(Resource):
    def post(self):
        data = request.get_json()
        user_question = data.get('question')

        if not user_question:
            return {"error": "No se proporcionó ninguna pregunta."}, 400

        try:
            # 1. Generar embedding para la pregunta del usuario
            query_embedding = get_embedding(user_question)

            # 2. Buscar documentos relevantes en Supabase (vector size 768)
            matches = supabase.rpc('match_documents', {
                'query_embedding': query_embedding,
                'match_threshold': 0.6,  # Umbral de similitud, ajústalo según tus pruebas
                'match_count': 10
            }).execute()

            context_text = ""
            if matches.data:
                context_text = "\n---\n".join([match['content'] for match in matches.data])
            else:
                # Si no hay coincidencias, aún podemos intentar responder
                context_text = "No se encontró información específica en la base de datos."

            print("----- CONTEXTO RECUPERADO -----")
            print(context_text)
            print("-------------------------------")

            # 3. Construir el prompt para Gemini
            prompt = f"""
            Basado en el siguiente contexto, responde la pregunta del usuario.
            Si la respuesta no se encuentra en el contexto, di que no tienes la información.

            Contexto:
            {context_text}

            Pregunta del usuario:
            {user_question}

            Respuesta:
            """

            print("----- PROMPT PARA GEMINI -----")
            print(prompt)
            print("-----------------------------")

            # 4. Llamar al modelo Gemini para generar la respuesta
            model = genai.GenerativeModel(GENERATION_MODEL)
            response = model.generate_content(prompt)

            return {"answer": response.text}, 200

        except Exception as e:
            print(f"Error en el endpoint /chat: {e}")
            return {"error": "Ocurrió un error interno en el servidor."}, 500