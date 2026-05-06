import React, { useState, useEffect } from 'react';
import { FlowsheetState } from '../types';
import { api } from '../utils/api';

const RECENT_KEY = 'dwsim_react_recent';

function getRecent(): string[] {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
}
function addRecent(p: string) {
  const prev = getRecent().filter(r => r !== p).slice(0, 9);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([p, ...prev])); } catch {}
}
function clearRecent() {
  try { localStorage.removeItem(RECENT_KEY); } catch {}
}

interface Props {
  dark:       boolean;
  state:      FlowsheetState | null;
  onLoad:     (p: string) => void;
  onRun:      () => void;
  onSave:     (push?: boolean) => void;
  simLoading: boolean;
}

const LBL  = (dark: boolean): React.CSSProperties =>
  ({ fontSize: 10, color: dark ? '#64748b' : '#94a3b8', fontWeight: 700, letterSpacing: 0.8, marginBottom: 6, textTransform: 'uppercase' });
const SEC  = (dark: boolean): React.CSSProperties =>
  ({ padding: '12px 14px', borderBottom: `1px solid ${dark ? '#1e293b' : '#e2e8f0'}` });

export default function FlowsheetPanel({ dark, state, onLoad, onRun, onSave, simLoading }: Props) {
  const [path,   setPath]   = useState('');
  const [recent, setRecent] = useState<string[]>(getRecent());

  const BRD = dark ? '#334155' : '#e2e8f0';
  const TXT = dark ? '#e2e8f0' : '#1e293b';
  const DIM = dark ? '#64748b' : '#94a3b8';
  const INP = dark ? '#0f172a' : '#f8fafc';

  const btn = (bg: string, col: string, dis = false): React.CSSProperties => ({
    width: '100%', background: dis ? (dark ? '#334155' : '#e2e8f0') : bg,
    color: dis ? DIM : col,
    border: 'none', borderRadius: 6, padding: '7px 0',
    fontWeight: 600, cursor: dis ? 'not-allowed' : 'pointer', fontSize: 13,
  });

  const doLoad = () => {
    if (!path) return;
    addRecent(path);
    setRecent(getRecent());
    onLoad(path);
  };

  const findOnDisk = async () => {
    try {
      const d: any = await api.findFlowsheets();
      if (d?.flowsheets?.[0]?.path) {
        setPath(d.flowsheets[0].path);
      } else if (d?.files?.[0]) {
        setPath(d.files[0]);
      }
    } catch {}
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto', background: dark ? '#1e293b' : '#fff' }}>

      {/* Flowsheet info */}
      <div style={SEC(dark)}>
        <div style={LBL(dark)}>Flowsheet</div>
        {state?.name ? (
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: TXT, marginBottom: 2 }}>📄 {state.name}</div>
            {state.property_package && <div style={{ fontSize: 11, color: '#86efac' }}>⚗ {state.property_package}</div>}
            {state.path && <div style={{ fontSize: 10, color: DIM, wordBreak: 'break-all', marginTop: 2 }}>{state.path}</div>}
            <div style={{ fontSize: 11, color: DIM, marginTop: 4 }}>
              {state.streams.length} streams · {state.unit_ops.length} unit ops
            </div>
            {state.converged != null && (
              <div style={{ fontSize: 11, marginTop: 3, color: state.converged ? '#86efac' : '#f87171' }}>
                {state.converged ? '✓ Converged' : '✗ Not converged'}
              </div>
            )}
          </div>
        ) : <div style={{ color: DIM, fontSize: 12 }}>No flowsheet loaded</div>}
      </div>

      {/* Load */}
      <div style={SEC(dark)}>
        <div style={LBL(dark)}>Load Existing</div>
        <input
          value={path}
          onChange={e => setPath(e.target.value)}
          placeholder="C:\path\to\flowsheet.dwxmz"
          style={{
            width: '100%', boxSizing: 'border-box',
            background: INP, border: `1px solid ${BRD}`, borderRadius: 6,
            color: TXT, padding: '6px 8px', fontSize: 12, outline: 'none', marginBottom: 6,
          }}
        />
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <button style={{ ...btn('#0ea5e9', '#fff', !path || simLoading), flex: 2 }}
            onClick={doLoad} disabled={!path || simLoading}>
            {simLoading ? 'Loading…' : 'Load & Solve'}
          </button>
          <button style={{ ...btn('#334155', '#94a3b8'), flex: 1, border: `1px solid ${BRD}` }}
            onClick={findOnDisk}>
            Find
          </button>
        </div>

        {/* Recent */}
        {recent.length > 0 && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <span style={{ fontSize: 10, color: DIM, fontWeight: 600 }}>RECENT</span>
              <button onClick={() => { clearRecent(); setRecent([]); }}
                style={{ background: 'none', border: 'none', color: DIM, cursor: 'pointer', fontSize: 10 }}>
                clear
              </button>
            </div>
            {recent.slice(0, 5).map(r => (
              <div key={r}
                onClick={() => { setPath(r); doLoad(); }}
                style={{
                  fontSize: 11, color: DIM, cursor: 'pointer', padding: '2px 4px',
                  borderRadius: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}
                onMouseEnter={e => (e.currentTarget.style.color = '#38bdf8')}
                onMouseLeave={e => (e.currentTarget.style.color = DIM)}
                title={r}
              >📁 {r.split(/[\\/]/).pop()}</div>
            ))}
          </div>
        )}
      </div>

      {/* Controls */}
      <div style={SEC(dark)}>
        <div style={LBL(dark)}>Controls</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button style={btn('#16a34a', '#fff', simLoading || !state?.name)}
            onClick={onRun} disabled={simLoading || !state?.name}>
            ▶ Run Simulation
          </button>
          <button style={{ ...btn('#1e293b', TXT, !state?.name), border: `1px solid ${BRD}` }}
            onClick={() => onSave(false)} disabled={!state?.name}>
            💾 Save Flowsheet
          </button>
          <button style={{ ...btn('#1e293b', '#f97316', !state?.name), border: `1px solid ${BRD}` }}
            onClick={() => onSave(true)} disabled={!state?.name}
            title="Save and push to running DWSIM GUI">
            ⇒ Push to DWSIM
          </button>
        </div>
      </div>

      {/* Export */}
      <div style={SEC(dark)}>
        <div style={LBL(dark)}>Export</div>
        <div style={{ display: 'flex', gap: 6 }}>
          <a href="/results/export/excel" download style={{ flex: 1, textDecoration: 'none' }}>
            <button style={{ width: '100%', background: 'none', border: `1px solid ${BRD}`, borderRadius: 6, color: '#86efac', padding: '6px 0', cursor: 'pointer', fontSize: 12 }}>
              ⬇ Excel
            </button>
          </a>
          <a href="/results/export/csv" download style={{ flex: 1, textDecoration: 'none' }}>
            <button style={{ width: '100%', background: 'none', border: `1px solid ${BRD}`, borderRadius: 6, color: DIM, padding: '6px 0', cursor: 'pointer', fontSize: 12 }}>
              ⬇ CSV
            </button>
          </a>
        </div>
      </div>

      {/* Objects */}
      {state?.streams && state.streams.length > 0 && (
        <div style={{ padding: '12px 14px' }}>
          <div style={LBL(dark)}>Objects</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {state.streams.map(s => (
              <span key={s} style={{ background: '#0c4a6e', color: '#7dd3fc', borderRadius: 4, padding: '2px 7px', fontSize: 11 }}>
                ~ {s}
              </span>
            ))}
            {state.unit_ops.map(u => (
              <span key={u} style={{ background: '#1e1b4b', color: '#a5b4fc', borderRadius: 4, padding: '2px 7px', fontSize: 11 }}>
                ⚙ {u}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
