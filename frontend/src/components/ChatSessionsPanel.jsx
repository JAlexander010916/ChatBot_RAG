import './ChatSessionsPanel.css';

const formatDate = (value) => {
  if (!value) return 'Sin actividad';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Sin actividad';
  return date.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export default function ChatSessionsPanel({
  sessions,
  activeSessionId,
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  loading,
}) {
  return (
    <aside className="chat-sessions-sidebar">
      <div className="chat-sessions-header">
        <h2>💬 Chats</h2>
        <button
          className="btn-primary"
          type="button"
          onClick={onCreateSession}
          disabled={loading}
        >
          + Nuevo
        </button>
      </div>

      <div className="chat-sessions-list">
        {sessions.length === 0 && (
          <div className="chat-sessions-empty">
            <p>No hay chats guardados</p>
            <small>Crea uno para empezar</small>
          </div>
        )}

        {sessions.map((session) => {
          const isActive = session.session_id === activeSessionId;
          return (
            <div
              key={session.session_id}
              className={`chat-session-item ${isActive ? 'is-active' : ''}`}
            >
              <button
                type="button"
                className="chat-session-select"
                onClick={() => onSelectSession(session.session_id)}
                title={session.title}
              >
                <div className="chat-session-title">{session.title}</div>
                <div className="chat-session-meta">
                  <span>{session.messages_count || 0} msg</span>
                  <span>•</span>
                  <span>{formatDate(session.last_message_at)}</span>
                </div>
              </button>

              <button
                type="button"
                className="chat-session-delete"
                title="Eliminar chat"
                onClick={() => onDeleteSession(session.session_id)}
              >
                🗑️
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
