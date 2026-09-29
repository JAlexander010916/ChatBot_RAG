"""
Router para chat usando RAG
"""
from fastapi import APIRouter, HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse
import logging
import time
import json
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from uuid import uuid4

from ..config import DB_PATH
from ..services.database_service import DatabaseService
from ..services.ollama_service import OllamaService
from ..services.rag_engine import RAGEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# Inicializar servicios
db_service = DatabaseService(str(DB_PATH))
ollama_service = OllamaService()
rag_engine = RAGEngine(db_service, ollama_service)


class ChatRequest(BaseModel):
    """Modelo para request de chat"""
    question: str
    top_k: int = 5  # Número de chunks a recuperar
    document_ids: Optional[List[int]] = None  # Opcional para acotar el contexto
    session_id: Optional[str] = None  # Opcional para mantener historial


class ChatResponse(BaseModel):
    """Modelo para respuesta de chat"""
    question: str
    answer: str
    sources: List[Dict[str, Any]]
    confidence: float
    processing_time: Optional[float] = None


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class RenameSessionRequest(BaseModel):
    title: str


MAX_TURNS_FOR_CONTEXT = 6


def _build_session_title(question: str, current_count: int) -> str:
    if current_count > 0:
        return ""
    compact = " ".join(question.split()).strip()
    if not compact:
        return "Nuevo chat"
    return compact[:60] + ("..." if len(compact) > 60 else "")


def _serialize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": message.get("id"),
        "role": message.get("role"),
        "content": message.get("content"),
        "sources": message.get("sources") or [],
        "confidence": message.get("confidence"),
        "processing_time": message.get("processing_time"),
        "timestamp": message.get("created_at"),
    }


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    serialized = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {serialized}\n\n"


def _chat_service_unavailable_detail() -> str:
    status = ollama_service.get_runtime_status()
    if not status["ollama_connected"]:
        return "Servicio no disponible: Ollama no está activo. Ejecuta 'ollama serve'."

    if status["missing_required_models"]:
        missing = ", ".join(status["missing_required_models"])
        return (
            "Servicio no disponible: faltan modelos requeridos en Ollama. "
            f"Modelos configurados: chat={status['configured_chat_model']}, embeddings={status['configured_embedding_model']}. "
            f"Modelos faltantes={missing or 'ninguno'}."
        )

    return "Servicio no disponible: configuración de chat inválida."


@router.get("/sessions")
async def list_sessions() -> List[Dict[str, Any]]:
    """Lista chats guardados en base de datos."""
    try:
        return db_service.get_chat_sessions()
    except Exception as e:
        logger.error(f"Error listando sesiones: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions")
async def create_session(request: CreateSessionRequest) -> Dict[str, Any]:
    """Crea un nuevo chat."""
    try:
        session_id = f"session-{uuid4()}"
        title = (request.title or "Nuevo chat").strip() or "Nuevo chat"
        session = db_service.create_chat_session(session_id=session_id, title=title)
        return session
    except Exception as e:
        logger.error(f"Error creando sesión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}")
async def get_session_messages(session_id: str) -> Dict[str, Any]:
    """Obtiene mensajes de un chat."""
    try:
        sessions = db_service.get_chat_sessions(limit=1000)
        session = next((item for item in sessions if item["session_id"] == session_id), None)
        if not session:
            raise HTTPException(status_code=404, detail="Chat no encontrado")

        messages = db_service.get_chat_messages(session_id=session_id, limit=500)
        return {
            "session": session,
            "messages": [_serialize_message(message) for message in messages]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo sesión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, request: RenameSessionRequest) -> Dict[str, Any]:
    """Renombra un chat existente."""
    try:
        title = (request.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="El título es obligatorio")
        db_service.update_chat_session_title(session_id=session_id, title=title)
        return {"success": True, "session_id": session_id, "title": title}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error renombrando sesión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> Dict[str, Any]:
    """Elimina un chat y todos sus mensajes."""
    try:
        deleted = db_service.delete_chat_session(session_id=session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat no encontrado")
        return {"success": True, "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando sesión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask")
async def ask_question(request: ChatRequest) -> ChatResponse:
    """Responde una pregunta usando el motor RAG"""
    
    try:
        start_time = time.time()

        status = ollama_service.get_runtime_status()
        if (not status["ollama_connected"]) or bool(status["missing_required_models"]):
            raise HTTPException(status_code=503, detail=_chat_service_unavailable_detail())
        
        # Validar pregunta
        if not request.question or len(request.question) < 2:
            raise HTTPException(
                status_code=400,
                detail="La pregunta debe tener al menos 2 caracteres"
            )
        
        # Verificar que hay documentos
        documents = db_service.get_all_documents()
        if not documents:
            raise HTTPException(
                status_code=400,
                detail="No hay documentos cargados. Por favor, carga documentos primero."
            )
        
        # Verificar que hay chunks
        all_chunks = db_service.get_all_chunks(document_ids=request.document_ids)
        if not all_chunks:
            if request.document_ids:
                raise HTTPException(
                    status_code=400,
                    detail="Los documentos seleccionados no tienen chunks disponibles."
                )
            raise HTTPException(
                status_code=400,
                detail="Los documentos aún se están procesando. Espera unos momentos e intenta de nuevo."
            )
        
        logger.info(f"Pregunta: {request.question}")
        logger.info(
            f"Documentos disponibles: {len(documents)}, Chunks: {len(all_chunks)}"
            + (f", filtro document_ids={request.document_ids}" if request.document_ids else "")
        )

        recent_turns: List[Dict[str, Any]] = []
        existing_user_messages = 0
        if request.session_id:
            db_service.create_chat_session(session_id=request.session_id, title="Nuevo chat")
            recent_turns = db_service.get_recent_chat_turns(
                session_id=request.session_id,
                limit=MAX_TURNS_FOR_CONTEXT
            )
            existing_user_messages = db_service.get_chat_user_message_count(request.session_id)
        
        try:
            # Ejecutar RAG
            result = rag_engine.answer_question(
                request.question,
                top_k=request.top_k,
                document_ids=request.document_ids,
                conversation_history=recent_turns,
            )
        except Exception as e:
            logger.error(f"Error en RAG Engine: {str(e)}")
            if "Faltan modelos requeridos" in str(e) or "No se puede ejecutar 'chat'" in str(e):
                raise HTTPException(status_code=503, detail=f"Servicio no disponible: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error procesando pregunta: {str(e)}"
            )
        
        processing_time = time.time() - start_time
        
        # Guardar historial en base de datos si se proporciona session_id
        if request.session_id:
            auto_title = _build_session_title(request.question, existing_user_messages)
            if auto_title:
                db_service.update_chat_session_title(request.session_id, auto_title)

            db_service.add_chat_message(
                session_id=request.session_id,
                role="user",
                content=request.question,
            )
            db_service.add_chat_message(
                session_id=request.session_id,
                role="assistant",
                content=result["answer"],
                sources=result["sources"],
                confidence=result["confidence"],
                processing_time=round(processing_time, 2)
            )
        
        return ChatResponse(
            question=request.question,
            answer=result["answer"],
            sources=result["sources"],
            confidence=result["confidence"],
            processing_time=round(processing_time, 2)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")


@router.post("/ask/stream")
async def ask_question_stream(http_request: Request, request: ChatRequest) -> StreamingResponse:
    """Responde una pregunta usando streaming SSE para entregar tokens en vivo."""

    status = ollama_service.get_runtime_status()
    if (not status["ollama_connected"]) or bool(status["missing_required_models"]):
        raise HTTPException(status_code=503, detail=_chat_service_unavailable_detail())

    # Validaciones iniciales antes de abrir el stream
    if not request.question or len(request.question) < 2:
        raise HTTPException(
            status_code=400,
            detail="La pregunta debe tener al menos 2 caracteres"
        )

    documents = db_service.get_all_documents()
    if not documents:
        raise HTTPException(
            status_code=400,
            detail="No hay documentos cargados. Por favor, carga documentos primero."
        )

    all_chunks = db_service.get_all_chunks(document_ids=request.document_ids)
    if not all_chunks:
        if request.document_ids:
            raise HTTPException(
                status_code=400,
                detail="Los documentos seleccionados no tienen chunks disponibles."
            )
        raise HTTPException(
            status_code=400,
            detail="Los documentos aún se están procesando. Espera unos momentos e intenta de nuevo."
        )

    recent_turns: List[Dict[str, Any]] = []
    existing_user_messages = 0
    if request.session_id:
        db_service.create_chat_session(session_id=request.session_id, title="Nuevo chat")
        recent_turns = db_service.get_recent_chat_turns(
            session_id=request.session_id,
            limit=MAX_TURNS_FOR_CONTEXT
        )
        existing_user_messages = db_service.get_chat_user_message_count(request.session_id)

    relevant_chunks = rag_engine.retrieve_relevant_chunks(
        request.question,
        top_k=request.top_k,
        document_ids=request.document_ids,
    )

    sources = rag_engine._extract_sources(relevant_chunks)
    confidence = rag_engine._calculate_confidence(relevant_chunks)
    context = rag_engine._build_context(relevant_chunks) if relevant_chunks else ""

    async def event_generator():
        started_at = time.time()
        first_token_at: Optional[float] = None
        answer_parts: List[str] = []

        try:
            yield _sse_event("start", {
                "sources": sources,
                "confidence": confidence,
            })

            if not relevant_chunks:
                static_answer = "No se encontraron documentos relevantes para tu pregunta."
                answer_parts.append(static_answer)
                if await http_request.is_disconnected():
                    return
                yield _sse_event("token", {"token": static_answer})
            elif not context:
                static_answer = "No se pudo preparar el contexto suficiente para responder con referencia."
                answer_parts.append(static_answer)
                if await http_request.is_disconnected():
                    return
                yield _sse_event("token", {"token": static_answer})
            else:
                for token in ollama_service.stream_response(
                    request.question,
                    context=context,
                    conversation_history=recent_turns,
                ):
                
                    if await http_request.is_disconnected():
                        logger.info("Cliente desconectado durante stream SSE")
                        return

                    if not token:
                        continue

                    if first_token_at is None:
                        first_token_at = time.time()

                    answer_parts.append(token)
                    yield _sse_event("token", {"token": token})

            answer = "".join(answer_parts).strip()
            if not answer:
                answer = "No encuentro información suficiente en los documentos para responder con fiabilidad."

            if rag_engine._looks_like_unusable_answer(answer):
                answer, extracted = rag_engine._build_extractive_fallback_answer(request.question, relevant_chunks)
                if not extracted:
                    answer = "No encuentro información suficiente en los documentos para responder con fiabilidad."

            processing_time = round(time.time() - started_at, 2)
            first_token_ms = round((first_token_at - started_at) * 1000, 1) if first_token_at else None

            if request.session_id and answer:
                auto_title = _build_session_title(request.question, existing_user_messages)
                if auto_title:
                    db_service.update_chat_session_title(request.session_id, auto_title)

                db_service.add_chat_message(
                    session_id=request.session_id,
                    role="user",
                    content=request.question,
                )
                db_service.add_chat_message(
                    session_id=request.session_id,
                    role="assistant",
                    content=answer,
                    sources=sources,
                    confidence=confidence,
                    processing_time=processing_time,
                )

            if await http_request.is_disconnected():
                return

            yield _sse_event("done", {
                "answer": answer,
                "sources": sources,
                "confidence": confidence,
                "processing_time": processing_time,
                "first_token_ms": first_token_ms,
            })
        except Exception as e:
            logger.error(f"Error en stream SSE: {str(e)}")
            if await http_request.is_disconnected():
                return
            detail = f"Error procesando pregunta: {str(e)}"
            if "Faltan modelos requeridos" in str(e) or "No se puede ejecutar 'chat'" in str(e):
                detail = f"Servicio no disponible: {str(e)}"
            yield _sse_event("error", {"detail": detail})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health")
async def chat_health() -> Dict[str, Any]:
    """
    Verifica la salud del sistema de chat
    
    Returns:
        {
            "status": str,
            "ollama": bool,
            "database": bool,
            "documents": int
        }
    """
    try:
        ollama_status = ollama_service.get_runtime_status()
        
        try:
            documents = db_service.get_all_documents()
            db_ok = True
            doc_count = len(documents)
        except:
            db_ok = False
            doc_count = 0
        
        status = "healthy" if (ollama_status["ready_for_chat"] and ollama_status["ready_for_embeddings"] and db_ok) else "degraded"

        error_detail = None
        if not ollama_status["ollama_connected"]:
            error_detail = "Ollama no está conectado"
        elif ollama_status["missing_required_models"]:
            missing = ", ".join(ollama_status["missing_required_models"])
            error_detail = f"Faltan modelos requeridos: {missing}"
        
        return {
            "status": status,
            "ollama": ollama_status["ollama_connected"],
            "database": db_ok,
            "documents": doc_count,
            "configured_chat_model": ollama_status["configured_chat_model"],
            "configured_embedding_model": ollama_status["configured_embedding_model"],
            "installed_models": ollama_status["installed_models"],
            "missing_required_models": ollama_status["missing_required_models"],
            "ready_for_chat": ollama_status["ready_for_chat"],
            "ready_for_embeddings": ollama_status["ready_for_embeddings"],
            "error_detail": error_detail,
        }
    
    except Exception as e:
        logger.error(f"Error en health check: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@router.get("/stats")
async def get_chat_stats() -> Dict[str, Any]:
    """
    Obtiene estadísticas del chat
    
    Returns:
        {
            "active_sessions": int,
            "total_messages": int,
            "avg_response_time": float
        }
    """
    try:
        return db_service.get_chat_stats()
    
    except Exception as e:
        logger.error(f"Error obteniendo stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))