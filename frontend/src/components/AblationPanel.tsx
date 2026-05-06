import React, { useState, useEffect } from 'react';
import { api } from '../utils/api';

interface Props { dark: boolean; }

interface Config { config_id: string; description: string; disable_safety?: boolean; disable_rag?: boolean; temperature?: number; }
interface Summary { config_id: string; description: string; success_rate: number; converge_rate: number; mean_tools: number; mean_duration_s: number; }

export default function AblationPanel({ dark }: Props) {
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';
  const bg   = dark ? '#0f172a' : '#f8fafc';

  const [configs,   setConfigs]   = useState<Config[]>([]);
  const [summary,   setSummary]   = useState<Summary[]>([]);
  const [selected,  setSelected]  = useState<string[]>([]);
  const [running,   setRunning]   = useState(false);
  const [runs,      setRuns]      = useState(3);
  const [error,     setError]     = useState('');
  const [open,      setOpen]      = useState(false);

  useEffect(() => {
    api.ablationConfigs().then((d: any) => {
      setConfigs(Object.values(d?.configs || d || {}));
    }).catch(() => {});
    api.ablationSummary().then((d: any) => {
      setSummary(Object.values(d?.per_config || {}));
    }).catch(() => {});
  }, []);

  const toggle = (id: string) =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);

  const runAblation = async () => {
    if (selected.length === 0) { setError('Select at least one config'); return; }
    setError(''); setRunning(true);
    try {
      const d: any = await api.ablationRun({ config_ids: selected, task_ids: ['all'], n_runs: runs });
      if (d?.per_config) setSummary(Object.values(d.per_config));
    } catch (e: any) { setError(e.message || 'Run failed'); }
    finally { setRunning(false); }
  };

  const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
  const bar = (v: number, color: string) => (
    <div style={{ position: 'relative', height: 6, background: dark ? '#334155' : '#e2e8f0', borderRadius: 3, overflow: 'hidden', width: '100%' }}>
      <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${v * 100}%`, background: color, borderRadius: 3 }} />
    </div>
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
        <span style={{ fontWeight: 700, color: txt, fontSize: 12 }}>🧪 Ablation Study</span>
        <span style={{ color: dim, fontSize: 11 }}>{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div style={{ padding: '8px 12px', background: bg }}>
          {/* Configs */}
          <div style={{ color: dim, fontSize: 10, fontWeight: 600, marginBottom: 6 }}>SELECT CONFIGURATIONS</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
            {configs.map((c: Config) => (
              <button key={c.config_id} onClick={() => toggle(c.config_id)}
                title={c.description}
                style={{
                background: selected.includes(c.config_id) ? '#0ea5e9' : '#1e293b',
                color: selected.includes(c.config_id) ? '#fff' : dim,
                border: `1px solid ${selected.includes(c.config_id) ? '#0ea5e9' : brd}`,
                borderRadius: 5, padding: '3px 8px', fontSize: 10, cursor: 'pointer',
              }}>{c.config_id}</button>
            ))}
            <button onClick={() => setSelected(configs.map(c => c.config_id))} style={{
              background: 'none', color: '#38bdf8', border: `1px solid ${brd}`,
              borderRadius: 5, padding: '3px 8px', fontSize: 10, cursor: 'pointer',
            }}>All</button>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <span style={{ color: dim, fontSize: 10 }}>Runs each:</span>
            {[1, 3, 5].map(n => (
              <button key={n} onClick={() => setRuns(n)} style={{
                background: runs === n ? '#0ea5e9' : 'none',
                color: runs === n ? '#fff' : dim,
                border: `1px solid ${runs === n ? '#0ea5e9' : brd}`,
                borderRadius: 4, padding: '2px 7px', fontSize: 10, cursor: 'pointer',
              }}>{n}</button>
            ))}
          </div>

          {error && <div style={{ color: '#f87171', fontSize: 11, marginBottom: 6 }}>{error}</div>}
          <button onClick={runAblation} disabled={running} style={{
            width: '100%', background: running ? '#334155' : '#7c3aed',
            color: '#fff', border: 'none', borderRadius: 6,
            padding: '6px 0', fontWeight: 700, fontSize: 11,
            cursor: running ? 'not-allowed' : 'pointer', marginBottom: 10,
          }}>
            {running ? '⏳ Running ablation…' : '▶ Run Ablation Study'}
          </button>

          {/* Results table */}
          {summary.length > 0 && (
            <>
              <div style={{ color: dim, fontSize: 10, fontWeight: 600, marginBottom: 6 }}>RESULTS (A0 = full system baseline)</div>
              {summary.sort((a, b) => b.success_rate - a.success_rate).map((s: Summary) => (
                <div key={s.config_id} style={{
                  background: card, border: `1px solid ${s.config_id === 'A0' ? '#0ea5e9' : brd}`,
                  borderRadius: 8, padding: '8px 10px', marginBottom: 6,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
                    <span style={{
                      fontFamily: 'monospace', fontWeight: 700,
                      color: s.config_id === 'A0' ? '#38bdf8' : txt, fontSize: 12,
                    }}>{s.config_id}</span>
                    <span style={{
                      color: s.success_rate >= 0.8 ? '#4ade80' : s.success_rate >= 0.5 ? '#fbbf24' : '#f87171',
                      fontWeight: 700, fontSize: 12,
                    }}>{pct(s.success_rate)}</span>
                  </div>
                  <div style={{ color: dim, fontSize: 10, marginBottom: 5 }}>{s.description}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                    <div>
                      <div style={{ color: dim, fontSize: 9, marginBottom: 2 }}>Success rate</div>
                      {bar(s.success_rate, '#4ade80')}
                    </div>
                    <div>
                      <div style={{ color: dim, fontSize: 9, marginBottom: 2 }}>Convergence</div>
                      {bar(s.converge_rate, '#38bdf8')}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, marginTop: 5 }}>
                    <span style={{ color: dim, fontSize: 10 }}>Tools: {s.mean_tools?.toFixed(1)}</span>
                    <span style={{ color: dim, fontSize: 10 }}>Time: {s.mean_duration_s?.toFixed(1)}s</span>
                  </div>
                </div>
              ))}
            </>
          )}

          {summary.length === 0 && (
            <div style={{ color: dim, fontSize: 11, textAlign: 'center', padding: '10px 0' }}>
              No ablation results yet. Select configs and click Run.
              <br/>
              <span style={{ fontSize: 10 }}>Full study (200 interactions) requires ~$5 paid API credits.</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
