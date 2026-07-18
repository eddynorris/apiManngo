import os
import requests
import time
import logging

logger = logging.getLogger(__name__)

class SunatService:
    def __init__(self):
        self.client_id = os.environ.get("MANNGO_ID_SUNAT")
        self.client_secret = os.environ.get("MANNGO_CLAVE_SUNAT")
        self.ruc = os.environ.get("SUNAT_RUC")
        self.usuario_sol = os.environ.get("SUNAT_USUARIO_SOL")
        self.clave_sol = os.environ.get("SUNAT_CLAVE_SOL")
        self.ambiente = os.environ.get("SUNAT_AMBIENTE", "beta").lower()

        # Cache de token en memoria
        self._token = None
        self._token_expires_at = 0

        # Configurar URLs
        if self.ambiente == "produccion":
            self.token_url = f"https://api-seguridad.sunat.gob.pe/v1/clientessol/{self.client_id}/oauth2/token/"
            self.api_url = "https://api-cpe.sunat.gob.pe/v1/contribuyente/gemini"
        else:
            # Beta / Test
            self.token_url = f"https://gre-test.sunat.gob.pe/v1/clientessol/{self.client_id}/oauth2/token"
            self.api_url = "https://gre-test.sunat.gob.pe/v1/contribuyente/gemini"

    def obtener_access_token(self):
        """
        Obtiene el token de acceso OAuth2 de la SUNAT.
        Utiliza caché en memoria si el token sigue siendo válido.
        """
        # Validar si el token actual sigue vigente (con 5 minutos de margen de seguridad)
        if self._token and time.time() < self._token_expires_at - 300:
            return self._token

        if not all([self.client_id, self.client_secret, self.ruc, self.usuario_sol, self.clave_sol]):
            raise ValueError("Faltan configurar credenciales de la SUNAT en el archivo .env.")

        # Limpiar credenciales de espacios en blanco
        username = f"{self.ruc.strip()}{self.usuario_sol.strip()}"
        password = self.clave_sol.strip()

        payload = {
            "grant_type": "password",
            "scope": "https://api-cpe.sunat.gob.pe",
            "client_id": self.client_id.strip(),
            "client_secret": self.client_secret.strip(),
            "username": username,
            "password": password
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        try:
            logger.info(f"Solicitando token de acceso SUNAT en ambiente: {self.ambiente}")
            response = requests.post(self.token_url, data=payload, headers=headers, timeout=15)
            
            if response.status_code != 200:
                try:
                    err_json = response.json()
                    err_desc = err_json.get("error_description") or err_json.get("error") or response.text
                except Exception:
                    err_desc = response.text
                logger.error(f"Error de autenticación con SUNAT ({response.status_code}): {err_desc}")
                raise RuntimeError(f"SUNAT rechazó la autenticación ({response.status_code}): {err_desc}")
                
            response.raise_for_status()
            
            data = response.json()
            self._token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = time.time() + expires_in
            
            logger.info("Token de acceso SUNAT obtenido exitosamente.")
            return self._token
        except Exception as e:
            logger.error(f"Error al obtener token de acceso SUNAT: {e}")
            raise RuntimeError(str(e))

    def emitir_guia_remision(self, datos_guia):
        """
        Envía una Guía de Remisión Electrónica (GRE) a la SUNAT.
        datos_guia debe ser un diccionario compatible con el esquema JSON de SUNAT.
        """
        token = self.obtener_access_token()
        url = f"{self.api_url}/despachos"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            logger.info(f"Enviando Guía de Remisión a SUNAT: {datos_guia.get('serie')}-{datos_guia.get('numero')}")
            response = requests.post(url, json=datos_guia, headers=headers, timeout=20)
            
            if response.status_code in [200, 201]:
                return response.json()
            else:
                try:
                    err_data = response.json()
                except Exception:
                    err_data = {"message": response.text}
                
                logger.error(f"Error devuelto por SUNAT ({response.status_code}): {err_data}")
                raise RuntimeError(err_data.get("message", "Error al procesar la guía de remisión."))
        except Exception as e:
            logger.error(f"Error al transmitir Guía de Remisión a SUNAT: {e}")
            raise

    def consultar_estado_ticket(self, ticket):
        """
        Consulta el estado de procesamiento del ticket de la GRE enviado.
        """
        token = self.obtener_access_token()
        url = f"{self.api_url}/despachos/status/{ticket}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            logger.info(f"Consultando estado de ticket SUNAT: {ticket}")
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error al consultar ticket SUNAT: {e}")
            raise

# Instancia global
sunat_service = SunatService()
