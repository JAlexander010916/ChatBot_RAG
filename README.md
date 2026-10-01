# Chat Document AI

Aplicación para cargar documentos PDF, DOCX, DOC generar embeddings locales con Ollama y hacer preguntas sobre el contenido mediante una interfaz web.

## Estructura del proyecto

- `backend/` - API FastAPI y lógica RAG
- `frontend/` - Interfaz React + Vite
- `documentos/` - Carpeta local donde se guardan los documentos cargados
- `documents.db` - Base SQLite creada automáticamente al arrancar el backend

## Requisitos

- Python 3.10+ recomendado
- Node.js 18+ recomendado
- Ollama ejecutándose en `http://localhost:11434`
- Modelos requeridos en Ollama:
	- `gemma4:cloud` (chat, servido por Ollama Cloud)
	- `nomic-embed-text` (embeddings, local)

## Requisito de modelos (obligatorio)

- El backend está configurado para usar por defecto:
	- `OLLAMA_CHAT_MODEL = gemma4:cloud`
	- `OLLAMA_EMBEDDING_MODEL = nomic-embed-text`
- Los tres valores se pueden sobrescribir con las variables de entorno
  `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBEDDING_MODEL` y `OLLAMA_HOST`
  (útil para volver a un modelo local sin tocar código).
- No existe fallback automático a otros modelos.

### Chat en la nube (Ollama Cloud)

El chat usa `gemma4:cloud`, que se consume a través del Ollama local
(`http://localhost:11434`); el servidor se encarga de autenticar contra ollama.com.
Prerrequisitos:

1. **Ollama >= 0.18** (verifica con `ollama --version`). En versiones anteriores los
   tags `*-cloud` devuelven `404 model not found`.
2. **Sesión iniciada** en ollama.com:
   ```bash
   ollama signin
   ```
3. **Registrar el modelo cloud:**
   ```bash
   ollama pull gemma4:cloud
   ```

Verificación recomendada:

```bash
ollama list
```

Debes ver ambos modelos en la salida. Si falta alguno, el sistema responderá con error explícito (`503` en chat, error de procesamiento en documentos).

> **Coste:** `gemma4` se factura por tokens de consumo ($0.14 in / $0.40 out por 1M).
> Cada pregunta RAG reenvía el contexto de los chunks en cada request, así que el
> input domina el coste (~$0.0004 por pregunta con `top_k=5`). El plan Free incluye
> créditos iniciales y limita a 1 request concurrente.
> **Privacidad:** el contexto de los documentos viaja a la nube de Ollama.

## Instalación

### Backend

```bash
pip install -r backend/requirements.txt
```

### Frontend

```bash
cd frontend
npm install
```

## Ejecución

### 1. Iniciar Ollama

**Para que el modelo corra tienes que poner un VPN

```bash
ollama run gemma4:cloud
```

### 2. Iniciar backend

```bash
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

### 3. Iniciar frontend

```bash
cd frontend
npm run dev
```

### 4. Abrir la app

- Frontend: `http://localhost:3000`
- Backend docs: `http://localhost:8000/docs`

## Flujo de uso

1. Carga uno o varios documentos PDF/DOCX/DOC.
2. Espera el estado de carga de embeddings.
3. Cuando el sistema indique que los embeddings están listos, ya puedes hacer preguntas.

## Endpoints principales

### Documentos

- `POST /api/documents/upload`
- `POST /api/documents/load-all`
- `GET /api/documents/`
- `GET /api/documents/{document_id}`
- `DELETE /api/documents/{document_id}`
- `GET /api/documents/stats`

### Chat

- `POST /api/chat/ask`
- `GET /api/chat/health`
- `GET /api/chat/stats`

## Notas

- La base de datos y los documentos cargados se generan localmente.
- El flujo de embeddings depende de Ollama; si no está activo o falta `nomic-embed-text`, el procesamiento de documentos fallará con mensaje claro.
- Si Ollama está caído, falta `gemma4:cloud` o no hay sesión iniciada en ollama.com, `/api/chat/ask` y `/api/chat/ask/stream` devolverán `503` con detalle explícito.

