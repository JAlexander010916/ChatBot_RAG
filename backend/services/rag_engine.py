"""
Motor RAG - Recuperación Aumentada Generativa
Combina búsqueda semántica con generación de lenguaje
"""
import numpy as np
import re
import logging
import time
from typing import List, Tuple, Dict, Any, Optional

from .database_service import DatabaseService
from .ollama_service import OllamaService

logger = logging.getLogger(__name__)


class RAGEngine:
    """
    Motor de Recuperación Aumentada Generativa (RAG)
    """

    def __init__(self, db_service: DatabaseService, ollama_service: OllamaService):
        """
        Inicializa el motor RAG
        
        Args:
            db_service: Servicio de base de datos
            ollama_service: Servicio de Ollama
        """
        self.db = db_service
        self.ollama = ollama_service
        # Pesos del score híbrido final: alfa*coseno + beta*match textual.
        self.hybrid_alpha = 0.65
        self.hybrid_beta = 0.35
        # Umbrales sobre el score híbrido final.
        self.min_relevance_threshold = 0.12
        self.min_medium_relevance = 0.06
        self.min_weak_relevance = 0.02
        self.min_chunk_for_context = 0.04
        self.min_medium_chunk_count = 1
        self.min_overlap_for_fallback = 0.15
        self.snippet_chars = 280

    def answer_question(
        self,
        question: str,
        top_k: int = 15,
        document_ids: Optional[List[int]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        try:
            question_started_at = time.perf_counter()
            logger.info(f"Procesando pregunta: {question}")

            relevant_chunks = self.retrieve_relevant_chunks(
                question,
                top_k=top_k,
                document_ids=document_ids,
            )

            if not relevant_chunks:
                return {
                    'answer': 'No se encontraron documentos relevantes para tu pregunta.',
                    'sources': [],
                    'confidence': 0.0
                }

            evidence = self._evaluate_evidence(relevant_chunks)
            answer_mode = "abstention"

            # Quitar el bloqueo por evidencia débil:
            # Si hay chunks, se intenta responder aunque la similitud sea baja.
            if not self._has_question_anchor(question, relevant_chunks):
                logger.info(
                    "Sin ancla fuerte de pregunta, pero se intenta generar con el contexto disponible | "
                    "top=%.4f | medium_hits=%d",
                    evidence['top_score'],
                    evidence['medium_hits'],
                )

            if evidence['allow_generation']:
                context = self._build_context(relevant_chunks)

                generation_started_at = time.perf_counter()
                try:
                    answer = self.ollama.generate_response(
                        question,
                        context=context,
                        conversation_history=conversation_history,
                    )
                except Exception as generation_error:
                    logger.warning(f"Fallo en generación con Ollama; se usa fallback extractivo: {generation_error}")
                    answer, extracted = self._build_extractive_fallback_answer(question, relevant_chunks)
                    answer_mode = 'fallback_extractive' if extracted else 'abstention'
                else:
                    generation_time_ms = (time.perf_counter() - generation_started_at) * 1000
                    logger.info(f"Tiempo de generación: {generation_time_ms:.2f} ms")
                    answer_mode = 'generation'

                if not answer.strip() or self._looks_like_unusable_answer(answer):
                    logger.warning("Respuesta generativa no utilizable; se usa fallback extractivo")
                    answer, extracted = self._build_extractive_fallback_answer(question, relevant_chunks)
                    answer_mode = 'fallback_extractive' if extracted else 'abstention'
            else:
                answer, extracted = self._build_extractive_fallback_answer(question, relevant_chunks)
                answer_mode = 'fallback_extractive' if extracted else 'abstention'
                logger.info(
                    "Modo respuesta=fallback | motivo=%s | top=%.4f | medium_hits=%d",
                    evidence['reason'],
                    evidence['top_score'],
                    evidence['medium_hits'],
                )

            sources = self._extract_sources(relevant_chunks)
            confidence = self._calculate_confidence(relevant_chunks)

            total_time_ms = (time.perf_counter() - question_started_at) * 1000
            logger.info(
                "✓ Respuesta lista | modo=%s | confianza=%.2f%% | tiempo_total=%.2f ms",
                answer_mode,
                confidence * 100,
                total_time_ms,
            )

            return {
                'answer': answer,
                'sources': sources,
                'confidence': confidence
            }

        except Exception as e:
            logger.error(f"Error generando respuesta: {str(e)}")
            raise

    def retrieve_relevant_chunks(
        self,
        question: str,
        top_k: int = 15,
        document_ids: Optional[List[int]] = None,
    ) -> List[Tuple[Dict[str, Any], float]]:
        try:
            embedding_started_at = time.perf_counter()
            question_embedding = self.ollama.generate_embedding(question)
            embedding_time_ms = (time.perf_counter() - embedding_started_at) * 1000
            question_terms = self._extract_terms(question)

            retrieval_started_at = time.perf_counter()
            all_chunks = self.db.get_all_chunks(document_ids=document_ids)

            if not all_chunks:
                logger.warning("No hay chunks en la base de datos")
                return []
            
            # Similitud coseno vectorizada sobre todos los fragmentos.
            cosine_scores = self._batch_cosine_similarity(question_embedding, all_chunks)
            
            # Coincidencia textual por solapamiento de terminos.
            textual_scores = [self._textual_match_score(question_terms, chunk.get('content', '')) for chunk in all_chunks]
            
            # Puntuacion hibrida: alfa * coseno + beta * coincidencia textual.
            scores = [self._hybrid_score(cosine, textual) for cosine, textual in zip(cosine_scores, textual_scores)]
            ranked = sorted(
                zip(all_chunks, scores),
                key=lambda item: item[1],
                reverse=True
            )

            # No se descartan fragmentos por puntuacion baja.
            top_chunks = [
                (chunk, float(score))
                for chunk, score in ranked[:top_k]
            ]

            retrieval_time_ms = (time.perf_counter() - retrieval_started_at) * 1000
            top_scores = [round(float(score), 4) for _, score in ranked[:min(top_k, 5)]]
            logger.info(
                "RAG retrieval | emb_ms=%.2f | rank_ms=%.2f | chunks_eval=%d | chunks_ctx=%d | top_scores=%s",
                embedding_time_ms,
                retrieval_time_ms,
                len(all_chunks),
                len(top_chunks),
                top_scores,
            )

            return top_chunks

        except Exception as e:
            logger.error(f"Error recuperando chunks: {str(e)}")
            raise
        
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Similitud coseno cruda entre dos vectores (rango típico -1 a 1)."""
        try:
            scores = self._batch_cosine_similarity(vec1, [{'embeddings': vec2}])
            return float(scores[0]) if len(scores) else 0.0
        except Exception as e:
            logger.error(f"Error calculando similitud: {str(e)}")
            return 0.0

    def _batch_cosine_similarity(self, query_vec: List[float], chunks: List[Dict[str, Any]]) -> np.ndarray:
        """Calcula similitud coseno de una consulta contra todos los chunks."""
        query = np.asarray(query_vec, dtype=np.float32)
        matrix = np.asarray([chunk['embeddings'] for chunk in chunks], dtype=np.float32)

        query_norm = np.linalg.norm(query)
        chunk_norms = np.linalg.norm(matrix, axis=1)
        denom = chunk_norms * query_norm
        dots = matrix @ query
        scores = np.divide(dots, denom, out=np.zeros_like(dots), where=denom > 0)
        return scores

    def _extract_terms(self, text: str) -> List[str]:
        """Extrae términos simples para la coincidencia textual."""
        return [term for term in re.findall(r"\b\w+\b", (text or '').lower()) if len(term) > 2]

    def _textual_match_score(self, question_terms: List[str], content: str) -> float:
        """Calcula una coincidencia textual simple basada en solapamiento de términos."""
        if not question_terms or not content:
            return 0.0

        content_terms = set(self._extract_terms(content))
        if not content_terms:
            return 0.0

        question_term_set = set(question_terms)
        overlap = question_term_set.intersection(content_terms)
        return len(overlap) / len(question_term_set) if question_term_set else 0.0

    def _hybrid_score(self, cosine_score: float, textual_score: float) -> float:
        """Combina score semántico y textual en un único ranking."""
        return float((self.hybrid_alpha * float(cosine_score)) + (self.hybrid_beta * float(textual_score)))

    def _build_context(self, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> str:
        """
        Construye un contexto a partir de los chunks relevantes
        
        Args:
            relevant_chunks: Lista de tuplas (chunk, similarity)
            
        Returns:
            Texto del contexto
        """
        try:
            context_parts: List[str] = []

            for chunk, similarity in relevant_chunks:
                doc = self.db.get_document(chunk['document_id'])
                filename = doc['filename'] if doc else 'Unknown'
                page = chunk.get('page_number')
                page_label = f", p. {page}" if page else ""
                context_parts.append(
                    f"[De {filename}{page_label} | similitud {similarity:.2f}] {chunk['content']}"
                )

            context = "\n\n".join(context_parts)
            logger.debug(f"Contexto construido: {len(context)} caracteres")

            return context

        except Exception as e:
            logger.error(f"Error construyendo contexto: {str(e)}")
            return ""

    def _looks_like_unusable_answer(self, answer: str) -> bool:
        """Detecta respuestas vacías o con razonamiento interno visible."""
        normalized_answer = (answer or '').strip().lower()
        if not normalized_answer:
            return True

        # Si la respuesta EMPIEZA por razonamiento, no es utilizable.
        reasoning_prefixes = (
            "okay",
            "let's",
            "voy a analizar",
            "el usuario me está pidiendo",
            "analizando el contexto",
        )
        if normalized_answer.startswith(reasoning_prefixes):
            return True

        # Razonamiento interno filtrado en cualquier punto de la respuesta.
        # OJO: conectores como "en el contexto" o "por lo tanto" son español
        # normal en una buena respuesta ("Basado en el contexto proporcionado...")
        # y NO deben descartar la respuesta completa.
        leaked_reasoning_markers = (
            "el usuario me está pidiendo",
            "analizando el contexto",
        )
        return any(marker in normalized_answer for marker in leaked_reasoning_markers)

    def _build_extractive_fallback_answer(self, question: str, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> Tuple[str, bool]:
        """Construye una respuesta legible a partir de oraciones de los chunks cuando el modelo no coopera."""
        question_terms = set(self._extract_terms(question))
        candidate_sentences: List[Tuple[float, str]] = []

        for chunk, similarity in relevant_chunks[:4]:
            normalized_content = re.sub(r"\s+", " ", (chunk.get('content') or '')).strip()
            if not normalized_content:
                continue

            sentences = re.split(r"(?<=[.!?])\s+", normalized_content)
            for sentence in sentences:
                cleaned_sentence = sentence.strip(" -\t\n\r\"“”")
                if len(cleaned_sentence) < 50 or len(cleaned_sentence) > 360:
                    continue
                if not cleaned_sentence or not cleaned_sentence[0].isupper():
                    continue

                sentence_terms = set(self._extract_terms(cleaned_sentence))
                if not sentence_terms:
                    continue

                overlap_ratio = len(question_terms.intersection(sentence_terms)) / max(1, len(question_terms))
                length_tokens = len(cleaned_sentence.split())
                if length_tokens < 8:
                    length_score = 0.0
                elif length_tokens <= 32:
                    length_score = 0.25
                elif length_tokens <= 50:
                    length_score = 0.1
                else:
                    length_score = 0.0

                score = (float(similarity) * 1.5) + overlap_ratio + length_score
                if overlap_ratio <= 0 and similarity < self.min_medium_relevance:
                    continue
                candidate_sentences.append((score, cleaned_sentence))

        if candidate_sentences:
            candidate_sentences.sort(key=lambda item: item[0], reverse=True)
            unique_sentences: List[str] = []
            seen = set()

            for _score, sentence in candidate_sentences:
                if sentence in seen:
                    continue
                seen.add(sentence)
                unique_sentences.append(sentence)
                if len(unique_sentences) == 2:
                    break

            if unique_sentences:
                joined = " ".join(unique_sentences)
                return f"Según los documentos, {joined}", True

        logger.info(
            "Fallback extractivo sin oraciones utilizables | question_terms=%d | chunks=%d",
            len(question_terms),
            len(relevant_chunks),
        )
        return "No encuentro información suficiente en los documentos para responder con fiabilidad.", False

    def _extract_sources(self, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> List[Dict[str, Any]]:
        """
        Extrae documentos fuente únicos de los chunks
        
        Args:
            relevant_chunks: Lista de tuplas (chunk, similarity)
            
        Returns:
            Lista de diccionarios con info de documentos
        """
        try:
            sources_dict: Dict[int, Dict[str, Any]] = {}

            for chunk, similarity in relevant_chunks:
                doc_id = chunk['document_id']

                if doc_id not in sources_dict or similarity > sources_dict[doc_id]['relevance']:
                    doc = self.db.get_document(doc_id)
                    if doc:
                        snippet = (chunk.get('content') or '').strip()
                        if len(snippet) > self.snippet_chars:
                            snippet = snippet[:self.snippet_chars].rstrip() + '…'
                        sources_dict[doc_id] = {
                            'id': doc['id'],
                            'filename': doc['filename'],
                            'filetype': doc['filetype'],
                            'relevance': round(float(similarity), 4),
                            'page': chunk.get('page_number'),
                            'chunk_index': chunk.get('chunk_index'),
                            'snippet': snippet
                        }

            sources = sorted(
                sources_dict.values(),
                key=lambda item: item['relevance'],
                reverse=True
            )
            logger.debug(f"Extraídas {len(sources)} fuentes únicas")

            return sources

        except Exception as e:
            logger.error(f"Error extrayendo fuentes: {str(e)}")
            return []

    def _calculate_confidence(self, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> float:
        """
        Calcula la confianza basada en similaridades de chunks relevantes
        
        Args:
            relevant_chunks: Lista de tuplas (chunk, similarity)
            
        Returns:
            Confianza (0-1)
        """
        try:
            if not relevant_chunks:
                return 0.0

            # La confianza es la mejor similitud recuperada, no un promedio inflado.
            confidence = max(sim for _, sim in relevant_chunks)
            confidence = max(0.0, min(1.0, float(confidence)))

            logger.debug(f"Confianza calculada: {confidence:.2%}")

            return confidence

        except Exception as e:
            logger.error(f"Error calculando confianza: {str(e)}")
            return 0.0

    def _has_enough_evidence(self, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> bool:
        """Valida si la recuperación tiene evidencia mínima para responder."""
        evidence = self._evaluate_evidence(relevant_chunks)
        return not evidence['abstain'] and (evidence['allow_generation'] or evidence['allow_fallback'])

    def _evaluate_evidence(self, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> Dict[str, Any]:
        """Evalúa evidencia sin bloquear la respuesta si hay chunks disponibles."""
        if not relevant_chunks:
            return {
                'abstain': True,
                'allow_generation': False,
                'allow_fallback': False,
                'top_score': 0.0,
                'medium_hits': 0,
                'reason': 'no_chunks'
            }

        scores = [float(similarity) for _, similarity in relevant_chunks]
        top_score = scores[0]
        medium_hits = sum(1 for score in scores if score >= self.min_medium_relevance)

        # Sin bloqueo por score bajo: si hay chunks, se intenta responder.
        return {
            'abstain': False,
            'allow_generation': True,
            'allow_fallback': True,
            'top_score': top_score,
            'medium_hits': medium_hits,
            'reason': 'forced_generation'
        }
        
    def _has_question_anchor(self, question: str, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> bool:
        """Permite responder con contexto útil aunque el solapamiento textual sea bajo."""
        if not relevant_chunks:
            return False

        question_terms = self._extract_terms(question)
        if not question_terms:
            return True

        top_score = max((float(score) for _, score in relevant_chunks), default=0.0)
        if top_score >= self.min_chunk_for_context:
            return True

        return False

    def generate_answer(self, question: str, relevant_chunks: List[Tuple[Dict[str, Any], float]]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Genera una respuesta y retorna fuentes
        
        Args:
            question: La pregunta
            relevant_chunks: Chunks relevantes recuperados
            
        Returns:
            Tupla (respuesta, fuentes)
        """
        try:
            context = self._build_context(relevant_chunks)
            answer = self.ollama.generate_response(question, context=context)
            sources = self._extract_sources(relevant_chunks)

            return answer, sources

        except Exception as e:
            logger.error(f"Error generando respuesta: {str(e)}")
            raise

    def get_system_stats(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas del sistema RAG
        
        Returns:
            Diccionario con estadísticas
        """
        try:
            documents = self.db.get_all_documents()
            chunks = self.db.get_all_chunks()

            return {
                'total_documents': len(documents),
                'total_chunks': len(chunks),
                'avg_chunks_per_doc': len(chunks) / len(documents) if documents else 0,
                'chunk_embedding_dimension': 768,
                'ollama_connected': self.ollama.check_connection(),
                'embedding_model': self.ollama.embedding_model,
                'OLLAMA_CHAT_MODEL': self.ollama.chat_model
            }

        except Exception as e:
            logger.error(f"Error obteniendo stats: {str(e)}")
            return {}