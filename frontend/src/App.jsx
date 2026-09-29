import { useState, useEffect, useCallback } from 'react';
import { api } from './services/api';
import Header from './components/Header';
import ChatBox from './components/ChatBox';
import DocumentUpload from './components/DocumentUpload';
import DocumentList from './components/DocumentList';
import ChatSessionsPanel from './components/ChatSessionsPanel';
import './App.css';

const CHAT_SESSION_STORAGE_KEY = 'chat_document_ai_session_id';
const CHAT_HISTORY_STORAGE_KEY_PREFIX = 'chat_document_ai_history';
const CHAT_HISTORY_SCHEMA_VERSION = 1;
const MAX_STORED_MESSAGES = 100;

const getChatHistoryStorageKey = (sessionId) => `${CHAT_HISTORY_STORAGE_KEY_PREFIX}:${sessionId}`;

const normalizeStoredMessage = (message) => ({
  ...message,
  timestamp: message?.timestamp ? new Date(message.timestamp) : new Date(),
});

const trimMessages = (messages) => messages.slice(-MAX_STORED_MESSAGES);

const loadStoredMessages = (sessionId) => {
  if (!sessionId) {
    return [];
  }

  try {
    const rawHistory = window.localStorage.getItem(getChatHistoryStorageKey(sessionId));
    if (!rawHistory) {
      return [];
    }

    const parsedHistory = JSON.parse(rawHistory);
    if (
      parsedHistory?.version !== CHAT_HISTORY_SCHEMA_VERSION
      || parsedHistory?.session_id !== sessionId
      || !Array.isArray(parsedHistory?.messages)
    ) {
      return [];
    }

    return trimMessages(parsedHistory.messages.map(normalizeStoredMessage));
  } catch (error) {
    console.error('Error restaurando historial del chat:', error);
    return [];
  }
};

const persistMessages = (sessionId, messages) => {
  if (!sessionId) {
    return;
  }

  const trimmedMessages = trimMessages(messages);
  const payload = {
    version: CHAT_HISTORY_SCHEMA_VERSION,
    session_id: sessionId,
    updated_at: new Date().toISOString(),
    messages: trimmedMessages.map((message) => ({
      ...message,
      timestamp: message.timestamp instanceof Date ? message.timestamp.toISOString() : message.timestamp,
    })),
  };

  try {
    window.localStorage.setItem(getChatHistoryStorageKey(sessionId), JSON.stringify(payload));
  } catch (error) {
    console.error('Error guardando historial del chat:', error);
  }
};

const getOrCreateSessionId = () => {
  const existingSessionId = window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
  if (existingSessionId) {
    return existingSessionId;
  }

  const sessionId = window.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, sessionId);
  return sessionId;
};

export default function App() {
  const [sessionId, setSessionId] = useState(getOrCreateSessionId);
  const [messages, setMessages] = useState(() => loadStoredMessages(getOrCreateSessionId()));
  const [chatSessions, setChatSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showUpload, setShowUpload] = useState(false);
  const [systemHealth, setSystemHealth] = useState(null);
  const [stats, setStats] = useState(null);
  const [selectedSource, setSelectedSource] = useState(null);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [selectedDocumentLoading, setSelectedDocumentLoading] = useState(false);
  const [selectedDocumentError, setSelectedDocumentError] = useState(null);
  const [streamAbortController, setStreamAbortController] = useState(null);
  const [streamingMessageId, setStreamingMessageId] = useState(null);
  const [streamFirstTokenMs, setStreamFirstTokenMs] = useState(null);

  const loadChatSessions = useCallback(async () => {
    try {
      const sessions = await api.getChatSessions();
      setChatSessions(sessions);
      return sessions;
    } catch (err) {
      console.error('Error cargando chats:', err);
      return [];
    }
  }, []);

  const loadSessionMessages = useCallback(async (targetSessionId) => {
    try {
      const payload = await api.getChatSession(targetSessionId);
      const dbMessages = Array.isArray(payload?.messages) ? payload.messages : [];
      const normalizedMessages = trimMessages(dbMessages.map((message) => ({
        ...message,
        timestamp: message?.timestamp ? new Date(message.timestamp) : new Date(),
      })));
      setMessages(normalizedMessages);
      persistMessages(targetSessionId, normalizedMessages);
    } catch (err) {
      console.error('Error cargando mensajes del chat:', err);
      setMessages([]);
    }
  }, []);

  useEffect(() => {
    const initializeSession = async () => {
      setSessionsLoading(true);
      try {
        const sessions = await loadChatSessions();
        if (sessions.length === 0) {
          const created = await api.createChatSession('Nuevo chat');
          setSessionId(created.session_id);
          window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, created.session_id);
          setChatSessions([{
            ...created,
            messages_count: 0,
          }]);
          setMessages([]);
          return;
        }

        const hasCurrentSession = sessions.some((session) => session.session_id === sessionId);
        const activeSession = hasCurrentSession ? sessionId : sessions[0].session_id;

        if (activeSession !== sessionId) {
          setSessionId(activeSession);
          window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, activeSession);
        }

        await loadSessionMessages(activeSession);
      } catch (err) {
        console.error('Error inicializando chats:', err);
      } finally {
        setSessionsLoading(false);
      }
    };

    initializeSession();
  }, [loadChatSessions, loadSessionMessages, sessionId]);

  const loadDocuments = useCallback(async () => {
    try {
      const docs = await api.getDocuments();
      const visibleDocs = docs.filter(
        (doc) => doc.backend_file_exists !== false && doc.processing_status !== 'error'
      );
      setDocuments(visibleDocs);
      const docStats = await api.getDocStats();
      setStats(docStats);
    } catch (err) {
      console.error('Error cargando documentos:', err);
    }
  }, []);

  const checkHealth = useCallback(async () => {
    try {
      const health = await api.getChatHealth();
      setSystemHealth(health);
      if (!health?.ollama) {
        setError('⚠️ Ollama no está conectado. Asegurate de ejecutar: ollama serve');
      } else if (Array.isArray(health?.missing_required_models) && health.missing_required_models.length > 0) {
        setError(
          `⚠️ Faltan modelos requeridos en Ollama: ${health.missing_required_models.join(', ')}. `
          + `Modelos esperados: chat=${health.configured_chat_model}, embeddings=${health.configured_embedding_model}.`
        );
      } else if (error && (error.includes('Ollama') || error.includes('Faltan modelos requeridos'))) {
        setError(null);
      }
    } catch (err) {
      console.error('Error en health check:', err);
    }
  }, [error]);

  const loadInitialData = useCallback(async () => {
    try {
      await loadDocuments();
      await checkHealth();
    } catch (err) {
      setError('Error al cargar datos iniciales');
      console.error(err);
    }
  }, [checkHealth, loadDocuments]);

  useEffect(() => {
    loadInitialData();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, [checkHealth, loadInitialData]);

  useEffect(() => {
    const hasProcessing = documents.some((doc) => doc.processing_status === 'processing');
    if (!hasProcessing) return undefined;
    const interval = setInterval(loadDocuments, 2000);
    return () => clearInterval(interval);
  }, [documents, loadDocuments]);

  useEffect(() => {
    setSelectedDocumentIds((currentIds) => {
      const selectableIds = new Set(
        documents
          .filter((doc) => doc.processing_status === 'ready' || !doc.processing_status)
          .map((doc) => doc.id)
      );
      const nextIds = currentIds.filter((docId) => selectableIds.has(docId));
      return nextIds.length === currentIds.length ? currentIds : nextIds;
    });
  }, [documents]);

  useEffect(() => {
    persistMessages(sessionId, messages);
  }, [messages, sessionId]);

  const appendMessage = (message) => {
    setMessages((prev) => trimMessages([...prev, message]));
  };

  const updateMessageById = (messageId, updater) => {
    setMessages((prev) => prev.map((msg) => (msg.id === messageId ? updater(msg) : msg)));
  };

  const handleToggleDocumentFilter = (docId) => {
    setSelectedDocumentIds((currentIds) => (
      currentIds.includes(docId)
        ? currentIds.filter((currentId) => currentId !== docId)
        : [...currentIds, docId]
    ));
  };

  const handleSendMessage = async (question) => {
    if (streamAbortController) {
      return;
    }

    setLoading(true);
    setError(null);
    setStreamFirstTokenMs(null);

    const userMessageId = Date.now();
    const assistantMessageId = userMessageId + 1;
    const startedAt = performance.now();
    const abortController = new AbortController();

    setStreamAbortController(abortController);
    setStreamingMessageId(assistantMessageId);

    try {
      appendMessage({
        id: userMessageId,
        role: 'user',
        content: question,
        timestamp: new Date()
      });

      appendMessage({
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        sources: [],
        confidence: 0,
        processing_time: null,
        isStreaming: true,
        timestamp: new Date()
      });

      await api.askQuestionStream(question, {
        topK: 5,
        documentIds: selectedDocumentIds,
        sessionId,
        signal: abortController.signal,
        onStart: (payload) => {
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            sources: payload?.sources || [],
            confidence: typeof payload?.confidence === 'number' ? payload.confidence : 0,
          }));
        },
        onToken: (token) => {
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            content: `${message.content || ''}${token}`,
          }));

          setStreamFirstTokenMs((current) => {
            if (current !== null) return current;
            return Math.max(0, Math.round(performance.now() - startedAt));
          });
        },
        onDone: (payload) => {
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            content: payload?.answer || message.content,
            sources: payload?.sources || message.sources || [],
            confidence: typeof payload?.confidence === 'number' ? payload.confidence : message.confidence,
            processing_time: payload?.processing_time ?? message.processing_time,
            first_token_ms: payload?.first_token_ms ?? message.first_token_ms,
            isStreaming: false,
          }));

          if (typeof payload?.first_token_ms === 'number') {
            setStreamFirstTokenMs(Math.round(payload.first_token_ms));
          }
        },
        onError: (payload) => {
          throw new Error(payload?.detail || 'Error en stream de respuesta');
        },
      });
    } catch (err) {
      if (err?.name === 'AbortError') {
        updateMessageById(assistantMessageId, (message) => ({
          ...message,
          isStreaming: false,
          content: message.content || '⏹️ Respuesta cancelada por el usuario.',
        }));
      } else {
        updateMessageById(assistantMessageId, (message) => ({
          ...message,
          isStreaming: false,
          content: message.content || 'No se pudo completar la respuesta en streaming.',
        }));
        setError(err.message);
      }
      console.error(err);
    } finally {
      setLoading(false);
      setStreamAbortController(null);
      setStreamingMessageId(null);
      await loadChatSessions();
    }
  };

  const handleCancelStreaming = () => {
    if (!streamAbortController) {
      return;
    }

    streamAbortController.abort();
  };

  const handleCreateSession = async () => {
    try {
      setSessionsLoading(true);
      const created = await api.createChatSession('Nuevo chat');
      setSessionId(created.session_id);
      window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, created.session_id);
      setMessages([]);
      await loadChatSessions();
    } catch (err) {
      setError(err.message || 'No se pudo crear el chat');
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleSelectSession = async (targetSessionId) => {
    if (!targetSessionId || targetSessionId === sessionId) {
      return;
    }
    try {
      setSessionsLoading(true);
      setSessionId(targetSessionId);
      window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, targetSessionId);
      await loadSessionMessages(targetSessionId);
    } catch (err) {
      setError(err.message || 'No se pudo abrir el chat');
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleDeleteSession = async (targetSessionId) => {
    if (!targetSessionId) {
      return;
    }
    if (!window.confirm('¿Eliminar este chat completo?')) {
      return;
    }

    try {
      setSessionsLoading(true);
      await api.deleteChatSession(targetSessionId);
      const sessions = await loadChatSessions();

      if (sessions.length === 0) {
        const created = await api.createChatSession('Nuevo chat');
        setSessionId(created.session_id);
        window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, created.session_id);
        setMessages([]);
        await loadChatSessions();
        return;
      }

      if (targetSessionId === sessionId) {
        const nextSessionId = sessions[0].session_id;
        setSessionId(nextSessionId);
        window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, nextSessionId);
        await loadSessionMessages(nextSessionId);
      }
    } catch (err) {
      setError(err.message || 'No se pudo eliminar el chat');
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleUploadSuccess = async () => {
    setShowUpload(false);
    await loadDocuments();
    appendMessage({
      id: Date.now(),
      role: 'system',
      content: '✓ Documento cargado y embeddings listos',
      timestamp: new Date()
    });
  };

  const handleLoadAllComplete = async () => {
    await loadDocuments();
    appendMessage({
      id: Date.now(),
      role: 'system',
      content: '📂 Documentos cargados y embeddings listos',
      timestamp: new Date()
    });
  };

  const handleDeleteDocument = async (docId) => {
    if (!window.confirm('¿Eliminar este documento?')) return;
    try {
      await api.deleteDocument(docId);
      await loadDocuments();
      appendMessage({
        id: Date.now(),
        role: 'system',
        content: '✓ Documento eliminado',
        timestamp: new Date()
      });
    } catch (err) {
      setError(err.message);
    }
  };

  const handleDeleteSelectedDocuments = async () => {
    if (selectedDocumentIds.length === 0) {
      setError('Selecciona al menos un documento para eliminar');
      return;
    }

    if (!window.confirm(`¿Eliminar ${selectedDocumentIds.length} documento(s) de forma completa (backend + base de datos)?`)) {
      return;
    }

    try {
      const result = await api.deleteDocumentsBatch(selectedDocumentIds);
      await loadDocuments();
      appendMessage({
        id: Date.now(),
        role: 'system',
        content: `✓ Documentos eliminados completamente: ${result.deleted}/${result.requested}`,
        timestamp: new Date()
      });
      setSelectedDocumentIds([]);
    } catch (err) {
      setError(err.message || 'No se pudo eliminar documentos');
    }
  };

  const handleClearChat = () => {
    if (window.confirm('¿Limpiar todo el chat?')) {
      setMessages([]);
    }
  };

  const handleOpenSource = async (source) => {
    if (!source?.id) return;

    setSelectedSource(source);
    setSelectedDocument(null);
    setSelectedDocumentError(null);
    setSelectedDocumentLoading(true);

    try {
      const documentDetails = await api.getDocument(source.id);
      setSelectedDocument(documentDetails);
    } catch (err) {
      setSelectedDocumentError(err.message || 'No se pudo abrir la fuente');
    } finally {
      setSelectedDocumentLoading(false);
    }
  };

  const handleCloseSourceViewer = () => {
    setSelectedSource(null);
    setSelectedDocument(null);
    setSelectedDocumentError(null);
    setSelectedDocumentLoading(false);
  };

  const selectedChunkIndex = selectedSource ? Number(selectedSource.chunk_index) : null;
  const selectedPageNumber = selectedSource ? Number(selectedSource.page) : null;
  const highlightedChunks = selectedDocument?.chunks || [];

  return (
    <div className="app">
      <Header 
        documentsCount={documents.length}
        systemHealth={systemHealth}
        stats={stats}
      />

      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      <div className="app-container">
        <aside className="sidebar">
          <div className="sidebar-header">
            <h2>📁 Documentos</h2>
            <button 
              className="btn-primary"
              onClick={() => setShowUpload(!showUpload)}
            >
              {showUpload ? '✕ Cerrar' : '+ Cargar'}
            </button>
          </div>

          {showUpload && (
            <DocumentUpload 
              onSuccess={handleUploadSuccess}
              onLoadComplete={handleLoadAllComplete}
            />
          )}

          <div className="documents-section">
            <div className="docs-count">
              {documents.length} documento{documents.length !== 1 ? 's' : ''}
            </div>
            <div className="docs-actions">
              <button
                className="btn-secondary"
                onClick={handleDeleteSelectedDocuments}
                disabled={selectedDocumentIds.length === 0}
                title={selectedDocumentIds.length === 0
                  ? 'Selecciona documentos para eliminar'
                  : `Eliminar ${selectedDocumentIds.length} documento(s) seleccionados`}
              >
                Eliminar seleccionados
              </button>
            </div>
            <DocumentList 
              documents={documents}
              selectedDocumentIds={selectedDocumentIds}
              onToggleSelection={handleToggleDocumentFilter}
              onDelete={handleDeleteDocument}
            />
          </div>
        </aside>

        <main className="chat-area">
          <ChatBox
            messages={messages}
            loading={loading}
            onSendMessage={handleSendMessage}
            onCancelStreaming={handleCancelStreaming}
            isStreaming={Boolean(streamAbortController)}
            streamFirstTokenMs={streamFirstTokenMs}
            streamingMessageId={streamingMessageId}
            onClearChat={handleClearChat}
            onOpenSource={handleOpenSource}
            selectedDocumentsCount={selectedDocumentIds.length}
          />
        </main>

        <ChatSessionsPanel
          sessions={chatSessions}
          activeSessionId={sessionId}
          onCreateSession={handleCreateSession}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
          loading={sessionsLoading || loading}
        />
      </div>

      {selectedSource && (
        <div className="source-viewer-overlay" onClick={handleCloseSourceViewer} role="presentation">
          <div className="source-viewer-modal" onClick={(event) => event.stopPropagation()}>
            <div className="source-viewer-header">
              <div>
                <div className="source-viewer-title">Fuente abierta</div>
                <div className="source-viewer-subtitle">
                  {selectedSource.filename}
                </div>
              </div>
              <button type="button" className="source-viewer-close" onClick={handleCloseSourceViewer}>
                ✕
              </button>
            </div>

            <div className="source-viewer-summary">
              <span>{Number.isInteger(selectedPageNumber) ? `p. ${selectedPageNumber}` : 'Sin página'}</span>
              <span>•</span>
              <span>{Number.isInteger(selectedChunkIndex) ? `chunk_index ${selectedChunkIndex}` : 'Sin chunk_index'}</span>
              {selectedSource.relevance ? (
                <>
                  <span>•</span>
                  <span>similitud {Math.round(selectedSource.relevance * 100)}%</span>
                </>
              ) : null}
            </div>

            {selectedDocumentLoading && (
              <div className="source-viewer-loading">Cargando documento...</div>
            )}

            {selectedDocumentError && (
              <div className="source-viewer-error">{selectedDocumentError}</div>
            )}

            {selectedDocument && (
              <div className="source-viewer-content">
                <div className="source-viewer-chunk">
                  <div className="source-viewer-chunk-label">Fragmento exacto</div>
                  <div className="source-viewer-chunk-text">
                    {selectedSource.snippet || 'No hay extracto disponible para esta fuente.'}
                  </div>
                </div>

                <div className="source-viewer-context">
                  <div className="source-viewer-chunk-label">Documento completo</div>
                  <div className="source-viewer-chunks-list">
                    {highlightedChunks.map((chunk) => {
                      const isSelected = Number(chunk.chunk_index) === selectedChunkIndex && Number(chunk.page_number) === selectedPageNumber;
                      return (
                        <div key={chunk.id} className={`source-viewer-chunk-item ${isSelected ? 'is-selected' : ''}`}>
                          <div className="source-viewer-chunk-item-meta">
                            <span>p. {chunk.page_number || '—'}</span>
                            <span>•</span>
                            <span>chunk_index {chunk.chunk_index}</span>
                          </div>
                          <div className="source-viewer-chunk-item-text">{chunk.content}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}