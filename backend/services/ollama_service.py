"""
Servicio de Ollama - Comunicación con servidor Ollama para embeddings y generación
"""
import requests
import logging
import json
from typing import List, Dict, Any, Optional, Iterator

from ..config import OLLAMA_HOST, OLLAMA_EMBEDDING_MODEL, OLLAMA_CHAT_MODEL

logger = logging.getLogger(__name__)


class OllamaService:
    def __init__(self, host: str = OLLAMA_HOST, embedding_model: str = OLLAMA_EMBEDDING_MODEL, chat_model: str = OLLAMA_CHAT_MODEL):
        self.host = host
        self.embedding_model = embedding_model
        self.chat_model = chat_model
        self.required_models = [self.chat_model, self.embedding_model]
        self.embedding_endpoint = f"{host}/api/embeddings"
        self.chat_endpoint = f"{host}/api/chat"
        self.chat_timeout_seconds = 300
        self.chat_temperature = 0.2
        self.chat_top_p = 0.3
        # Margen suficiente para que un modelo con capacidad "thinking" no consuma
        # todo el presupuesto antes de emitir la respuesta visible.
        self.chat_num_predict = 1024
        # Los embeddings se calculan en local; un texto de fragmento completo
        # tarda unos segundos en CPU, pero nunca minutos.
        self.embedding_timeout_seconds = 120

        status = self.get_runtime_status()
        if not status["ollama_connected"]:
            logger.warning("Ollama no disponible en %s", host)
        else:
            logger.info("✓ Conectado a Ollama en %s", host)
            logger.info("  - Chat model configurado: %s", self.chat_model)
            logger.info("  - Embedding model configurado: %s", self.embedding_model)
            logger.info("  - Modelos instalados: %s", status["installed_models"])
            if status["missing_required_models"]:
                logger.error("  - Faltan modelos requeridos: %s", status["missing_required_models"])

    def _is_cloud_model(self, model_name: str) -> bool:
        """Detecta modelos servidos por Ollama Cloud (sufijo ':cloud')."""
        return ":" in model_name and model_name.rsplit(":", 1)[1].lower() == "cloud"

    def _format_ollama_http_error(self, status_code: int, body: str, operation: str) -> str:
        """Convierte un error HTTP de Ollama en un mensaje legible."""
        body_text = (body or "").strip()
        if status_code in (401, 403):
            return (
                f"Ollama Cloud rechazó la petición al ejecutar '{operation}' (HTTP {status_code}). "
                f"Comprueba dos cosas: (1) que Ollama sea version >= 0.18 "
                f"(tu instalacion actual puede ser antigua) y (2) que la sesion este iniciada "
                f"con 'ollama signin'. Modelo de chat configurado: {self.chat_model}. "
                f"Respuesta de Ollama: {body_text or 'sin detalle'}"
            )
        return f"Error de Ollama al ejecutar '{operation}' (HTTP {status_code}): {body_text or 'sin detalle'}"

    def _is_required_model_installed(self, required_model: str, installed_models: List[str]) -> bool:
        # Los modelos cloud no siempre aparecen en /api/tags. Si hay conexion con
        # Ollama y el modelo termina en ':cloud', la peticion se encola hacia
        # ollama.com aunque no este listado localmente.
        if self._is_cloud_model(required_model) and self.check_connection():
            return True

        if ":" in required_model:
            return required_model in installed_models
        return any(
            model_name == required_model or model_name.startswith(f"{required_model}:")
            for model_name in installed_models
        )

    def get_runtime_status(self) -> Dict[str, Any]:
        """Obtiene estado operativo de Ollama y modelos requeridos."""
        ollama_connected = self.check_connection()
        installed_models = self.list_models() if ollama_connected else []
        missing_required_models = [
            model_name
            for model_name in self.required_models
            if not self._is_required_model_installed(model_name, installed_models)
        ]
        ready_for_chat = ollama_connected and self._is_required_model_installed(self.chat_model, installed_models)
        ready_for_embeddings = ollama_connected and self._is_required_model_installed(self.embedding_model, installed_models)

        return {
            "ollama_connected": ollama_connected,
            "configured_chat_model": self.chat_model,
            "configured_embedding_model": self.embedding_model,
            "installed_models": installed_models,
            "missing_required_models": missing_required_models,
            "ready_for_chat": ready_for_chat,
            "ready_for_embeddings": ready_for_embeddings,
        }

    def _build_missing_models_error(self, operation: str) -> str:
        status = self.get_runtime_status()
        if not status["ollama_connected"]:
            return f"Ollama no está disponible en {self.host}. Inicia 'ollama serve'."

        if status["missing_required_models"]:
            missing = ", ".join(status["missing_required_models"])
            return (
                f"No se puede ejecutar '{operation}'. Faltan modelos requeridos en Ollama: {missing}. "
                f"Modelos configurados: chat={self.chat_model}, embeddings={self.embedding_model}."
            )

        return "Estado de Ollama no válido para la operación solicitada."

    def ensure_ready_for_chat(self) -> None:
        status = self.get_runtime_status()
        if not status["ready_for_chat"]:
            raise RuntimeError(self._build_missing_models_error("chat"))

    def ensure_ready_for_embeddings(self) -> None:
        status = self.get_runtime_status()
        if not status["ready_for_embeddings"]:
            raise RuntimeError(self._build_missing_models_error("embeddings"))

    def check_connection(self) -> bool:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False

    def generate_embedding(self, text: str) -> List[float]:
        try:
            self.ensure_ready_for_embeddings()
            payload = {"model": self.embedding_model, "prompt": text}
            response = requests.post(self.embedding_endpoint, json=payload, timeout=self.embedding_timeout_seconds)
            if response.status_code != 200:
                raise Exception(self._format_ollama_http_error(response.status_code, response.text, "embeddings"))
            result = response.json()
            if 'embedding' in result:
                return result['embedding']
            else:
                logger.error(f"Respuesta inesperada: {result}")
                raise RuntimeError("Respuesta inválida de Ollama al generar embeddings")
        except Exception as e:
            logger.error(f"Error generando embedding: {str(e)}")
            raise

    def generate_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        self.ensure_ready_for_embeddings()
        embeddings = []
        for i, text in enumerate(texts):
            try:
                embedding = self.generate_embedding(text)
                embeddings.append(embedding)
            except Exception as e:
                logger.error(f"Error en embedding {i}: {str(e)}")
                raise RuntimeError(f"No se pudieron generar embeddings con {self.embedding_model}: {str(e)}") from e
        logger.info(f"✓ Generados {len(embeddings)} embeddings")
        return embeddings

    def generate_response(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        try:
            self.ensure_ready_for_chat()
            if not context or not context.strip():
                return "No encuentro información suficiente en los documentos para responder con fiabilidad."

            history_block = ""
            if conversation_history:
                formatted_turns = []
                for turn in conversation_history:
                    role = turn.get("role", "")
                    content = (turn.get("content") or "").strip()
                    if not content:
                        continue
                    label = "Usuario" if role == "user" else "Asistente" if role == "assistant" else role.title()
                    formatted_turns.append(f"{label}: {content}")

                if formatted_turns:
                    history_block = "\n".join(formatted_turns)

            system_prompt = """Eres un asistente experto.

Reglas:
1) Usa el CONTEXTO como referencia principal.
2) Puedes responder con tus propias palabras, sintetizar y organizar la información.
3) Si algo no está en el contexto, dilo claramente y no inventes.
4) Si usas conocimiento externo, sepáralo del contexto y marca la diferencia.
5) Responde de forma natural, clara y útil.
"""

            user_prompt = f"""HISTORIAL RECIENTE:
{history_block if history_block else 'Sin historial previo.'}

CONTEXTO:
{context}

PREGUNTA:
{prompt}
"""

            payload = {
                "model": self.chat_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "think": False,
                "format": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"}
                    },
                    "required": ["answer"]
                },
                "options": {
                    "temperature": self.chat_temperature,
                    "top_p": self.chat_top_p,
                    "num_predict": min(max_tokens, self.chat_num_predict)
                }
            }
            response = requests.post(self.chat_endpoint, json=payload, timeout=self.chat_timeout_seconds)
            if response.status_code != 200:
                raise Exception(self._format_ollama_http_error(response.status_code, response.text, "chat"))
            result = response.json()
            text_response = self._extract_text_response(result)
            if text_response:
                return text_response

            logger.warning(f"Respuesta de Ollama sin contenido visible: {result}")
            return "Lo siento, no pude generar una respuesta en este momento."
        except Exception as e:
            logger.error(f"Error generando respuesta: {str(e)}")
            raise

    def stream_response(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[str]:
        """Genera una respuesta en streaming token a token usando Ollama."""
        try:
            self.ensure_ready_for_chat()
            if not context or not context.strip():
                yield "No encuentro información suficiente en los documentos para responder con fiabilidad."
                return

            history_block = ""
            if conversation_history:
                formatted_turns = []
                for turn in conversation_history:
                    role = turn.get("role", "")
                    content = (turn.get("content") or "").strip()
                    if not content:
                        continue
                    label = "Usuario" if role == "user" else "Asistente" if role == "assistant" else role.title()
                    formatted_turns.append(f"{label}: {content}")

                if formatted_turns:
                    history_block = "\n".join(formatted_turns)

            system_prompt = """Eres un asistente experto.

Reglas:
1) Usa el CONTEXTO como referencia principal.
2) Puedes responder con tus propias palabras, sintetizar y organizar la información.
3) Si algo no está en el contexto, dilo claramente y no inventes.
4) Si usas conocimiento externo, sepáralo del contexto y marca la diferencia.
5) Responde de forma natural, clara y útil.
"""

            user_prompt = f"""HISTORIAL RECIENTE:
{history_block if history_block else 'Sin historial previo.'}

CONTEXTO:
{context}

PREGUNTA:
{prompt}
"""

            payload = {
                "model": self.chat_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": True,
                "think": False,
                "options": {
                    "temperature": self.chat_temperature,
                    "top_p": self.chat_top_p,
                    "num_predict": min(max_tokens, self.chat_num_predict)
                }
            }

            with requests.post(
                self.chat_endpoint,
                json=payload,
                timeout=self.chat_timeout_seconds,
                stream=True
            ) as response:
                if response.status_code != 200:
                    raise Exception(self._format_ollama_http_error(response.status_code, response.text, "chat"))

                emitted_chars = 0
                thinking_chars = 0
                done_reason = None

                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue

                    try:
                        data = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    message = data.get("message") or {}
                    token = message.get("content") or data.get("response") or ""
                    thinking_chars += len(message.get("thinking") or "")
                    if token:
                        emitted_chars += len(token)
                        yield token

                    if data.get("done"):
                        done_reason = data.get("done_reason")
                        break

                if emitted_chars == 0:
                    logger.error(
                        "Ollama no emitio contenido visible | model=%s | done_reason=%s | thinking_chars=%d",
                        self.chat_model,
                        done_reason,
                        thinking_chars,
                    )

        except Exception as e:
            logger.error(f"Error en streaming de respuesta: {str(e)}")
            raise

    def _extract_text_response(self, result: Dict[str, Any]) -> str:
        """Extrae el texto visible de distintas variantes de respuesta de Ollama."""
        message = result.get('message') or {}
        message_content = (message.get('content') or '').strip()

        if message_content:
            structured_answer = self._extract_structured_answer(message_content)
            if structured_answer:
                return structured_answer

        response_text = (result.get('response') or '').strip()
        if response_text:
            structured_answer = self._extract_structured_answer(response_text)
            if structured_answer:
                return structured_answer
            return response_text

        if message_content:
            return message_content

        thinking_text = (result.get('thinking') or '').strip()
        if thinking_text:
            return thinking_text

        message_thinking = (message.get('thinking') or '').strip()
        if message_thinking:
            return message_thinking

        return ""

    def _extract_structured_answer(self, raw_text: str) -> str:
        """Obtiene el campo answer cuando Ollama responde con JSON estructurado."""
        try:
            parsed = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError):
            return ""

        answer = parsed.get('answer')
        return answer.strip() if isinstance(answer, str) else ""

    def get_model_info(self) -> Dict[str, Any]:
        status = self.get_runtime_status()
        return {
            "embedding_model": self.embedding_model,
            "chat_model": self.chat_model,
            "available": status["ollama_connected"],
            "installed_models": status["installed_models"],
            "missing_required_models": status["missing_required_models"],
            "ready_for_chat": status["ready_for_chat"],
            "ready_for_embeddings": status["ready_for_embeddings"],
        }

    def list_models(self) -> List[str]:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if 'models' in data:
                    return [m['name'] for m in data['models']]
            return []
        except Exception as e:
            logger.error(f"Error listando modelos: {str(e)}")
            return []