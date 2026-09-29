"""
Servicio de base de datos - Gestiona documentos, chunks y embeddings en SQLite
"""
import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class DatabaseService:
    """
    Servicio para gestionar la base de datos SQLite con documentos y embeddings
    """

    def __init__(self, db_path: str):
        """
        Inicializa el servicio de base de datos
        
        Args:
            db_path: Ruta al archivo SQLite
        """
        self.db_path = db_path
        self._all_chunks_cache: Optional[List[Dict[str, Any]]] = None
        self._chunks_by_doc_cache: Dict[Tuple[int, ...], List[Dict[str, Any]]] = {}
        self.init_database()

    def _invalidate_chunks_cache(self) -> None:
        """Invalida caché en memoria de chunks/embeddings cuando cambia la base."""
        self._all_chunks_cache = None
        self._chunks_by_doc_cache.clear()

    def init_database(self) -> None:
        """Inicializa las tablas de la base de datos"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Tabla de documentos
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT UNIQUE NOT NULL,
                        filepath TEXT NOT NULL,
                        filetype TEXT NOT NULL,
                        filesize INTEGER NOT NULL,
                        pages INTEGER,
                        processing_status TEXT NOT NULL DEFAULT 'ready',
                        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cursor.execute("PRAGMA table_info(documents)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                if 'processing_status' not in existing_columns:
                    cursor.execute("ALTER TABLE documents ADD COLUMN processing_status TEXT NOT NULL DEFAULT 'ready'")
                if 'processing_attempts' not in existing_columns:
                    cursor.execute("ALTER TABLE documents ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0")
                if 'last_error' not in existing_columns:
                    cursor.execute("ALTER TABLE documents ADD COLUMN last_error TEXT")

                # Tabla de chunks
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        document_id INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        embeddings BLOB NOT NULL,
                        page_number INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                    )
                """)

                cursor.execute("PRAGMA table_info(chunks)")
                existing_chunk_columns = {row[1] for row in cursor.fetchall()}
                if 'page_number' not in existing_chunk_columns:
                    cursor.execute("ALTER TABLE chunks ADD COLUMN page_number INTEGER")

                # Tabla de sesiones de chat
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Tabla de mensajes por sesión
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        sources TEXT,
                        confidence REAL,
                        processing_time REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
                    )
                """)

                conn.commit()
                self.cleanup_failed_documents()
                logger.info("Base de datos inicializada correctamente")

        except Exception as e:
            logger.error(f"Error inicializando base de datos: {str(e)}")
            raise

    def add_document(self, filename: str, filepath: str, filetype: str, 
                    filesize: int, pages: int = 0) -> int:
        """
        Agrega un documento a la base de datos
        
        Args:
            filename: Nombre del archivo
            filepath: Ruta del archivo
            filetype: Tipo de archivo (.pdf, .docx)
            filesize: Tamaño en bytes
            pages: Número de páginas
            
        Returns:
            ID del documento creado
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO documents (filename, filepath, filetype, filesize, pages, processing_status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (filename, filepath, filetype, filesize, pages, 'processing'))
                conn.commit()
                doc_id = cursor.lastrowid
                if doc_id is None:
                    raise RuntimeError("No se pudo obtener el ID del documento recién insertado")
                self._invalidate_chunks_cache()
                logger.info(f"Documento agregado: {filename} (ID: {doc_id})")
                return doc_id
        except sqlite3.IntegrityError:
            logger.error(f"Documento ya existe: {filename}")
            raise Exception(f"El archivo '{filename}' ya existe")
        except Exception as e:
            logger.error(f"Error agregando documento: {str(e)}")
            raise

    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """
        Obtiene un documento por ID
        
        Args:
            doc_id: ID del documento
            
        Returns:
            Diccionario con datos del documento o None si no existe
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
                row = cursor.fetchone()
                
                if row:
                    doc = dict(row)
                    if doc.get('processing_status') == 'error':
                        return None
                    return doc
                return None
        except Exception as e:
            logger.error(f"Error obteniendo documento: {str(e)}")
            raise

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """
        Obtiene todos los documentos visibles para el cliente.
        Los registros en estado 'error' se excluyen para evitar que sobrevivan
        a un fallo de procesamiento.
        
        Returns:
            Lista de diccionarios con documentos
        """
        try:
            self.cleanup_failed_documents()
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM documents WHERE COALESCE(processing_status, 'ready') != 'error' ORDER BY uploaded_at DESC"
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error obteniendo documentos: {str(e)}")
            raise

    def cleanup_failed_documents(self) -> int:
        """Elimina registros antiguos en estado error y borra sus archivos físicos."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, filepath, filename FROM documents WHERE processing_status = 'error'")
                failed_docs = cursor.fetchall()

                for doc_id, filepath, filename in failed_docs:
                    try:
                        file_path = Path(filepath)
                        if file_path.exists() and file_path.is_file():
                            file_path.unlink()
                    except Exception:
                        logger.warning(f"No se pudo borrar el archivo físico del documento fallido: {filepath}")

                    cursor.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
                    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    logger.info(f"Documento fallido limpiado: {filename} (ID: {doc_id})")

                conn.commit()
                if failed_docs:
                    self._invalidate_chunks_cache()
                return len(failed_docs)
        except Exception as e:
            logger.error(f"Error limpiando documentos fallidos: {str(e)}")
            raise

    def delete_document(self, doc_id: int) -> bool:
        """
        Elimina un documento y sus chunks, y borra también el archivo físico si existe.
        
        Args:
            doc_id: ID del documento
            
        Returns:
            True si se eliminó exitosamente
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT filepath FROM documents WHERE id = ?", (doc_id,))
                row = cursor.fetchone()
                filepath = row[0] if row else None

                cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                conn.commit()
                self._invalidate_chunks_cache()

                if filepath:
                    try:
                        file_path = self._normalize_file_path(filepath)
                        if file_path.exists() and file_path.is_file():
                            file_path.unlink()
                    except Exception:
                        logger.warning(f"No se pudo borrar el archivo físico del documento {doc_id}: {filepath}")

                logger.info(f"Documento eliminado: ID {doc_id}")
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error eliminando documento: {str(e)}")
            raise

    @staticmethod
    def _normalize_file_path(filepath: str):
        """Normaliza la ruta para borrar archivos físicos."""
        from pathlib import Path
        return Path(filepath)

    def add_chunks(self, doc_id: int, chunks_with_embeddings: List[Tuple]) -> None:
        """
        Agrega chunks de un documento con sus embeddings
        
        Args:
            doc_id: ID del documento
            chunks_with_embeddings: Lista de tuplas (texto, embedding) o
                (texto, embedding, page_number)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                for idx, chunk_row in enumerate(chunks_with_embeddings):
                    chunk_text = chunk_row[0]
                    embedding = chunk_row[1]
                    page_number = chunk_row[2] if len(chunk_row) > 2 else None
                    embedding_bytes = json.dumps(embedding).encode('utf-8')
                    
                    cursor.execute("""
                        INSERT INTO chunks (document_id, content, chunk_index, embeddings, page_number)
                        VALUES (?, ?, ?, ?, ?)
                    """, (doc_id, chunk_text, idx, embedding_bytes, page_number))
                
                conn.commit()
                self._invalidate_chunks_cache()
                logger.info(f"Agregados {len(chunks_with_embeddings)} chunks para doc {doc_id}")
        except Exception as e:
            logger.error(f"Error agregando chunks: {str(e)}")
            raise

    def get_chunks_by_document(self, doc_id: int) -> List[Dict[str, Any]]:
        """
        Obtiene todos los chunks de un documento
        
        Args:
            doc_id: ID del documento
            
        Returns:
            Lista de diccionarios con chunks
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, document_id, content, chunk_index, embeddings, page_number
                    FROM chunks
                    WHERE document_id = ?
                    ORDER BY chunk_index ASC
                """, (doc_id,))
                rows = cursor.fetchall()
                
                chunks: List[Dict[str, Any]] = []
                for row in rows:
                    chunk_dict = dict(row)
                    chunk_dict['embeddings'] = json.loads(chunk_dict['embeddings'])
                    chunks.append(chunk_dict)
                
                return chunks
        except Exception as e:
            logger.error(f"Error obteniendo chunks: {str(e)}")
            raise

    def get_all_chunks(self, document_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        Obtiene todos los chunks de todos los documentos
        
        Returns:
            Lista de diccionarios con chunks
        """
        try:
            if not document_ids and self._all_chunks_cache is not None:
                return [dict(chunk) for chunk in self._all_chunks_cache]

            cache_key: Optional[Tuple[int, ...]] = None
            if document_ids:
                cache_key = tuple(sorted(set(int(doc_id) for doc_id in document_ids)))
                cached = self._chunks_by_doc_cache.get(cache_key)
                if cached is not None:
                    return [dict(chunk) for chunk in cached]

            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                query = """
                    SELECT id, document_id, content, chunk_index, embeddings, page_number
                    FROM chunks
                """
                params: Tuple[Any, ...] = ()

                if document_ids:
                    placeholders = ", ".join(["?"] * len(document_ids))
                    query += f" WHERE document_id IN ({placeholders})"
                    params = tuple(document_ids)

                query += " ORDER BY document_id ASC, chunk_index ASC"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                chunks: List[Dict[str, Any]] = []
                for row in rows:
                    chunk_dict = dict(row)
                    chunk_dict['embeddings'] = json.loads(chunk_dict['embeddings'])
                    chunks.append(chunk_dict)

                if cache_key is None:
                    self._all_chunks_cache = [dict(chunk) for chunk in chunks]
                else:
                    self._chunks_by_doc_cache[cache_key] = [dict(chunk) for chunk in chunks]
                
                return chunks
        except Exception as e:
            logger.error(f"Error obteniendo todos los chunks: {str(e)}")
            raise

    def search_by_content(self, query: str, limit: int = 10, document_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        Busca chunks por contenido de texto (búsqueda simple)
        
        Args:
            query: Término a buscar
            limit: Máximo de resultados
            document_ids: Lista opcional de documentos a acotar
            
        Returns:
            Lista de chunks coincidentes
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                sql = """
                    SELECT c.id, c.document_id, c.content, d.filename
                    FROM chunks c
                    JOIN documents d ON c.document_id = d.id
                    WHERE d.processing_status != 'error' AND c.content LIKE ?
                """
                params: List[Any] = [f"%{query}%"]

                if document_ids:
                    placeholders = ", ".join(["?"] * len(document_ids))
                    sql += f" AND c.document_id IN ({placeholders})"
                    params.extend(document_ids)

                sql += " ORDER BY c.document_id, c.chunk_index LIMIT ?"
                params.append(limit)

                cursor.execute(sql, tuple(params))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error en búsqueda: {str(e)}")
            raise

    def update_document_pages(self, doc_id: int, pages: int) -> None:
        """
        Actualiza el número de páginas de un documento
        
        Args:
            doc_id: ID del documento
            pages: Número de páginas
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE documents
                    SET pages = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (pages, doc_id))
                conn.commit()
                logger.info(f"Documento {doc_id} actualizado: {pages} páginas")
        except Exception as e:
            logger.error(f"Error actualizando documento: {str(e)}")
            raise

    def update_document_processing_status(self, doc_id: int, status: str) -> None:
        """
        Actualiza el estado de procesamiento de un documento

        Args:
            doc_id: ID del documento
            status: Estado nuevo (processing, ready, error)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE documents
                    SET processing_status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, doc_id))
                conn.commit()
                logger.info(f"Documento {doc_id} marcado como: {status}")
        except Exception as e:
            logger.error(f"Error actualizando estado de documento: {str(e)}")
            raise

    def get_documents_by_processing_status(self, status: str) -> List[Dict[str, Any]]:
        """Obtiene documentos por estado de procesamiento."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT *
                    FROM documents
                    WHERE processing_status = ?
                    ORDER BY updated_at ASC
                    """,
                    (status,),
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error obteniendo documentos por estado '{status}': {str(e)}")
            raise

    def increment_document_processing_attempts(self, doc_id: int) -> int:
        """
        Incrementa el contador de intentos de procesamiento y retorna el valor actual.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE documents
                    SET processing_attempts = COALESCE(processing_attempts, 0) + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (doc_id,),
                )
                if cursor.rowcount == 0:
                    raise RuntimeError(f"Documento {doc_id} no encontrado para incrementar intentos")

                cursor.execute(
                    "SELECT processing_attempts FROM documents WHERE id = ?",
                    (doc_id,),
                )
                row = cursor.fetchone()
                conn.commit()

                if row is None:
                    raise RuntimeError(f"No se pudo leer intentos del documento {doc_id}")

                return int(row[0])
        except Exception as e:
            logger.error(f"Error incrementando intentos del documento {doc_id}: {str(e)}")
            raise

    def set_document_processing_error(self, doc_id: int, error_message: str) -> None:
        """Marca el documento en estado error y persiste el último mensaje."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE documents
                    SET processing_status = 'error',
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (error_message[:2000], doc_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error marcando documento {doc_id} como error: {str(e)}")
            raise

    def clear_document_processing_error(self, doc_id: int) -> None:
        """Limpia el último error de procesamiento sin tocar el estado actual."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE documents
                    SET last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (doc_id,),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error limpiando error de procesamiento de documento {doc_id}: {str(e)}")
            raise

    def reset_document_processing_state(self, doc_id: int) -> None:
        """Reestablece el documento a estado processing y limpia último error."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE documents
                    SET processing_status = 'processing',
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (doc_id,),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error reseteando estado de procesamiento de documento {doc_id}: {str(e)}")
            raise

    def clear_document_chunks(self, doc_id: int) -> None:
        """Elimina chunks previos de un documento para permitir reintentos idempotentes."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
                conn.commit()
                self._invalidate_chunks_cache()
        except Exception as e:
            logger.error(f"Error limpiando chunks del documento {doc_id}: {str(e)}")
            raise

    def clear_database(self) -> None:
        """Limpia completamente la base de datos (para testing)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chat_messages")
                cursor.execute("DELETE FROM chat_sessions")
                cursor.execute("DELETE FROM chunks")
                cursor.execute("DELETE FROM documents")
                conn.commit()
                self._invalidate_chunks_cache()
                logger.warning("Base de datos limpiada completamente")
        except Exception as e:
            logger.error(f"Error limpiando base de datos: {str(e)}")
            raise

    def create_chat_session(self, session_id: str, title: str = "Nuevo chat") -> Dict[str, Any]:
        """Crea una sesión de chat si no existe y retorna su resumen."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO chat_sessions (session_id, title)
                    VALUES (?, ?)
                """, (session_id, title))
                conn.commit()

                cursor.execute("""
                    SELECT session_id, title, created_at, updated_at, last_message_at
                    FROM chat_sessions
                    WHERE session_id = ?
                """, (session_id,))
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError("No se pudo crear/obtener la sesión de chat")
                return dict(row)
        except Exception as e:
            logger.error(f"Error creando sesión de chat: {str(e)}")
            raise

    def get_chat_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Lista sesiones de chat con conteo y última actividad."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        s.session_id,
                        s.title,
                        s.created_at,
                        s.updated_at,
                        s.last_message_at,
                        COUNT(m.id) AS messages_count
                    FROM chat_sessions s
                    LEFT JOIN chat_messages m ON m.session_id = s.session_id
                    GROUP BY s.session_id
                    ORDER BY s.last_message_at DESC, s.updated_at DESC
                    LIMIT ?
                """, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error listando sesiones de chat: {str(e)}")
            raise

    def add_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        confidence: Optional[float] = None,
        processing_time: Optional[float] = None,
    ) -> int:
        """Agrega un mensaje a una sesión de chat."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                serialized_sources = json.dumps(sources) if sources is not None else None
                cursor.execute("""
                    INSERT INTO chat_messages (session_id, role, content, sources, confidence, processing_time)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (session_id, role, content, serialized_sources, confidence, processing_time))

                cursor.execute("""
                    UPDATE chat_sessions
                    SET updated_at = CURRENT_TIMESTAMP,
                        last_message_at = CURRENT_TIMESTAMP
                    WHERE session_id = ?
                """, (session_id,))

                conn.commit()
                message_id = cursor.lastrowid
                if message_id is None:
                    raise RuntimeError("No se pudo obtener el ID del mensaje")
                return message_id
        except Exception as e:
            logger.error(f"Error agregando mensaje de chat: {str(e)}")
            raise

    def get_chat_messages(self, session_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Obtiene mensajes de una sesión de chat en orden cronológico."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, session_id, role, content, sources, confidence, processing_time, created_at
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (session_id, limit))
                rows = cursor.fetchall()

                messages: List[Dict[str, Any]] = []
                for row in reversed(rows):
                    message = dict(row)
                    raw_sources = message.get('sources')
                    message['sources'] = json.loads(raw_sources) if raw_sources else []
                    messages.append(message)

                return messages
        except Exception as e:
            logger.error(f"Error obteniendo mensajes de chat: {str(e)}")
            raise

    def get_recent_chat_turns(self, session_id: str, limit: int = 6) -> List[Dict[str, str]]:
        """Obtiene los últimos turnos (role/content) para contexto conversacional."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT role, content
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (session_id, limit))
                rows = cursor.fetchall()
                return [dict(row) for row in reversed(rows)]
        except Exception as e:
            logger.error(f"Error obteniendo turnos recientes de chat: {str(e)}")
            raise

    def get_chat_user_message_count(self, session_id: str) -> int:
        """Cuenta mensajes de usuario en una sesión."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM chat_messages
                    WHERE session_id = ? AND role = 'user'
                """, (session_id,))
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.error(f"Error contando mensajes de usuario: {str(e)}")
            raise

    def update_chat_session_title(self, session_id: str, title: str) -> None:
        """Actualiza el título de una sesión de chat."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE chat_sessions
                    SET title = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE session_id = ?
                """, (title, session_id))
                conn.commit()
        except Exception as e:
            logger.error(f"Error actualizando título de chat: {str(e)}")
            raise

    def delete_chat_session(self, session_id: str) -> bool:
        """Elimina una sesión completa de chat y sus mensajes."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
                cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error eliminando sesión de chat: {str(e)}")
            raise

    def clear_chat_session_messages(self, session_id: str) -> None:
        """Elimina mensajes de una sesión y conserva la sesión."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
                cursor.execute("""
                    UPDATE chat_sessions
                    SET updated_at = CURRENT_TIMESTAMP,
                        last_message_at = CURRENT_TIMESTAMP
                    WHERE session_id = ?
                """, (session_id,))
                conn.commit()
        except Exception as e:
            logger.error(f"Error limpiando mensajes de sesión: {str(e)}")
            raise

    def get_chat_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas globales de sesiones y mensajes de chat."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM chat_sessions")
                sessions = int(cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM chat_messages")
                messages = int(cursor.fetchone()[0])
                return {
                    "active_sessions": sessions,
                    "total_messages": messages,
                    "avg_messages_per_session": round(messages / sessions, 2) if sessions else 0,
                }
        except Exception as e:
            logger.error(f"Error obteniendo estadísticas de chat: {str(e)}")
            raise