import './DocumentList.css';

export default function DocumentList({ documents, selectedDocumentIds, onToggleSelection, onDelete }) {
  const visibleDocuments = documents.filter((doc) => doc.processing_status !== 'error' && doc.backend_file_exists !== false);

  if (visibleDocuments.length === 0) {
    return (
      <div className="document-list">
        <div className="empty-docs">
          <p>No hay documentos cargados</p>
          <small>Carga uno para comenzar</small>
        </div>
      </div>
    );
  }

  return (
    <div className="document-list">
      {visibleDocuments.map((doc) => {
        const isSelectable = doc.processing_status === 'ready' || !doc.processing_status;
        const isSelected = selectedDocumentIds.includes(doc.id);

        return (
          <div
            key={doc.id}
            className={`document-item ${isSelected ? 'document-item-selected' : ''} ${!isSelectable ? 'document-item-disabled' : ''}`}
          >
            <label
              className="doc-selection"
              title={isSelectable ? 'Usar este documento en el filtro' : 'Disponible cuando termine de procesarse'}
            >
              <input
                type="checkbox"
                checked={isSelected}
                disabled={!isSelectable}
                onChange={() => onToggleSelection(doc.id)}
              />
            </label>

            <div className="doc-info">
              <div className="doc-icon">
                {doc.filetype === '.pdf' ? '📕' : '📘'}
              </div>
              <div className="doc-details">
                <div className="doc-name" title={doc.filename}>
                  {doc.filename}
                </div>
                <div className="doc-meta">
                  <span className={`doc-status doc-status-${doc.processing_status || 'ready'}`}>
                    {doc.processing_status === 'processing' && 'Procesando'}
                    {doc.processing_status === 'error' && 'Error'}
                    {(!doc.processing_status || doc.processing_status === 'ready') && 'Listo'}
                  </span>
                  <span>•</span>
                  <span>{doc.chunks_count || 0} chunks</span>
                  <span>•</span>
                  <span>{doc.filesize_mb}MB</span>
                </div>
              </div>
            </div>
            <button
              className="btn-delete"
              onClick={() => onDelete(doc.id)}
              title="Eliminar documento completo"
            >
              🗑️
            </button>
          </div>
        );
      })}
    </div>
  );
}
