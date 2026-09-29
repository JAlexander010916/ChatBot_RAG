/**
 * Cliente HTTP para consumir la API del backend
 */

const API_BASE = import.meta.env.VITE_API_URL || '/api';

const requestJson = async (url, options = {}, defaultErrorMessage) => {
  const response = await fetch(url, options);

  if (!response.ok) {
    let errorMessage = defaultErrorMessage;

    try {
      const error = await response.json();
      errorMessage = error.detail || defaultErrorMessage;
    } catch {
      // Si la respuesta no es JSON, se conserva el mensaje por defecto.
    }

    throw new Error(errorMessage);
  }

  return response.json();
};

const parseSseBuffer = (buffer) => {
  const events = [];
  let working = buffer;

  while (true) {
    const separatorIndex = working.indexOf('\n\n');
    if (separatorIndex === -1) {
      break;
    }

    const rawEvent = working.slice(0, separatorIndex).trim();
    working = working.slice(separatorIndex + 2);

    if (!rawEvent) {
      continue;
    }

    const lines = rawEvent.split('\n');
    let eventName = 'message';
    const dataLines = [];

    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      }
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim());
      }
    }

    let payload = {};
    const dataText = dataLines.join('\n');
    if (dataText) {
      try {
        payload = JSON.parse(dataText);
      } catch {
        payload = { raw: dataText };
      }
    }

    events.push({ event: eventName, data: payload });
  }

  return { events, rest: working };
};

export const api = {
  uploadDocument: async (file) => {
    const formData = new FormData();
    const baseName = file.name.split(/[/\\]/).pop();
    formData.append('file', file, baseName);

    return requestJson(
      `${API_BASE}/documents/upload`,
      {
        method: 'POST',
        body: formData,
      },
      'Error al subir documento'
    );
  },

  getDocuments: async () => {
    return requestJson(`${API_BASE}/documents/`, {}, 'Error al obtener documentos');
  },

  getDocument: async (docId) => {
    return requestJson(`${API_BASE}/documents/${docId}`, {}, 'Error al obtener documento');
  },

  getDocumentsStatusBatch: async (documentIds) => {
    return requestJson(
      `${API_BASE}/documents/status-batch`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_ids: documentIds }),
      },
      'Error al obtener estado de documentos'
    );
  },

  deleteDocument: async (docId) => {
    return requestJson(
      `${API_BASE}/documents/${docId}`,
      { method: 'DELETE' },
      'Error al eliminar documento'
    );
  },

  deleteDocumentsBatch: async (documentIds) => {
    return requestJson(
      `${API_BASE}/documents/delete-batch`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_ids: documentIds }),
      },
      'Error al eliminar documentos'
    );
  },

  removeDocumentBackendFile: async (docId) => {
    return requestJson(
      `${API_BASE}/documents/${docId}/file-only`,
      { method: 'DELETE' },
      'Error al eliminar archivo del backend'
    );
  },

  removeDocumentsBackendFilesBatch: async (documentIds) => {
    return requestJson(
      `${API_BASE}/documents/file-only/batch-remove`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_ids: documentIds }),
      },
      'Error al eliminar archivos del backend'
    );
  },

  getDocStats: async () => {
    return requestJson(`${API_BASE}/documents/stats`, {}, 'Error al obtener estadísticas');
  },

  loadAllDocuments: async () => {
    return requestJson(
      `${API_BASE}/documents/load-all`,
      { method: 'POST' },
      'Error al cargar documentos'
    );
  },

  askQuestion: async (question, topK = 5, documentIds = undefined, sessionId = undefined) => {
    const payload = { question, top_k: topK };

    if (Array.isArray(documentIds) && documentIds.length > 0) {
      payload.document_ids = documentIds;
    }

    if (sessionId) {
      payload.session_id = sessionId;
    }

    return requestJson(
      `${API_BASE}/chat/ask`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      'Error en la pregunta'
    );
  },

  askQuestionStream: async (
    question,
    {
      topK = 5,
      documentIds = undefined,
      sessionId = undefined,
      signal,
      onStart,
      onToken,
      onDone,
      onError,
    } = {}
  ) => {
    const payload = { question, top_k: topK };

    if (Array.isArray(documentIds) && documentIds.length > 0) {
      payload.document_ids = documentIds;
    }

    if (sessionId) {
      payload.session_id = sessionId;
    }

    let response;
    try {
      response = await fetch(`${API_BASE}/chat/ask/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(payload),
        signal,
      });
    } catch (networkError) {
      if (networkError?.name === 'AbortError') {
        throw networkError;
      }
      throw new Error('No se pudo iniciar el stream de respuesta');
    }

    if (!response.ok) {
      let errorMessage = 'Error en el stream de la pregunta';
      try {
        const error = await response.json();
        errorMessage = error.detail || errorMessage;
      } catch {
        // Sin JSON en error
      }
      throw new Error(errorMessage);
    }

    if (!response.body) {
      throw new Error('El navegador no soporta streaming en esta respuesta');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    const dispatchEvent = ({ event, data }) => {
      if (event === 'start' && typeof onStart === 'function') {
        onStart(data);
      }
      if (event === 'token' && typeof onToken === 'function') {
        onToken(data?.token || '');
      }
      if (event === 'done' && typeof onDone === 'function') {
        onDone(data);
      }
      if (event === 'error' && typeof onError === 'function') {
        onError(data);
      }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSseBuffer(buffer);
        buffer = parsed.rest;

        for (const evt of parsed.events) {
          dispatchEvent(evt);
        }
      }
    } catch (streamError) {
      if (streamError?.name === 'AbortError') {
        throw streamError;
      }
      throw new Error(streamError?.message || 'Se interrumpió el stream de respuesta');
    } finally {
      reader.releaseLock();
    }
  },

  getChatHealth: async () => {
    return requestJson(`${API_BASE}/chat/health`, {}, 'Error verificando salud');
  },

  getChatStats: async () => {
    return requestJson(`${API_BASE}/chat/stats`, {}, 'Error al obtener estadísticas');
  },

  getChatSessions: async () => {
    return requestJson(`${API_BASE}/chat/sessions`, {}, 'Error al obtener chats');
  },

  createChatSession: async (title = 'Nuevo chat') => {
    return requestJson(
      `${API_BASE}/chat/sessions`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      },
      'Error al crear chat'
    );
  },

  getChatSession: async (sessionId) => {
    return requestJson(`${API_BASE}/chat/sessions/${sessionId}`, {}, 'Error al cargar chat');
  },

  deleteChatSession: async (sessionId) => {
    return requestJson(
      `${API_BASE}/chat/sessions/${sessionId}`,
      { method: 'DELETE' },
      'Error al eliminar chat'
    );
  },

  renameChatSession: async (sessionId, title) => {
    return requestJson(
      `${API_BASE}/chat/sessions/${sessionId}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      },
      'Error al renombrar chat'
    );
  }
};