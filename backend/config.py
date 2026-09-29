import os
import sys
from pathlib import Path


def _resolve_data_root() -> Path:
    """Resuelve carpeta raíz de datos de aplicación en una ruta escribible."""
    env_root = os.getenv("CHAT_APP_DATA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    if getattr(sys, "frozen", False):
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ChatDocumentAI"
        return Path.home() / "AppData" / "Local" / "ChatDocumentAI"

    return Path(__file__).resolve().parent


def _resolve_docs_dir() -> Path:
    """Resuelve una carpeta de documentos estable (dev y ejecutable)."""
    env_dir = os.getenv("CHAT_APP_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return _resolve_data_root() / "documentos"

    repo_root = Path(__file__).resolve().parent.parent
    root_docs = repo_root / "documentos"
    legacy_backend_docs = repo_root / "backend" / "documentos"

    if root_docs.exists():
        return root_docs

    return legacy_backend_docs


DOCS_DIR = _resolve_docs_dir()
DB_PATH = _resolve_data_root() / "chat_documents.db"

DOCS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.doc']
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# ===== CONFIGURACIÓN DE OLLAMA ====
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "gemma4:cloud")

# ===== CORS =====
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# ===== Variables de servidor =====
HOST = "0.0.0.0"
PORT = 8000
DEBUG = True