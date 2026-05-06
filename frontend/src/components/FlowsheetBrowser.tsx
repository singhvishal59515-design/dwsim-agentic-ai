import React, { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../utils/api';

interface FlowsheetFile {
  path: string;
  name: string;
  size_display: string;
  modified: string;
  modified_ts: number;
  directory: string;
}

interface Props {
  dark?: boolean;
  onLoaded: () => void;           // callback after a flowsheet is loaded
  currentFlowsheet?: string;      // name of currently loaded flowsheet
}

const s: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: '#1e293b',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '10px 14px',
    borderBottom: '1px solid #334155',
  },
  title: {
    fontSize: 13,
    fontWeight: 700,
    color: '#38bdf8',
    letterSpacing: 0.5,
  },
  liveIndicator: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    fontSize: 10,
    color: '#64748b',
  },
  liveDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#22c55e',
    animation: 'pulse 2s infinite',
  },
  liveDotOff: {
    background: '#64748b',
  },
  toolbar: {
    display: 'flex',
    gap: 6,
    padding: '8px 14px',
    borderBottom: '1px solid #334155',
  },
  scanBtn: {
    flex: 1,
    background: '#334155',
    color: '#e2e8f0',
    border: 'none',
    borderRadius: 5,
    padding: '6px 10px',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
  },
  scanBtnActive: {
    background: '#0ea5e9',
    color: '#fff',
  },
  notification: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 14px',
    background: '#14532d',
    borderBottom: '1px solid #166534',
    fontSize: 12,
    color: '#a3e635',
    cursor: 'pointer',
  },
  notifBtn: {
    background: '#22c55e',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    padding: '3px 10px',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    marginLeft: 'auto',
  },
  fileList: {
    flex: 1,
    overflowY: 'auto' as const,
    padding: '4px 0',
  },
  fileItem: {
    display: 'flex',
    flexDirection: 'column' as const,
    padding: '8px 14px',
    cursor: 'pointer',
    borderBottom: '1px solid #1e293b',
    background: '#0f172a',
    transition: 'background 0.15s',
  },
  fileItemHover: {
    background: '#1e3a5f',
  },
  fileItemActive: {
    background: '#0c4a6e',
    borderLeft: '3px solid #38bdf8',
  },
  fileItemNew: {
    borderLeft: '3px solid #22c55e',
  },
  fileName: {
    fontSize: 12,
    fontWeight: 600,
    color: '#e2e8f0',
    marginBottom: 2,
  },
  fileMeta: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 10,
    color: '#64748b',
  },
  fileDir: {
    fontSize: 10,
    color: '#475569',
    marginTop: 2,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  loadBtn: {
    background: '#0ea5e9',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    padding: '4px 12px',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    marginTop: 4,
    alignSelf: 'flex-start',
  },
  statusBar: {
    padding: '6px 14px',
    borderTop: '1px solid #334155',
    fontSize: 10,
    color: '#64748b',
    display: 'flex',
    justifyContent: 'space-between',
  },
  empty: {
    padding: '30px 14px',
    textAlign: 'center' as const,
    color: '#475569',
    fontSize: 12,
  },
  manualInput: {
    display: 'flex',
    gap: 6,
    padding: '8px 14px',
    borderTop: '1px solid #334155',
  },
  input: {
    flex: 1,
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: 5,
    color: '#e2e8f0',
    padding: '6px 8px',
    fontSize: 11,
    outline: 'none',
  },
};

export default function FlowsheetBrowser({ onLoaded, currentFlowsheet }: Props) {
  const [files, setFiles]           = useState<FlowsheetFile[]>([]);
  const [loading, setLoading]       = useState(false);
  const [loadingFile, setLoadingFile] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [notification, setNotification] = useState<{ event: string; file: FlowsheetFile } | null>(null);
  const [hovered, setHovered]       = useState<string | null>(null);
  const [newFiles, setNewFiles]     = useState<Set<string>>(new Set());
  const [manualPath, setManualPath] = useState('');
  const [status, setStatus]         = useState('');
  const wsRef = useRef<{ close: () => void } | null>(null);

  // Initial scan on mount
  useEffect(() => {
    handleScan();
  }, []);

  // WebSocket connection for real-time events
  useEffect(() => {
    const ws = api.flowsheetWs((evt) => {
      if (evt.type === 'file_event') {
        if (evt.event === 'created' || evt.event === 'modified') {
          setNotification({ event: evt.event, file: evt.file });
          setNewFiles((prev) => new Set(prev).add(evt.file.path));
          // Auto-refresh file list
          handleScan();
        } else if (evt.event === 'deleted') {
          setFiles((prev) => prev.filter((f) => f.path !== evt.file.path));
        }
      } else if (evt.type === 'pong') {
        // keepalive ok
      } else if (evt.type === 'scan_result') {
        setFiles(evt.files || []);
      } else if (evt.type === 'ws_close') {
        setWsConnected(false);
      }
    });

    wsRef.current = ws;
    // Small delay to let connection establish
    const checkTimer = setTimeout(() => setWsConnected(true), 500);

    return () => {
      clearTimeout(checkTimer);
      ws.close();
      wsRef.current = null;
    };
  }, []);

  const handleScan = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.scanFlowsheets(50);
      setFiles(r.files || []);
      setStatus(`${r.count} file(s) found`);
    } catch (e: any) {
      setStatus(`Scan error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleLoad = async (path: string) => {
    setLoadingFile(path);
    setStatus('Loading...');
    try {
      const r = await api.loadByPath(path);
      setStatus(`Loaded: ${r.alias} (${r.object_count} objects)`);
      setNotification(null);
      setNewFiles((prev) => {
        const next = new Set(prev);
        next.delete(path);
        return next;
      });
      onLoaded();
    } catch (e: any) {
      setStatus(`Load error: ${e.message}`);
    } finally {
      setLoadingFile(null);
    }
  };

  const handleManualLoad = async () => {
    if (!manualPath.trim()) return;
    await handleLoad(manualPath.trim());
    setManualPath('');
  };

  const dismissNotification = () => setNotification(null);

  return (
    <div style={s.root}>
      {/* Header */}
      <div style={s.header}>
        <span style={s.title}>Flowsheet Files</span>
        <div style={s.liveIndicator}>
          <div style={{ ...s.liveDot, ...(wsConnected ? {} : s.liveDotOff) }} />
          {wsConnected ? 'Live' : 'Offline'}
        </div>
      </div>

      {/* Toolbar */}
      <div style={s.toolbar}>
        <button
          style={{ ...s.scanBtn, ...(loading ? s.scanBtnActive : {}) }}
          onClick={handleScan}
          disabled={loading}
        >
          {loading ? 'Scanning...' : 'Scan Disk'}
        </button>
      </div>

      {/* Real-time notification banner */}
      {notification && (
        <div style={s.notification} onClick={() => handleLoad(notification.file.path)}>
          <span>
            {notification.event === 'created' ? 'New' : 'Updated'}:{' '}
            <strong>{notification.file.name}</strong>
          </span>
          <button
            style={s.notifBtn}
            onClick={(e) => {
              e.stopPropagation();
              handleLoad(notification.file.path);
            }}
          >
            Load Now
          </button>
          <button
            style={{ ...s.notifBtn, background: '#475569' }}
            onClick={(e) => {
              e.stopPropagation();
              dismissNotification();
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* File list */}
      <div style={s.fileList}>
        {files.length === 0 && !loading && (
          <div style={s.empty}>
            No .dwxmz files found.<br />
            Create a flowsheet in DWSIM and save it to<br />
            Documents, Desktop, or Downloads.
          </div>
        )}
        {files.map((f) => {
          const isActive = currentFlowsheet && f.name.replace(/\.(dwxmz|dwxm)$/i, '') === currentFlowsheet;
          const isNew = newFiles.has(f.path);
          const isHovered = hovered === f.path;
          const isLoading = loadingFile === f.path;

          return (
            <div
              key={f.path}
              style={{
                ...s.fileItem,
                ...(isActive ? s.fileItemActive : {}),
                ...(isNew && !isActive ? s.fileItemNew : {}),
                ...(isHovered && !isActive ? s.fileItemHover : {}),
              }}
              onMouseEnter={() => setHovered(f.path)}
              onMouseLeave={() => setHovered(null)}
              onDoubleClick={() => handleLoad(f.path)}
            >
              <div style={s.fileName}>
                {isActive && <span style={{ color: '#38bdf8', marginRight: 4 }}>&#9654;</span>}
                {isNew && !isActive && <span style={{ color: '#22c55e', marginRight: 4 }}>&#9679;</span>}
                {f.name}
              </div>
              <div style={s.fileMeta}>
                <span>{f.size_display}</span>
                <span>{f.modified}</span>
              </div>
              <div style={s.fileDir} title={f.directory}>{f.directory}</div>
              {isHovered && !isActive && (
                <button
                  style={s.loadBtn}
                  onClick={() => handleLoad(f.path)}
                  disabled={isLoading}
                >
                  {isLoading ? 'Loading...' : 'Load & Solve'}
                </button>
              )}
              {isActive && (
                <span style={{ fontSize: 10, color: '#38bdf8', marginTop: 4 }}>
                  Currently loaded
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Manual path input */}
      <div style={s.manualInput}>
        <input
          style={s.input}
          placeholder="Or paste a file path..."
          value={manualPath}
          onChange={(e) => setManualPath(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleManualLoad()}
        />
        <button
          style={{ ...s.scanBtn, flex: 'none', padding: '6px 12px' }}
          onClick={handleManualLoad}
          disabled={!manualPath.trim()}
        >
          Load
        </button>
      </div>

      {/* Status bar */}
      <div style={s.statusBar}>
        <span>{status}</span>
        <span>{files.length} file(s)</span>
      </div>
    </div>
  );
}
