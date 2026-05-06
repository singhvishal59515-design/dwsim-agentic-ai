import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';

interface Props { dark: boolean; }

interface ProviderInfo { name: string; model: string; key_present: boolean; status?: string; }
interface VersionInfo { dwsim_version: string; bridge_ready: boolean; dll_folder: string; python_version?: string; }

export default function DiagnosticsPanel({ dark }: Props) {
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  const [version,   setVersion]   = useState<VersionInfo | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [diag,      setDiag]      = useState<any>(null);
  const [open,      setOpen]      = useState(false);
  const [loading,   setLoading]   = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [v, p, d] = await Promise.allSettled([
        api.diagnosticsVersion(),
        api.diagnosticsProviders(),
        api.diagnostics(),
      ]);
      if (v.status === 'fulfilled') setVersion(v.value);
      if (p.status === 'fulfilled') setProviders(p.value?.providers || []);
      if (d.status === 'fulfilled') setDiag(d.value);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (open) load(); }, [open, load]);

  const statusDot = (ok: boolean, warn?: boolean) => (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: ok ? '#4ade80' : warn ? '#fbbf24' : '#f87171',
      marginRight: 5,
    }} />
  );

  return (
    <div style={{ borderBottom: `1px solid ${brd}` }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '8px 12px', cursor: 'pointer', background: card, userSelect: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {statusDot(version?.bridge_ready ?? true)}
          <span style={{ fontWeight: 700, color: txt, fontSize: 12 }}>⚙ System Diagnostics</span>
        </div>
        <span style={{ color: dim, fontSize: 11 }}>{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div style={{ padding: '8px 12px', background: dark ? '#0f172a' : '#f8fafc' }}>
          {loading && <div style={{ color: dim, fontSize: 11 }}>Loading…</div>}

          {/* DWSIM version */}
          {version && (
            <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
              <div style={{ color: dim, fontSize: 10, marginBottom: 4, fontWeight: 600 }}>DWSIM Bridge</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 4 }}>
                <div style={{ fontSize: 11 }}>
                  {statusDot(version.bridge_ready)}
                  <span style={{ color: txt }}>{version.bridge_ready ? 'Ready' : 'Not initialised'}</span>
                </div>
                <span style={{ color: '#38bdf8', fontFamily: 'monospace', fontSize: 11, fontWeight: 700 }}>
                  v{version.dwsim_version}
                </span>
              </div>
              {version.dll_folder && (
                <div style={{ color: dim, fontSize: 10, marginTop: 3, wordBreak: 'break-all' }}>
                  {version.dll_folder}
                </div>
              )}
              {version.python_version && (
                <div style={{ color: dim, fontSize: 10, marginTop: 2 }}>Python {version.python_version}</div>
              )}
            </div>
          )}

          {/* LLM providers */}
          {providers.length > 0 && (
            <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
              <div style={{ color: dim, fontSize: 10, marginBottom: 6, fontWeight: 600 }}>LLM Providers</div>
              {providers.map((p: ProviderInfo) => (
                <div key={p.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    {statusDot(p.key_present, !p.key_present)}
                    <span style={{ color: txt, fontSize: 11, fontWeight: 600 }}>{p.name}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ color: dim, fontSize: 10 }}>{p.model}</span>
                    <span style={{
                      fontSize: 9, padding: '1px 5px', borderRadius: 4,
                      background: p.key_present ? '#14532d' : '#450a0a',
                      color: p.key_present ? '#86efac' : '#fca5a5',
                    }}>{p.key_present ? 'key ✓' : 'no key'}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* General diagnostics */}
          {diag && (
            <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
              <div style={{ color: dim, fontSize: 10, marginBottom: 4, fontWeight: 600 }}>System Info</div>
              {Object.entries(diag).filter(([k]) => !['success','providers','dwsim_version','bridge_ready','dll_folder'].includes(k)).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                  <span style={{ color: dim, fontSize: 10 }}>{k.replace(/_/g, ' ')}</span>
                  <span style={{ color: txt, fontSize: 10, fontFamily: 'monospace' }}>{String(v)}</span>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={load}
            style={{
              width: '100%', background: '#1e293b', color: '#38bdf8',
              border: `1px solid ${brd}`, borderRadius: 5,
              padding: '4px 0', fontSize: 11, cursor: 'pointer',
            }}
          >↻ Refresh</button>
        </div>
      )}
    </div>
  );
}
