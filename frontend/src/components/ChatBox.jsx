import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import SourceCard from './SourceCard';
import './ChatBox.css';

export default function ChatBox({
  messages,
  loading,
  onSendMessage,
  onCancelStreaming,
  isStreaming,
  streamFirstTokenMs,
  streamingMessageId,
  onClearChat,
  onOpenSource,
  selectedDocumentsCount,
}) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);

  // Auto-scroll al final
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    onSendMessage(input);
    setInput('');
  };

  const isEmpty = messages.length === 0;

  return (
    <div className="chat-box">
      <div className="chat-header">
        <div className="chat-header-main">
          <h1>💬 Asistente Documental</h1>
          {selectedDocumentsCount > 0 && (
            <span className="filter-badge">
              Filtro activo: {selectedDocumentsCount} documento{selectedDocumentsCount !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        {messages.length > 0 && (
          <div className="chat-header-actions">
            {isStreaming && (
              <button className="btn-cancel-stream" onClick={onCancelStreaming} title="Cancelar respuesta">
                ⏹️ Cancelar
              </button>
            )}
            <button className="btn-clear" onClick={onClearChat} title="Limpiar chat" disabled={isStreaming}>
              🗑️ Limpiar
            </button>
          </div>
        )}
      </div>

      <div className="chat-messages">
        {isEmpty && (
          <div className="empty-state">
            <div className="empty-icon">💭</div>
            <h2>¡Hola! Soy tu asistente de documentos</h2>
            <p>Carga documentos PDF o DOCX en el panel izquierdo y luego hazme preguntas sobre su contenido.</p>
            <div className="example-questions">
              <p style={{ fontSize: '12px', color: '#999', marginTop: '20px' }}>Ejemplos de preguntas:</p>
              <div style={{ fontSize: '13px', color: '#666', marginTop: '10px', textAlign: 'center' }}>
                <div>• ¿Cuál es el tema principal?</div>
                <div>• ¿Qué se menciona sobre...?</div>
                <div>• Resumir el documento</div>
              </div>
            </div>
          </div>
        )}

        {messages.map((message) => (
          <div key={message.id} className={`message message-${message.role}`}>
            {message.role === 'user' && (
              <div className="user-message">
                <div className="message-avatar">👤</div>
                <div className="message-content">
                  {message.content}
                </div>
              </div>
            )}

            {message.role === 'assistant' && (
              <div className="assistant-message">
                <div className="message-avatar">🤖</div>
                <div className="message-content">
                  {message.sources && message.sources.length > 0 && (
                    <div className="sources-section">
                      <div className="sources-header">
                        📄 Fuentes encontradas ({message.sources.length}):
                      </div>
                      <div className="sources-list">
                        {message.sources.map((source, idx) => (
                          <SourceCard key={idx} source={source} onOpen={onOpenSource} />
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="generated-answer-section">
                    <div className="generated-answer-header">
                      ✨ Respuesta Generada
                    </div>
                    <div className="generated-answer-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {message.content}
                      </ReactMarkdown>
                      {message.isStreaming && message.id === streamingMessageId && (
                        <span className="stream-cursor" aria-label="streaming">▍</span>
                      )}
                    </div>
                  </div>

                  <div className="message-meta">
                    <span className="confidence">
                      Similitud: {(message.confidence * 100).toFixed(0)}%
                    </span>
                    {message.processing_time && (
                      <span className="time">
                        Tiempo: {message.processing_time}s
                      </span>
                    )}
                    {typeof message.first_token_ms === 'number' && (
                      <span className="time">
                        Primer token: {Math.round(message.first_token_ms)}ms
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {message.role === 'system' && (
              <div className="system-message">
                {message.content}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="message message-assistant">
            <div className="message-avatar">🤖</div>
            <div className="message-content">
              <div className="loading-indicator">
                <div className="spinner"></div>
                <span>{isStreaming ? 'Recibiendo respuesta en streaming...' : 'Procesando tu pregunta...'}</span>
              </div>
            </div>
          </div>
        )}

        {isStreaming && streamFirstTokenMs !== null && (
          <div className="stream-ttft-banner">
            ⚡ Primer token en {streamFirstTokenMs}ms
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form className="chat-input-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Escribe tu pregunta aquí..."
          disabled={loading || isStreaming}
          className="chat-input"
          autoFocus
        />
        <button 
          type="submit" 
          disabled={loading || isStreaming || !input.trim()}
          className="btn-send"
        >
          {loading || isStreaming ? '⏳' : '➤'} {isStreaming ? 'Streaming...' : loading ? 'Procesando...' : 'Enviar'}
        </button>
      </form>
    </div>
  );
}
