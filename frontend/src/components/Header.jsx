import './Header.css';

export default function Header({ documentsCount, systemHealth, stats }) {
  return (
    <header className="app-header">
      <div className="header-left">
        <h1 className="app-title">📄 Asistente Documental</h1>
        <p className="app-subtitle">Busca información en tus documentos usando IA</p>
      </div>

      <div className="header-right">
        <div className="stat-badge">
          <span className="stat-label">Documentos:</span>
          <span className="stat-value">{documentsCount}</span>
        </div>

        {systemHealth && (
          <>
            <div className={`status-badge ${systemHealth.ready_for_chat ? 'ok' : 'error'}`}>
              <span className="status-dot"></span>
              <span>{systemHealth.ready_for_chat ? 'Chat model OK' : 'Chat model faltante'}</span>
            </div>

            <div className={`status-badge ${systemHealth.ready_for_embeddings ? 'ok' : 'error'}`}>
              <span className="status-dot"></span>
              <span>{systemHealth.ready_for_embeddings ? 'Embeddings OK' : 'Embeddings faltante'}</span>
            </div>

            <div className={`status-badge ${systemHealth.database ? 'ok' : 'error'}`}>
              <span className="status-dot"></span>
              <span>{systemHealth.database ? 'BD OK' : 'BD Error'}</span>
            </div>
          </>
        )}

        {stats && stats.total_chunks > 0 && (
          <div className="stat-badge">
            <span className="stat-label">Chunks:</span>
            <span className="stat-value">{stats.total_chunks}</span>
          </div>
        )}
      </div>
    </header>
  );
}
