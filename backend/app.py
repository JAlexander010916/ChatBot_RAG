"""
Aplicación FastAPI principal
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import sys
from pathlib import Path
from typing import Optional

from .config import CORS_ORIGINS, HOST, PORT, DEBUG
from .routers import documents, chat


app = FastAPI(
    title="Chat Document AI",
    description="Sistema RAG para hacer preguntas sobre documentos",
    version="1.0.0"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir routers
app.include_router(documents.router)
app.include_router(chat.router)


@app.on_event("startup")
async def recover_documents_on_startup() -> None:
    """Recupera documentos que pudieron quedar colgados en estado processing."""
    documents.ensure_processing_worker_running()
    documents.recover_stuck_processing_documents()


@app.get("/")
async def root():
    """Abre la UI si está disponible; fallback a respuesta de API."""
    if frontend_dist:
        return RedirectResponse(url="/index.html")

    return {
        "message": "Chat Document AI API",
        "docs": "/docs",
        "endpoints": {
            "documents": "/api/documents/",
            "chat": "/api/chat/"
        }
    }


@app.get("/api/health")
async def health() -> dict:
    """Health check general"""
    return {"status": "OK"}


def _find_frontend_dist() -> Optional[Path]:
    """Busca el build del frontend para servir la UI desde FastAPI."""
    candidates = [
        Path(__file__).parent / "static",
        Path(__file__).parent.parent / "frontend" / "dist",
    ]

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(Path(bundle_dir) / "frontend" / "dist")
        candidates.append(Path(bundle_dir) / "static")

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    return None


frontend_dist = _find_frontend_dist()
if frontend_dist:
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info"
    )