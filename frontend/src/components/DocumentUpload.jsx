import { useEffect, useState, useRef } from 'react';
import { api } from '../services/api';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const UPLOAD_CONCURRENCY = 3;
const POLL_INTERVAL_MS = 1200;

const bytesToMb = (bytes = 0) => bytes / (1024 * 1024);

const getDynamicTimeoutMs = ({ sizeBytes = 0, pages = 0 }) => {
  const baseMs = 60000;
  const sizeMs = Math.round(bytesToMb(sizeBytes) * 18000);
  const pagesMs = Math.max(0, pages) * 3000;
  const timeout = baseMs + sizeMs + pagesMs;
  return Math.max(90000, Math.min(timeout, 900000));
};

const getStatusLabel = (status) => {
  if (status === 'uploading') return 'uploading';
  if (status === 'processing') return 'processing';
  if (status === 'ready') return 'ready';
  if (status === 'error') return 'error';
  return 'pending';
};

const runWithConcurrency = async (items, limit, worker) => {
  if (!items.length) return;

  let cursor = 0;
  const runWorker = async () => {
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) {
        return;
      }
      await worker(items[index], index);
    }
  };

  const workers = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, () => runWorker());
  await Promise.all(workers);
};

export default function DocumentUpload({ onSuccess, onLoadComplete }) {
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0 });
  const [fileStatuses, setFileStatuses] = useState([]);
  const fileStatusesRef = useRef([]);
  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);

  useEffect(() => {
    if (!folderInputRef.current) return;
    folderInputRef.current.setAttribute('webkitdirectory', '');
    folderInputRef.current.setAttribute('directory', '');
    folderInputRef.current.setAttribute('mozdirectory', '');
  }, []);

  useEffect(() => {
    fileStatusesRef.current = fileStatuses;
  }, [fileStatuses]);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const files = Array.from(e.dataTransfer.files);
      uploadMultipleFiles(files);
    }
  };

  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      uploadMultipleFiles(Array.from(e.target.files));
    }
  };

  const handleFolderSelect = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      const files = Array.from(e.target.files);
      uploadMultipleFiles(files);
    }
    if (folderInputRef.current) {
      folderInputRef.current.value = '';
    }
  };

  const updateFileStatus = (localId, patch) => {
    setFileStatuses((current) =>
      current.map((item) => (item.localId === localId ? { ...item, ...patch } : item))
    );
  };

  const initializeStatuses = (validFiles) => {
    const items = validFiles.map((file, index) => ({
      localId: index + 1,
      filename: file.name,
      sizeBytes: file.size,
      status: 'pending',
      statusLabel: getStatusLabel('pending'),
      documentId: null,
      pages: 0,
      uploadedAtMs: null,
      processingStartedAtMs: null,
      timeoutMs: getDynamicTimeoutMs({ sizeBytes: file.size, pages: 0 }),
      error: null,
    }));
    setFileStatuses(items);
    return items;
  };

  const processBatchPolling = async (itemsById) => {
    const pollingIds = Array.from(itemsById.values())
      .filter((item) => item.documentId)
      .map((item) => item.documentId);

    if (!pollingIds.length) {
      return;
    }

    let pendingIds = new Set(pollingIds);
    while (pendingIds.size > 0) {
      const now = Date.now();

      for (const documentId of pendingIds) {
        const tracked = itemsById.get(documentId);
        if (!tracked) continue;

        const startedAt = tracked.processingStartedAtMs || tracked.uploadedAtMs || now;
        if (now - startedAt > tracked.timeoutMs) {
          updateFileStatus(tracked.localId, {
            status: 'error',
            statusLabel: getStatusLabel('error'),
            error: 'Timeout dinámico excedido durante procesamiento',
          });
          pendingIds.delete(documentId);
        }
      }

      if (pendingIds.size === 0) {
        break;
      }

      const pendingList = Array.from(pendingIds);
      const batch = await api.getDocumentsStatusBatch(pendingList);

      (batch.documents || []).forEach((doc) => {
        const tracked = itemsById.get(doc.id);
        if (!tracked) {
          return;
        }

        const serverStatus = doc.processing_status || 'ready';
        const dynamicTimeoutMs = getDynamicTimeoutMs({
          sizeBytes: tracked.sizeBytes,
          pages: doc.pages || tracked.pages || 0,
        });

        updateFileStatus(tracked.localId, {
          pages: doc.pages || tracked.pages || 0,
          timeoutMs: dynamicTimeoutMs,
          status: serverStatus,
          statusLabel: getStatusLabel(serverStatus),
        });

        tracked.pages = doc.pages || tracked.pages || 0;
        tracked.timeoutMs = dynamicTimeoutMs;
        tracked.status = serverStatus;

        if (serverStatus === 'ready' || serverStatus === 'error') {
          pendingIds.delete(doc.id);
        }
      });

      (batch.missing_ids || []).forEach((missingId) => {
        const tracked = itemsById.get(missingId);
        if (!tracked) {
          return;
        }
        updateFileStatus(tracked.localId, {
          status: 'error',
          statusLabel: getStatusLabel('error'),
          error: 'Documento no encontrado durante polling',
        });
        pendingIds.delete(missingId);
      });

      if (pendingIds.size > 0) {
        await sleep(POLL_INTERVAL_MS);
      }
    }
  };

  const uploadMultipleFiles = async (files) => {
    const validFiles = files.filter(file => {
      const ext = '.' + file.name.split('.').pop().toLowerCase();
      return ['.pdf', '.docx', '.doc'].includes(ext);
    });

    if (validFiles.length === 0) {
      setMessage({
        type: 'error',
        text: '❌ No se encontraron archivos PDF o DOCX en la selección',
      });
      setTimeout(() => setMessage(null), 4000);
      return;
    }

    setLoading(true);
    setMessage(null);
    setUploadProgress({ current: 0, total: validFiles.length });
    const statusItems = initializeStatuses(validFiles);
    let completedUploads = 0;

    const byDocumentId = new Map();

    await runWithConcurrency(validFiles, UPLOAD_CONCURRENCY, async (file, index) => {
      const tracked = statusItems[index];
      updateFileStatus(tracked.localId, {
        status: 'uploading',
        statusLabel: getStatusLabel('uploading'),
      });
      setMessage({
        type: 'loading',
        text: `📤 Subiendo ${file.name}...`,
      });

      try {
        const uploadResult = await api.uploadDocument(file);
        const now = Date.now();
        const docId = uploadResult.document_id;

        updateFileStatus(tracked.localId, {
          documentId: docId,
          uploadedAtMs: now,
          processingStartedAtMs: now,
          status: 'processing',
          statusLabel: getStatusLabel('processing'),
          timeoutMs: getDynamicTimeoutMs({ sizeBytes: file.size, pages: 0 }),
        });

        if (docId) {
          byDocumentId.set(docId, {
            ...tracked,
            documentId: docId,
            uploadedAtMs: now,
            processingStartedAtMs: now,
            sizeBytes: file.size,
            timeoutMs: getDynamicTimeoutMs({ sizeBytes: file.size, pages: 0 }),
          });
        }
      } catch (error) {
        updateFileStatus(tracked.localId, {
          status: 'error',
          statusLabel: getStatusLabel('error'),
          error: error.message || 'Error desconocido en upload',
        });
      } finally {
        completedUploads += 1;
        setUploadProgress({ current: completedUploads, total: validFiles.length });
      }
    });

    setMessage({
      type: 'loading',
      text: '🧠 Procesando embeddings por lote...',
    });

    await processBatchPolling(byDocumentId);

    const snapshot = fileStatusesRef.current;
    const successCount = snapshot.filter((item) => item.status === 'ready').length;
    const errorCount = snapshot.filter((item) => item.status === 'error').length;

    if (errorCount === 0) {
      setMessage({
        type: 'success',
        text: `✅ ${successCount} archivos listos`,
      });
    } else {
      setMessage({
        type: 'error',
        text: `⚠️ ${successCount} listos, ${errorCount} con error`,
      });
    }

    setLoading(false);
    setUploadProgress({ current: 0, total: 0 });

    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }

    setTimeout(() => {
      if (onSuccess) onSuccess();
      setMessage(null);
    }, 3000);
  };

  const handleLoadAll = async () => {
    try {
      setLoading(true);
      setMessage({
        type: 'loading',
        text: '📂 Cargando todos los documentos de la carpeta del servidor...',
      });

      const data = await api.loadAllDocuments();

      const documentsToWait = (data.documents || []).filter(
        (document) => document.status === 'processing' && document.doc_id
      );

      const statusSeed = documentsToWait.map((document, index) => ({
        localId: index + 1,
        filename: document.filename,
        sizeBytes: Math.round((document.filesize_mb || 0) * 1024 * 1024),
        status: 'processing',
        statusLabel: getStatusLabel('processing'),
        documentId: document.doc_id,
        pages: 0,
        uploadedAtMs: Date.now(),
        processingStartedAtMs: Date.now(),
        timeoutMs: getDynamicTimeoutMs({
          sizeBytes: Math.round((document.filesize_mb || 0) * 1024 * 1024),
          pages: 0,
        }),
        error: null,
      }));

      setFileStatuses(statusSeed);

      const statusMap = new Map();
      statusSeed.forEach((item) => {
        statusMap.set(item.documentId, item);
      });

      await processBatchPolling(statusMap);

      if (data.success) {
        setMessage({
          type: 'success',
          text: `✅ Se cargaron ${data.loaded} documentos y embeddings listos`,
        });
        if (onLoadComplete) {
          onLoadComplete();
        }
      }

      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({
        type: 'error',
        text: `❌ Error: ${error.message}`,
      });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.container}>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <button
          onClick={() => fileInputRef.current?.click()}
          style={{
            ...styles.uploadButton,
            flex: 1,
            minWidth: '120px',
          }}
          disabled={loading}
        >
          📁 Seleccionar Archivo
        </button>

        <button
          onClick={() => folderInputRef.current?.click()}
          style={{
            ...styles.uploadButton,
            flex: 1,
            minWidth: '120px',
            backgroundColor: '#10b981',
          }}
          disabled={loading}
        >
          📂 Seleccionar Carpeta
        </button>

        <button
          onClick={handleLoadAll}
          style={{
            ...styles.uploadButton,
            flex: 1,
            minWidth: '120px',
            backgroundColor: '#764ba2',
          }}
          disabled={loading}
        >
          📦 Cargar Todos (Backend)
        </button>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        onChange={handleFileSelect}
        accept=".pdf,.docx,.doc"
        style={{ display: 'none' }}
        disabled={loading}
        multiple
      />

      <input
        ref={folderInputRef}
        type="file"
        onChange={handleFolderSelect}
        accept=".pdf,.docx,.doc"
        style={{ display: 'none' }}
        disabled={loading}
        multiple
      />

      {uploadProgress.total > 0 && (
        <div style={styles.progressContainer}>
          <div 
            style={{
              ...styles.progressBar,
              width: `${(uploadProgress.current / uploadProgress.total) * 100}%`
            }}
          />
          <span style={styles.progressText}>
            {uploadProgress.current} / {uploadProgress.total}
          </span>
        </div>
      )}

      {fileStatuses.length > 0 && (
        <div style={styles.statusList}>
          {fileStatuses.map((item) => (
            <div key={item.localId} style={styles.statusRow}>
              <div style={styles.statusFilename} title={item.filename}>{item.filename}</div>
              <div
                style={{
                  ...styles.statusBadge,
                  ...(item.status === 'uploading' && styles.statusUploading),
                  ...(item.status === 'processing' && styles.statusProcessing),
                  ...(item.status === 'ready' && styles.statusReady),
                  ...(item.status === 'error' && styles.statusError),
                }}
              >
                {item.statusLabel}
              </div>
            </div>
          ))}
        </div>
      )}

      <div
        style={{
          ...styles.dropzone,
          ...(dragActive && styles.dropzoneActive),
        }}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <div style={styles.dropzoneContent}>
          {loading ? (
            <>
              <span style={styles.icon}>⏳</span>
              <p style={styles.text}>Procesando...</p>
            </>
          ) : (
            <>
              <span style={styles.icon}>📁</span>
              <p style={styles.text}>
                Arrastra archivos o carpeta aquí
              </p>
              <p style={styles.subtext}>PDF o DOCX (máx 50MB cada uno)</p>
            </>
          )}
        </div>
      </div>

      {message && (
        <div
          style={{
            ...styles.message,
            ...(message.type === 'error' && styles.messageError),
            ...(message.type === 'success' && styles.messageSuccess),
            ...(message.type === 'loading' && styles.messageLoading),
          }}
        >
          {message.text}
        </div>
      )}
    </div>
  );
}

const styles = {
  container: {
    padding: '1rem',
  },
  dropzone: {
    border: '2px dashed #667eea',
    borderRadius: '8px',
    padding: '2rem',
    textAlign: 'center',
    cursor: 'pointer',
    transition: 'all 0.3s ease',
    backgroundColor: '#f9f9f9',
    marginBottom: '1rem',
  },
  dropzoneActive: {
    borderColor: '#764ba2',
    backgroundColor: '#f0f0ff',
    boxShadow: '0 0 0 3px rgba(102, 126, 234, 0.1)',
  },
  dropzoneContent: {
    pointerEvents: 'none',
  },
  icon: {
    fontSize: '2.5rem',
    display: 'block',
    marginBottom: '0.75rem',
  },
  text: {
    margin: '0.5rem 0 0.25rem',
    fontSize: '1rem',
    fontWeight: '500',
    color: '#333',
  },
  subtext: {
    margin: '0.5rem 0 0',
    fontSize: '0.875rem',
    color: '#666',
  },
  message: {
    padding: '1rem',
    borderRadius: '6px',
    fontSize: '0.875rem',
    textAlign: 'center',
    animation: 'slideIn 0.3s ease',
    marginTop: '0.5rem',
  },
  messageError: {
    backgroundColor: '#FEE2E2',
    color: '#991B1B',
    border: '1px solid #FECACA',
  },
  messageSuccess: {
    backgroundColor: '#DCFCE7',
    color: '#166534',
    border: '1px solid #BBF7D0',
  },
  messageLoading: {
    backgroundColor: '#FEF3C7',
    color: '#92400E',
    border: '1px solid #FCD34D',
  },
  uploadButton: {
    padding: '0.75rem 1rem',
    backgroundColor: '#667eea',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    fontWeight: '600',
    fontSize: '0.9rem',
    transition: 'background-color 0.2s',
  },
  progressContainer: {
    width: '100%',
    height: '20px',
    backgroundColor: '#e5e7eb',
    borderRadius: '10px',
    overflow: 'hidden',
    marginBottom: '0.5rem',
    position: 'relative',
  },
  progressBar: {
    height: '100%',
    backgroundColor: '#667eea',
    transition: 'width 0.3s ease',
    borderRadius: '10px',
  },
  progressText: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    fontSize: '11px',
    fontWeight: '600',
    color: '#1f2937',
  },
  statusList: {
    border: '1px solid #e5e7eb',
    borderRadius: '8px',
    marginBottom: '0.75rem',
    maxHeight: '180px',
    overflowY: 'auto',
    backgroundColor: '#ffffff',
  },
  statusRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: '0.75rem',
    padding: '0.5rem 0.75rem',
    borderBottom: '1px solid #f3f4f6',
  },
  statusFilename: {
    fontSize: '0.82rem',
    color: '#111827',
    overflow: 'hidden',
    whiteSpace: 'nowrap',
    textOverflow: 'ellipsis',
    maxWidth: '75%',
  },
  statusBadge: {
    fontSize: '0.72rem',
    fontWeight: '700',
    textTransform: 'uppercase',
    borderRadius: '9999px',
    padding: '0.2rem 0.6rem',
    letterSpacing: '0.02em',
  },
  statusUploading: {
    backgroundColor: '#dbeafe',
    color: '#1d4ed8',
  },
  statusProcessing: {
    backgroundColor: '#fef3c7',
    color: '#92400e',
  },
  statusReady: {
    backgroundColor: '#dcfce7',
    color: '#166534',
  },
  statusError: {
    backgroundColor: '#fee2e2',
    color: '#991b1b',
  },
};