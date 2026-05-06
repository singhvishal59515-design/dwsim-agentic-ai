import React, { useState, useEffect, useRef } from 'react';
import { api } from '../utils/api';
import { LLMStatus, PROVIDER_COLOR, PROVIDER_LABEL } from '../types';

const CLOUD: Record<string, string[]> = {
  openai:    ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
  groq:      ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'gemma2-9b-it'],
  gemini:    ['gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-pro'],
  anthropic: ['claude-sonnet-4-5', 'claude-haiku-4-5'],
};

const S: Record<string, React.CSSProperties> = {
  wrap:    { position: 'relative' },
  trigger: { display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
              background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
              padding: '4px 10px', fontSize: 12, userSelect: 'none' },
  dot:     { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  panel:   { position: 'absolute', top: '100%', right: 0, marginTop: 4,
              background: '#1e293b', border: '1px solid #334155', borderRadius: 10,
              boxShadow: '0 8px 32px rgba(0,0,0,.5)', width: 320, zIndex: 200 },
  hdr:     { background: '#0f172a', padding: '10px 14px', borderBottom: '1px solid #334155',
              fontSize: 11, color: '#94a3b8', fontWeight: 600, letterSpacing: 0.8 },
  sec:     { padding: '10px 14px 4px', borderBottom: '1px solid #1e293b' },
  lbl:     { fontSize: 10, color: '#64748b', fontWeight: 600, letterSpacing: 0.8, marginBottom: 6 },
  row:     { display: 'flex', gap: 6, flexWrap: 'wrap' as const, marginBottom: 8 },
  pbtn:    { padding: '4px 10px', borderRadius: 5, border: '1px solid #334155',
              fontSize: 11, cursor: 'pointer', fontWeight: 600, transition: 'all .15s' },
  sel:     { width: '100%', background: '#0f172a', border: '1px solid #334155',
              borderRadius: 5, color: '#e2e8f0', padding: '6px 10px', fontSize: 12,
              marginBottom: 8, cursor: 'pointer' },
  swbtn:   { width: '100%', padding: 8, borderRadius: 6, border: 'none',
              background: '#0ea5e9', color: '#fff', fontSize: 13, fontWeight: 600,
              cursor: 'pointer', marginTop: 2, marginBottom: 6 },
  swoff:   { background: '#334155', color: '#64748b', cursor: 'not-allowed' },
  footer:  { padding: '8px 14px', fontSize: 11, color: '#64748b',
              display: 'flex', alignItems: 'center', gap: 6 },
  toast:   { position: 'fixed' as const, bottom: 24, right: 24, zIndex: 999,
              background: '#0f172a', border: '1px solid #334155', borderRadius: 10,
              padding: '12px 18px', fontSize: 13, color: '#e2e8f0',
              boxShadow: '0 8px 24px rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', gap: 8 },
};

export default function LLMSelector({ onSwitch, dark }: { onSwitch?: (p: string, m: string) => void; dark?: boolean }) {
  const [open, setOpen]             = useState(false);
  const [status, setStatus]         = useState<LLMStatus>({ provider: 'groq', model: 'llama-3.3-70b-versatile' });
  const [selProv, setSelProv]       = useState('groq');
  const [selModel, setSelModel]     = useState('llama-3.3-70b-versatile');
  const [groqModels, setGroqModels] = useState<string[]>([]);
  const [ollModels, setOllModels]   = useState<string[]>([]);
  const [ollErr, setOllErr]         = useState('');
  const [ollLoading, setOllLoading] = useState(false);
  const [switching, setSwitching]   = useState(false);
  const [toast, setToast]           = useState('');
  const [keyed, setKeyed]           = useState<Record<string, boolean>>({});
  const ref = useRef<HTMLDivElement>(null);

  // Load current status
  useEffect(() => {
    api.llmStatus().then((r: any) => {
      setStatus({ provider: r.provider || 'groq', model: r.model || '' });
      setSelProv(r.provider || 'groq');
      setSelModel(r.model || '');
      if (r.providers_available) setKeyed(r.providers_available);
    }).catch(() => {});
    api.groqModels().then((r: any) => {
      if (r.models) setGroqModels(r.models.map((m: any) => m.id || m));
    }).catch(() => {});
  }, []);

  // Close on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  // Load Ollama models when selected
  useEffect(() => {
    if (selProv !== 'ollama') return;
    setOllLoading(true); setOllErr('');
    api.ollamaModels().then((r: any) => {
      if (r.success && r.models?.length) {
        setOllModels(r.models); setSelModel(r.models[0]); setOllErr('');
      } else {
        setOllModels([]); setOllErr(r.error || 'No models found');
      }
    }).catch(() => {
      setOllModels([]); setOllErr('Cannot reach Ollama — is it running?');
    }).finally(() => setOllLoading(false));
  }, [selProv]);

  const availModels = selProv === 'ollama'
    ? ollModels
    : selProv === 'groq'
    ? (groqModels.length ? groqModels : CLOUD.groq)
    : (CLOUD[selProv] || []);

  const doSwitch = async () => {
    if (!selModel) return;
    setSwitching(true);
    try {
      await api.llmSwitch(selProv, selModel);
      setStatus({ provider: selProv, model: selModel });
      setToast(`Switched to ${PROVIDER_LABEL[selProv]} (${selModel})`);
      setTimeout(() => setToast(''), 3000);
      onSwitch?.(selProv, selModel);
      setOpen(false);
    } catch (e: any) {
      setToast('Error: ' + e.message);
      setTimeout(() => setToast(''), 4000);
    } finally { setSwitching(false); }
  };

  const dot = PROVIDER_COLOR[status.provider] || '#64748b';
  const provHasKey = (p: string) =>
    p === 'ollama' ? true : (keyed[p] !== false);   // true when unknown (no status yet)

  const canSwitch = selModel && !switching &&
    provHasKey(selProv) &&
    (selProv !== 'ollama' || ollModels.length > 0);

  return (
    <div ref={ref} style={S.wrap}>
      <div style={S.trigger} onClick={() => setOpen(o => !o)}>
        <span style={{ ...S.dot, background: dot }} />
        <span style={{ color: PROVIDER_COLOR[status.provider] || '#94a3b8', fontWeight: 600 }}>
          {PROVIDER_LABEL[status.provider] || status.provider}
        </span>
        <span style={{ color: '#64748b', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {status.model}
        </span>
        <span style={{ color: '#64748b', fontSize: 10 }}>{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div style={S.panel}>
          <div style={S.hdr}>LLM PROVIDER</div>

          {/* Cloud */}
          <div style={S.sec}>
            <div style={S.lbl}>CLOUD PROVIDERS</div>
            <div style={S.row}>
              {Object.keys(CLOUD).map(p => {
                const hasKey = provHasKey(p);
                return (
                  <button key={p} title={hasKey ? '' : `No API key — set ${p.toUpperCase()}_API_KEY in .env`}
                    style={{
                      ...S.pbtn,
                      background:  selProv === p ? PROVIDER_COLOR[p] : '#0f172a',
                      color:       selProv === p ? '#fff' : hasKey ? '#94a3b8' : '#475569',
                      borderColor: selProv === p ? PROVIDER_COLOR[p] : hasKey ? '#334155' : '#1e293b',
                      opacity:     hasKey ? 1 : 0.45,
                      position:    'relative',
                    }}
                    onClick={() => { setSelProv(p); setSelModel(CLOUD[p][0]); }}>
                    {PROVIDER_LABEL[p]}
                    {!hasKey && (
                      <span style={{ marginLeft: 4, fontSize: 10 }}>🔒</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Ollama */}
          <div style={S.sec}>
            <div style={S.lbl}>LOCAL (OLLAMA)</div>
            <button style={{
              ...S.pbtn, marginBottom: 8,
              background:  selProv === 'ollama' ? '#9b59b6' : '#0f172a',
              color:       selProv === 'ollama' ? '#fff' : '#94a3b8',
              borderColor: selProv === 'ollama' ? '#9b59b6' : '#334155',
            }} onClick={() => setSelProv('ollama')}>
              Ollama (Local)
            </button>
            {selProv === 'ollama' && (
              ollLoading
                ? <div style={{ color: '#38bdf8', fontSize: 11, padding: '6px 0' }}>Fetching models…</div>
                : ollErr
                ? <div style={{ background: '#3b1f1f', border: '1px solid #7f1d1d', borderRadius: 6,
                                padding: '8px 10px', color: '#f87171', fontSize: 11, marginBottom: 6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Ollama not found</div>
                    <div>{ollErr}</div>
                    <div style={{ marginTop: 6, color: '#fca5a5', fontSize: 10 }}>
                      Install: <strong>ollama.com/download</strong><br />
                      Pull: <code>ollama pull llama3.2</code>
                    </div>
                  </div>
                : ollModels.length > 0
                ? <div style={{ background: '#1a2e1a', border: '1px solid #166534', borderRadius: 6,
                                padding: '6px 10px', color: '#86efac', fontSize: 11, marginBottom: 6 }}>
                    {ollModels.length} model{ollModels.length > 1 ? 's' : ''} available
                  </div>
                : null
            )}
          </div>

          {/* Model selector */}
          {availModels.length > 0 && (
            <div style={{ padding: '10px 14px 0' }}>
              <div style={S.lbl}>MODEL</div>
              <select style={S.sel} value={selModel} onChange={e => setSelModel(e.target.value)}>
                {availModels.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}

          {/* No-key warning */}
          {!provHasKey(selProv) && (
            <div style={{ margin: '0 14px 8px', background: '#1c0a0a', border: '1px solid #7f1d1d',
                          borderRadius: 6, padding: '8px 10px', fontSize: 11, color: '#fca5a5' }}>
              No API key for <strong>{PROVIDER_LABEL[selProv]}</strong>.
              Add <code style={{ background: '#2d1010', padding: '1px 4px', borderRadius: 3 }}>
                {selProv.toUpperCase()}_API_KEY
              </code> to your <code>.env</code> file and restart the server.
            </div>
          )}

          {/* Switch */}
          <div style={{ padding: '4px 14px 12px' }}>
            <button style={{ ...S.swbtn, ...(canSwitch ? {} : S.swoff) }}
                    disabled={!canSwitch} onClick={doSwitch}>
              {switching ? 'Switching…' : `Use ${PROVIDER_LABEL[selProv] || selProv}`}
            </button>
          </div>

          <div style={S.footer}>
            <span style={{ ...S.dot, background: dot }} />
            <span>Active: <strong style={{ color: '#e2e8f0' }}>{status.provider}</strong></span>
            <span style={{ color: '#475569' }}>|</span>
            <span style={{ color: '#94a3b8' }}>{status.model}</span>
          </div>
        </div>
      )}

      {toast && (
        <div style={S.toast}>
          <span>{toast.startsWith('Error') ? '❌' : '✅'}</span>
          <span>{toast}</span>
        </div>
      )}
    </div>
  );
}
