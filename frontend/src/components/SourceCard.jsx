export default function SourceCard({ source, onOpen }) {
  const relevance = typeof source.relevance === 'number'
    ? `${Math.round(source.relevance * 100)}%`
    : null;

  const pageLabel = Number.isInteger(source.page) ? `p. ${source.page}` : null;
  const chunkIndexLabel = Number.isInteger(source.chunk_index)
    ? `chunk_index ${source.chunk_index}`
    : null;
  const citationParts = [source.filename, pageLabel, chunkIndexLabel].filter(Boolean);
  const citation = citationParts.length > 0 ? `[${citationParts.join(' · ')}]` : null;
  const snippet = (source.snippet || '')
    .replace(/\s+/g, ' ')
    .trim();
  const cleanSnippet = snippet.length > 220 ? `${snippet.slice(0, 220).trimEnd()}…` : snippet;

  return (
    <button
      type="button"
      className="source-card"
      onClick={() => onOpen?.(source)}
      title={onOpen ? 'Abrir fuente' : undefined}
    >
      <div className="source-icon">
        {source.filetype === '.pdf' ? '📕' : '📘'}
      </div>
      <div className="source-info">
        <div className="source-filename">
          {source.filename}
        </div>
        <div className="source-meta">
          {citation && <span className="source-citation">{citation}</span>}
          {citation && (pageLabel || relevance) && <span>•</span>}
          {pageLabel && <span>{pageLabel}</span>}
          {pageLabel && relevance && <span>•</span>}
          {relevance && <span>similitud {relevance}</span>}
        </div>
        {cleanSnippet && (
          <blockquote className="source-snippet">
            {cleanSnippet}
          </blockquote>
        )}
      </div>
    </button>
  );
}
