# AGENTS.md — Chat Document AI

## Project overview

Two apps coexist in this repo:

1. **FastAPI + React RAG web app** (newer) — backend in `backend/`, frontend in `frontend/`

**README is outdated** — it only describes the Flet app, not the RAG web app. Trust the code, not the README.

## Architecture

### Backend (`backend/`)
- **FastAPI** on `0.0.0.0:8000` (config in `backend/config.py`)
- **SQLite** via raw `sqlite3` — schema auto-created on first run (`documents.db` in repo root)
- **Ollama** required locally at `http://localhost:11434` — chat model `gemma4:cloud` (Ollama Cloud, needs `ollama signin` + Ollama >= 0.18), embedding model `nomic-embed-text` (local)
- Chat model is overridable via the `OLLAMA_CHAT_MODEL` env var (default `gemma4:cloud`); same for `OLLAMA_HOST` and `OLLAMA_EMBEDDING_MODEL`
- RAG flow: upload doc → extract text → split into chunks (500 word, 50 overlap) → generate embeddings via Ollama → store in SQLite → on question, embed query → cosine similarity search → generate answer with context
- File size limit: 50 MB. Allowed: `.pdf`, `.docx`

### Frontend (`frontend/`)
- **React 18 + Vite 5** on port 3000 (no TypeScript, plain JSX)
- Vite proxies `/api` → `localhost:8000`
- No ESLint config file despite `lint` script in package.json
- `pages/` directory exists but is empty — all components are in `components/`

### Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Backend health check |
| GET | `/api/chat/health` | Ollama + doc count status |
| POST | `/api/chat/ask` | Ask a question (RAG) — body: `{question, top_k?}` |
| POST | `/api/documents/upload` | Upload a PDF/DOCX |
| GET | `/api/documents/list` | List all documents |
| GET | `/api/documents/{id}` | Get document details |
| DELETE | `/api/documents/{id}` | Delete document |
| GET | `/api/documents/search/content?query=` | Full-text search in chunks |

## Commands

```bash
# Backend (from repo root)
pip install -r backend/requirements.txt
python -m uvicorn backend.app:app --reload --port 8000
# or cd backend && python app.py

# Frontend
cd frontend && npm install && npm run dev

```

## Constraints & quirks

- **Ollama must be running** for any RAG operation. If offline, `/api/chat/health` returns `WARNING` and `/api/chat/ask` returns 503.
- SQLite DB (`documents.db`) and uploaded files (`documentos/`) are in the repo root — **add to `.gitignore`** if not already tracked.
- The frontend `eslint` script exists but has no config — running `npm run lint` will fail.
- No test framework or test files exist anywhere in the project.
- The `pages/` directory in frontend is unused / scaffolding.
- Backend uses `sys.path.insert(0, ...)` in `app.py` for imports — running from repo root or `backend/` both work.
- Embeddings are stored as JSON text in SQLite (`embedding_json` column), dimension 768.
