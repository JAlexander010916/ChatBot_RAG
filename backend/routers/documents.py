"""
Routers para documentos - Upload, listing, deletion, load all
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Body
from pathlib import Path
import logging
from typing import List, Dict, Any, Optional
import threading
import queue

from ..config import DOCS_DIR, ALLOWED_EXTENSIONS, MAX_FILE_SIZE, DB_PATH
from ..services.document_processor import DocumentProcessor
from ..services.database_service import DatabaseService
from ..services.ollama_service import OllamaService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

# Inicializar servicios
db_service = DatabaseService(str(DB_PATH))
ollama_service = OllamaService()

MAX_PROCESSING_RETRIES = 3
_processing_queue: "queue.Queue[tuple[int, str, str]]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _enqueue_document_processing(doc_id: int, filepath: str, filename: str) -> None:
    _processing_queue.put((doc_id, filepath, filename))


def _processing_worker() -> None:
    while True:
        doc_id, filepath, filename = _processing_queue.get()
        try:
            process_document_background(doc_id=doc_id, filepath=filepath, filename=filename)
        finally:
            _processing_queue.task_done()


def ensure_processing_worker_running() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        worker = threading.Thread(target=_processing_worker, daemon=True, name="document-processing-worker")
        worker.start()
        _worker_started = True
        logger.info("Worker de procesamiento de documentos iniciado")


def recover_stuck_processing_documents() -> Dict[str, int]:
    """
    Recupera documentos que quedaron en 'processing' tras reinicios/fallos.
    Reencola los que aún tienen intentos disponibles y marca error en los agotados.
    """
    ensure_processing_worker_running()
    processing_docs = db_service.get_documents_by_processing_status('processing')
    requeued = 0
    marked_error = 0

    for doc in processing_docs:
        doc_id = int(doc['id'])
        attempts = int(doc.get('processing_attempts') or 0)
        filepath = str(doc.get('filepath') or "")
        filename = str(doc.get('filename') or f"doc-{doc_id}")

        if attempts >= MAX_PROCESSING_RETRIES:
            db_service.set_document_processing_error(
                doc_id,
                f"Documento marcado como error tras reinicio: superó {MAX_PROCESSING_RETRIES} intentos",
            )
            marked_error += 1
            continue

        if not filepath or not Path(filepath).exists():
            db_service.set_document_processing_error(
                doc_id,
                "Archivo del documento no disponible para reintento",
            )
            marked_error += 1
            continue

        db_service.reset_document_processing_state(doc_id)
        _enqueue_document_processing(doc_id, filepath, filename)
        requeued += 1

    if processing_docs:
        logger.info(
            "Recuperación de documentos processing completada: total=%s, reencolados=%s, error=%s",
            len(processing_docs),
            requeued,
            marked_error,
        )

    return {
        "total_processing": len(processing_docs),
        "requeued": requeued,
        "marked_error": marked_error,
    }


def _document_backend_file_exists(doc: Dict[str, Any]) -> bool:
    filepath_value = doc.get("filepath")
    if not filepath_value:
        return False
    try:
        return Path(filepath_value).exists()
    except Exception:
        return False


def _remove_backend_file_for_doc(doc: Dict[str, Any]) -> bool:
    filepath_value = doc.get("filepath")
    if not filepath_value:
        return False
    filepath = Path(filepath_value)
    if filepath.exists() and filepath.is_file():
        filepath.unlink()
        return True
    return False


def _embedding_service_unavailable_detail() -> str:
    status = ollama_service.get_runtime_status()
    if not status["ollama_connected"]:
        return "No se puede procesar documentos: Ollama no está activo. Ejecuta 'ollama serve'."

    if not status["ready_for_embeddings"]:
        missing = ", ".join(status["missing_required_models"])
        return (
            "No se puede procesar documentos: falta el modelo de embeddings requerido. "
            f"Modelo configurado={status['configured_embedding_model']}. "
            f"Modelos faltantes={missing or 'ninguno'}."
        )

    return "No se puede procesar documentos: configuración de embeddings inválida."


# ========== POST ENDPOINTS ==========

@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Carga un documento individual (PDF o DOCX)
    """
    filepath: Optional[Path] = None
    file_created = False
    try:
        if file.filename is None:
            raise HTTPException(status_code=400, detail="El nombre del archivo es obligatorio")
        
        original_filename = file.filename
        base_filename = Path(original_filename).name
        if not base_filename:
            raise HTTPException(status_code=400, detail="Nombre de archivo inválido")

        file_ext = Path(base_filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de archivo no permitido. Permitidos: {ALLOWED_EXTENSIONS}"
            )
        
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Archivo demasiado grande. Máximo: {MAX_FILE_SIZE / 1024 / 1024:.0f}MB"
            )
        
        filepath = DOCS_DIR / base_filename
        if filepath.exists():
            raise HTTPException(
                status_code=409,
                detail=f"El archivo '{base_filename}' ya existe"
            )
        
        with open(filepath, "wb") as f:
            f.write(content)
        file_created = True
        
        doc_id = db_service.add_document(
            filename=base_filename,
            filepath=str(filepath),
            filetype=file_ext,
            filesize=len(content),
            pages=0
        )
        
        if background_tasks:
            background_tasks.add_task(ensure_processing_worker_running)
            background_tasks.add_task(
                _enqueue_document_processing,
                doc_id=doc_id,
                filepath=str(filepath),
                filename=base_filename,
            )
        
        return {
            "success": True,
            "document_id": doc_id,
            "filename": base_filename,
            "status": "processing",
            "message": "Documento subido. Procesando embeddings..."
        }
    
    except HTTPException as e:
        if file_created and filepath and filepath.exists():
            filepath.unlink()
        raise e
    
    except Exception as e:
        logger.error(f"Error en upload: {str(e)}")
        if file_created and filepath and filepath.exists():
            filepath.unlink()
        raise HTTPException(status_code=500, detail=f"Error al procesar: {str(e)}")


@router.post("/load-all")
async def load_all_documents_from_folder(
    background_tasks: BackgroundTasks
):
    """
    Carga automáticamente TODOS los documentos de la carpeta documentos/
    """
    try:
        if not DOCS_DIR.exists():
            raise HTTPException(
                status_code=400,
                detail="La carpeta 'documentos' no existe"
            )
        
        document_files: List[Path] = []
        for ext in ['*.pdf', '*.PDF', '*.docx', '*.DOCX', '*.doc', '*.DOC']:
            document_files.extend(DOCS_DIR.rglob(ext))
        
        document_files = list(set(document_files))
        
        if not document_files:
            raise HTTPException(
                status_code=400,
                detail="No hay documentos PDF, DOCX o DOC en la carpeta configurada"
            )
        
        logger.info(f"Encontrados {len(document_files)} documentos para procesar")
        
        results: List[Dict[str, Any]] = []
        loaded_count = 0
        
        for filepath in document_files:
            try:
                existing = db_service.get_all_documents()
                if any(doc['filename'] == filepath.name for doc in existing):
                    logger.info(f"Documento ya existe: {filepath.name}")
                    results.append({
                        "filename": filepath.name,
                        "status": "already_exists",
                        "message": "Ya estaba cargado"
                    })
                    continue
                
                filesize = filepath.stat().st_size
                filetype = filepath.suffix.lower()
                
                doc_id = db_service.add_document(
                    filename=filepath.name,
                    filepath=str(filepath),
                    filetype=filetype,
                    filesize=filesize,
                    pages=0
                )
                
                if background_tasks:
                    background_tasks.add_task(ensure_processing_worker_running)
                    background_tasks.add_task(
                        _enqueue_document_processing,
                        doc_id=doc_id,
                        filepath=str(filepath),
                        filename=filepath.name,
                    )
                
                results.append({
                    "filename": filepath.name,
                    "doc_id": doc_id,
                    "status": "processing",
                    "filesize_mb": round(filesize / 1024 / 1024, 2)
                })
                
                loaded_count += 1
                logger.info(f"✓ Agregado para procesar: {filepath.name}")
            
            except Exception as e:
                logger.error(f"Error procesando {filepath.name}: {str(e)}")
                results.append({
                    "filename": filepath.name,
                    "status": "error",
                    "error": str(e)
                })
        
        return {
            "success": True,
            "total_found": len(document_files),
            "loaded": loaded_count,
            "documents": results,
            "message": f"Se están procesando {loaded_count} documentos..."
        }
    
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error cargando carpeta: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# ========== GET ENDPOINTS ==========

@router.get("/")
async def list_documents():
    """
    Lista todos los documentos cargados
    """
    try:
        documents = db_service.get_all_documents()
        result: List[Dict[str, Any]] = []
        for doc in documents:
            chunks = db_service.get_chunks_by_document(doc['id'])
            result.append({
                **doc,
                "chunks_count": len(chunks),
                "filesize_mb": round(doc['filesize'] / 1024 / 1024, 2),
                "backend_file_exists": _document_backend_file_exists(doc)
            })
        return result
    except Exception as e:
        logger.error(f"Error listando documentos: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_stats():
    """
    Retorna estadísticas del sistema
    """
    try:
        documents = db_service.get_all_documents()
        total_size = sum(doc['filesize'] for doc in documents) / 1024 / 1024 if documents else 0
        
        total_chunks = 0
        for doc in documents:
            chunks = db_service.get_chunks_by_document(doc['id'])
            total_chunks += len(chunks)
        
        ollama_status = ollama_service.get_runtime_status()
        status = "healthy" if ollama_status["ready_for_chat"] and ollama_status["ready_for_embeddings"] else "degraded"
        
        return {
            "status": status,
            "total_documents": len(documents),
            "total_chunks": total_chunks,
            "total_size_mb": round(total_size, 2),
            "ollama_connected": ollama_status["ollama_connected"],
            "configured_chat_model": ollama_status["configured_chat_model"],
            "configured_embedding_model": ollama_status["configured_embedding_model"],
            "installed_models": ollama_status["installed_models"],
            "missing_required_models": ollama_status["missing_required_models"],
            "ready_for_chat": ollama_status["ready_for_chat"],
            "ready_for_embeddings": ollama_status["ready_for_embeddings"],
        }
    except Exception as e:
        logger.error(f"Error obteniendo stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/content")
async def search_documents(query: str, limit: int = 10):
    """
    Búsqueda simple por contenido de texto
    """
    try:
        if not query or len(query) < 2:
            raise HTTPException(status_code=400, detail="Query debe tener al menos 2 caracteres")
        results = db_service.search_by_content(query, limit)
        return results
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en búsqueda: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/status-batch")
async def get_documents_status_batch(document_ids: List[int] = Body(..., embed=True)):
    """
    Retorna estado de procesamiento para un lote de documentos.
    """
    try:
        if not isinstance(document_ids, list) or not document_ids:
            raise HTTPException(status_code=400, detail="document_ids debe ser una lista no vacía")

        statuses: List[Dict[str, Any]] = []
        missing_ids: List[int] = []

        for document_id in document_ids:
            doc = db_service.get_document(document_id)
            if not doc:
                missing_ids.append(document_id)
                continue

            statuses.append({
                "id": doc["id"],
                "filename": doc["filename"],
                "processing_status": doc.get("processing_status", "ready"),
                "pages": doc.get("pages") or 0,
                "updated_at": doc.get("updated_at"),
                "backend_file_exists": _document_backend_file_exists(doc),
            })

        return {
            "documents": statuses,
            "missing_ids": missing_ids,
            "total": len(document_ids),
            "found": len(statuses),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error consultando estado por lote: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}")
async def get_document(document_id: int):
    """
    Obtiene detalles de un documento específico
    """
    try:
        doc = db_service.get_document(document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        chunks = db_service.get_chunks_by_document(document_id)
        public_chunks = [
            {key: value for key, value in chunk.items() if key != 'embeddings'}
            for chunk in chunks
        ]
        return {
            **doc,
            "chunks": public_chunks,
            "chunks_count": len(public_chunks),
            "filesize_mb": round(doc['filesize'] / 1024 / 1024, 2),
            "backend_file_exists": _document_backend_file_exists(doc)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo documento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{document_id}")
async def delete_document(document_id: int):
    """
    Elimina un documento y sus chunks
    """
    try:
        doc = db_service.get_document(document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        
        filepath = Path(doc['filepath'])
        if filepath.exists():
            filepath.unlink()
        
        db_service.delete_document(document_id)
        
        return {
            "success": True,
            "message": f"Documento '{doc['filename']}' eliminado exitosamente"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando documento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete-batch")
async def delete_documents_batch(document_ids: List[int] = Body(..., embed=True)):
    """
    Elimina completamente múltiples documentos (archivo físico + base de datos).
    """
    try:
        if not isinstance(document_ids, list) or not document_ids:
            raise HTTPException(status_code=400, detail="document_ids debe ser una lista no vacía")

        deleted_count = 0
        not_found_ids: List[int] = []
        results: List[Dict[str, Any]] = []

        for document_id in document_ids:
            doc = db_service.get_document(document_id)
            if not doc:
                not_found_ids.append(document_id)
                continue

            filepath = Path(doc['filepath'])
            file_removed = False
            if filepath.exists() and filepath.is_file():
                filepath.unlink()
                file_removed = True

            db_deleted = db_service.delete_document(document_id)
            if db_deleted:
                deleted_count += 1

            results.append({
                "document_id": document_id,
                "filename": doc['filename'],
                "file_removed": file_removed,
                "db_deleted": db_deleted,
            })

        return {
            "success": True,
            "requested": len(document_ids),
            "deleted": deleted_count,
            "not_found_ids": not_found_ids,
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en borrado masivo completo de documentos: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{document_id}/file-only")
async def remove_document_backend_file(document_id: int):
    """
    Elimina solo el archivo físico del backend y mantiene el registro en base de datos.
    """
    try:
        doc = db_service.get_document(document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")

        removed = _remove_backend_file_for_doc(doc)
        return {
            "success": True,
            "document_id": document_id,
            "filename": doc["filename"],
            "file_removed": removed,
            "backend_file_exists": _document_backend_file_exists(doc),
            "message": "Archivo del backend eliminado" if removed else "El archivo ya no existía en backend",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando archivo backend de documento {document_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/file-only/batch-remove")
async def remove_documents_backend_files_batch(document_ids: List[int] = Body(..., embed=True)):
    """
    Elimina archivos físicos del backend para múltiples documentos, sin borrar BD.
    """
    try:
        if not isinstance(document_ids, list) or not document_ids:
            raise HTTPException(status_code=400, detail="document_ids debe ser una lista no vacía")

        results: List[Dict[str, Any]] = []
        not_found_ids: List[int] = []
        removed_count = 0

        for document_id in document_ids:
            doc = db_service.get_document(document_id)
            if not doc:
                not_found_ids.append(document_id)
                continue

            removed = _remove_backend_file_for_doc(doc)
            if removed:
                removed_count += 1

            results.append({
                "document_id": document_id,
                "filename": doc["filename"],
                "file_removed": removed,
                "backend_file_exists": _document_backend_file_exists(doc),
            })

        return {
            "success": True,
            "requested": len(document_ids),
            "removed": removed_count,
            "not_found_ids": not_found_ids,
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en borrado masivo de archivos backend: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ========== FUNCIÓN DE PROCESAMIENTO EN BACKGROUND ==========

def process_document_background(doc_id: int, filepath: str, filename: str) -> None:
    """
    Procesa documento en background: extrae texto, genera chunks y embeddings.
    Si falla, realiza reintentos automáticos y finalmente marca estado error.
    """
    try:
        logger.info(f"⏳ Iniciando procesamiento de '{filename}'")
        status = ollama_service.get_runtime_status()
        if (not status["ollama_connected"]) or (not status["ready_for_embeddings"]):
            raise RuntimeError(_embedding_service_unavailable_detail())

        attempt = db_service.increment_document_processing_attempts(doc_id)
        db_service.reset_document_processing_state(doc_id)
        db_service.update_document_processing_status(doc_id, 'processing')
        db_service.clear_document_chunks(doc_id)
        doc_info = DocumentProcessor.process_document(filepath, filename)
        chunk_items = doc_info['chunks']
        if not chunk_items:
            raise ValueError("El documento no produjo texto extraíble")

        chunk_texts = [
            chunk['content'] if isinstance(chunk, dict) else chunk
            for chunk in chunk_items
        ]
        logger.info(f"  🔢 Generando {len(chunk_texts)} embeddings...")
        embeddings = ollama_service.generate_batch_embeddings(chunk_texts)
        chunks_with_embeddings = []
        for chunk, embedding in zip(chunk_items, embeddings):
            if isinstance(chunk, dict):
                chunks_with_embeddings.append(
                    (chunk['content'], embedding, chunk.get('page'))
                )
            else:
                chunks_with_embeddings.append((chunk, embedding, None))
        db_service.add_chunks(doc_id, chunks_with_embeddings)
        db_service.update_document_pages(doc_id, doc_info['pages'])
        db_service.update_document_processing_status(doc_id, 'ready')
        db_service.clear_document_processing_error(doc_id)
        logger.info(f"✅ '{filename}' procesado exitosamente")
    except Exception as e:
        logger.error(f"❌ Error procesando '{filename}': {str(e)}")
        message = str(e)
        non_retryable = (
            "falta el modelo de embeddings requerido" in message
            or "No se puede ejecutar 'embeddings'" in message
            or "Ollama no está activo" in message
        )

        if non_retryable:
            try:
                db_service.set_document_processing_error(doc_id, message)
            except Exception:
                pass
            logger.error("Documento '%s' marcado como error no-reintentable por dependencia de modelo", filename)
            return

        current_attempts = 0
        try:
            doc = db_service.get_document(doc_id)
            if doc:
                current_attempts = int(doc.get('processing_attempts') or 0)
        except Exception:
            pass
        if current_attempts < MAX_PROCESSING_RETRIES:
            logger.warning(
                "Reintentando '%s' (documento %s). Intento %s/%s",
                filename,
                doc_id,
                current_attempts + 1,
                MAX_PROCESSING_RETRIES,
            )
            try:
                db_service.reset_document_processing_state(doc_id)
                _enqueue_document_processing(doc_id, filepath, filename)
            except Exception as retry_error:
                logger.error(f"No se pudo reencolar documento {doc_id}: {retry_error}")
                try:
                    db_service.set_document_processing_error(doc_id, str(e))
                except Exception:
                    pass
            return

        try:
            db_service.set_document_processing_error(doc_id, str(e))
        except Exception:
            pass
        logger.error(
            "Documento '%s' marcado como error tras agotar reintentos (%s/%s)",
            filename,
            current_attempts,
            MAX_PROCESSING_RETRIES,
        )