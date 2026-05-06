import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';

interface SFEntry {
  code: string; name: string; severity: string;
  description: string; status?: string; where_fixed?: string;
  detection?: string;
}
interface BenchTask { task_id: string; description: string; category?: string; }
interface BenchResult { outcome: string; time_s: number; speedup_x?: number; }

interface ReplayTurn {
  turn_id: string; session_id: string; timestamp: string;
  prompt_hash: string; model: string; temperature: number;
  n_tools: number; converged: boolean; duration_s: number; sf_viols: number;
}

interface ReproInfo {
  prompt_hash: string; tool_sequence: string[]; n_tools: number;
  provider: string; model: string; temperature: number; seed: number;
  duration_s: number; session_summary?: { turns: number; last_turn_id?: string; converged_pct?: number };
}

interface Props { dark: boolean; }

export default function SafetyPanel({ dark }: Props) {
  const [catalogue,   setCatalogue]   = useState<SFEntry[]>([]);
  const [tasks,       setTasks]       = useState<BenchTask[]>([]);
  const [running,     setRunning]     = useState('');
  const [results,     setResults]     = useState<Record<string, BenchResult>>({});
  const [catOpen,     setCatOpen]     = useState(false);
  const [benchOpen,   setBenchOpen]   = useState(false);
  const [replayOpen,  setReplayOpen]  = useState(false);
  const [reproOpen,   setReproOpen]   = useState(false);
  const [replayTurns, setReplayTurns] = useState<ReplayTurn[]>([]);
  const [replayLoading, setReplayLoading] = useState(false);
  const [reproInfo,   setReproInfo]   = useState<ReproInfo | null>(null);
  const [copyMsg,     setCopyMsg]     = useState('');

  const brd  = dark ? '#334155' : '#e2e8f0';
  const card = dark ? '#1e293b' : '#fff';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const acc  = '#38bdf8';

  useEffect(() => {
    api.safetyCatalogue().then((d: any) => {
      if (Array.isArray(d?.failures)) setCatalogue(d.failures);
      else if (Array.isArray(d?.catalogue)) setCatalogue(d.catalogue);
    }).catch(() => {});
    api.benchmarkTasks().then((d: any) => {
      if (Array.isArray(d?.tasks)) setTasks(d.tasks);
    }).catch(() => {});
  }, []);

  const loadReplay = useCallback(async () => {
    setReplayLoading(true);
    try {
      const d: any = await api.replayTurns('', 30);
      if (d?.turns) setReplayTurns(d.turns);
    } catch { /* ignore */ }
    finally { setReplayLoading(false); }
  }, []);

  const loadRepro = useCallback(async () => {
    try {
      const d: any = await api.reproducibility();
      if (d?.success) setReproInfo(d);
    } catch { /* ignore */ }
  }, []);

  const toggleReplay = () => {
    if (!replayOpen) loadReplay();
    setReplayOpen(o => !o);
  };

  const toggleRepro = () => {
    if (!reproOpen) loadRepro();
    setReproOpen(o => !o);
  };

  const copyHash = (hash: string) => {
    navigator.clipboard.writeText(hash).then(() => {
      setCopyMsg('Copied!');
      setTimeout(() => setCopyMsg(''), 1500);
    });
  };

  const runBenchmark = async (id: string) => {
    setRunning(id);
    try {
      const r: any = await api.benchmarkRun(id);
      setResults(prev => ({ ...prev, [id]: r }));
    } catch {
      setResults(prev => ({ ...prev, [id]: { outcome: 'ERROR', time_s: 0 } }));
    } finally { setRunning(''); }
  };

  // severity → colour
  const SEV_COLOR: Record<string, string> = {
    SILENT: '#f87171', WARNING: '#fbbf24', LOUD: '#fb923c',
    CRITICAL: '#f87171', HIGH: '#fb923c', MEDIUM: '#fbbf24',
    LOW: '#a3e635', INFO: '#60a5fa', DETECTED: '#a78bfa',
  };

  // SF-09 is the new global balance check — highlight it
  const SF09_CODES = new Set(['SF-09', 'SF-09a', 'SF-09b', 'SF-09c']);

  const section = (label: string, open: boolean, toggle: () => void, badge?: string | number) => (
    <div
      onClick={toggle}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 12px', cursor: 'pointer', borderBottom: `1px solid ${brd}`,
        background: card, userSelect: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontWeight: 700, color: txt, fontSize: 12 }}>{label}</span>
        {badge !== undefined && badge !== '' && (
          <span style={{
            background: '#1e40af', color: '#bfdbfe',
            borderRadius: 9, padding: '1px 7px', fontSize: 10, fontWeight: 700,
          }}>{badge}</span>
        )}
      </div>
      <span style={{ color: dim, fontSize: 12 }}>{open ? '▲' : '▼'}</span>
    </div>
  );

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: bg }}>

      {/* ── Silent Failure Catalogue ─────────────────────────────────────── */}
      {section('🛡 Silent Failure Catalogue', catOpen, () => setCatOpen(o => !o), catalogue.length)}
      {catOpen && (
        <div style={{ padding: '8px 12px', borderBottom: `1px solid ${brd}` }}>
          {catalogue.length === 0 && <div style={{ color: dim, fontSize: 12 }}>Loading…</div>}
          {catalogue.map(sf => (
            <div key={sf.code} style={{
              background: SF09_CODES.has(sf.code)
                ? (dark ? '#1a2744' : '#eff6ff')   // highlight SF-09 in blue
                : card,
              border: `1px solid ${SF09_CODES.has(sf.code) ? '#3b82f6' : brd}`,
              borderRadius: 8, padding: 10, marginBottom: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                <span style={{
                  background: '#0f172a', color: SF09_CODES.has(sf.code) ? '#60a5fa' : acc,
                  fontFamily: 'monospace', padding: '1px 7px',
                  borderRadius: 4, fontSize: 11, fontWeight: 700,
                }}>{sf.code}</span>
                {sf.status && (
                  <span style={{
                    color: sf.status === 'FIXED' ? '#86efac'
                         : sf.status?.startsWith('DETECTED') ? '#fbbf24' : dim,
                    fontSize: 10, fontWeight: 600,
                  }}>{sf.status}</span>
                )}
                {sf.severity && (
                  <span style={{ color: SEV_COLOR[sf.severity] || dim, fontSize: 10 }}>
                    {sf.severity}
                  </span>
                )}
              </div>
              <div style={{ color: txt, fontSize: 11, marginBottom: sf.where_fixed ? 4 : 0 }}>
                {sf.description}
              </div>
              {sf.where_fixed && (
                <div style={{ color: dim, fontSize: 10, fontStyle: 'italic', marginTop: 3 }}>
                  Where fixed: {sf.where_fixed}
                </div>
              )}
              {sf.detection && (
                <div style={{ color: dim, fontSize: 10, fontStyle: 'italic' }}>
                  Detection: {sf.detection}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Reproducibility fingerprint ──────────────────────────────────── */}
      {section('🔑 Last Turn Reproducibility', reproOpen, toggleRepro)}
      {reproOpen && (
        <div style={{ padding: '10px 12px', borderBottom: `1px solid ${brd}` }}>
          {!reproInfo ? (
            <div style={{ color: dim, fontSize: 12 }}>
              No turn recorded yet. Send a chat message first.
            </div>
          ) : (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
                {[
                  ['Provider', `${reproInfo.provider}/${reproInfo.model}`],
                  ['Temperature', String(reproInfo.temperature)],
                  ['Seed', String(reproInfo.seed)],
                  ['Tools called', String(reproInfo.n_tools)],
                  ['Duration', `${reproInfo.duration_s?.toFixed(1)}s`],
                  ['Session turns', String(reproInfo.session_summary?.turns ?? '–')],
                  ['Converged %', reproInfo.session_summary?.converged_pct != null
                    ? `${reproInfo.session_summary.converged_pct}%` : '–'],
                ].map(([k, v]) => (
                  <div key={k} style={{ background: card, border: `1px solid ${brd}`, borderRadius: 6, padding: '5px 8px' }}>
                    <div style={{ color: dim, fontSize: 10 }}>{k}</div>
                    <div style={{ color: txt, fontSize: 12, fontWeight: 600 }}>{v}</div>
                  </div>
                ))}
              </div>
              <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 6, padding: '6px 8px', marginBottom: 8 }}>
                <div style={{ color: dim, fontSize: 10, marginBottom: 2 }}>Prompt hash (SHA-256 prefix)</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <code style={{ color: '#a78bfa', fontSize: 12, fontFamily: 'monospace' }}>
                    {reproInfo.prompt_hash || '—'}
                  </code>
                  {reproInfo.prompt_hash && (
                    <button
                      onClick={() => copyHash(reproInfo.prompt_hash)}
                      style={{ background: '#1e293b', color: acc, border: `1px solid ${brd}`, borderRadius: 4, padding: '2px 8px', fontSize: 10, cursor: 'pointer' }}
                    >{copyMsg || 'Copy'}</button>
                  )}
                </div>
              </div>
              {reproInfo.tool_sequence?.length > 0 && (
                <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 6, padding: '6px 8px' }}>
                  <div style={{ color: dim, fontSize: 10, marginBottom: 4 }}>Tool sequence</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {reproInfo.tool_sequence.map((t, i) => (
                      <span key={i} style={{
                        background: '#0f172a', color: '#7dd3fc',
                        fontFamily: 'monospace', fontSize: 10,
                        padding: '2px 6px', borderRadius: 4,
                      }}>{i + 1}. {t}</span>
                    ))}
                  </div>
                </div>
              )}
              <button
                onClick={loadRepro}
                style={{
                  marginTop: 8, background: '#1e293b', color: acc,
                  border: `1px solid ${brd}`, borderRadius: 5,
                  padding: '4px 12px', fontSize: 11, cursor: 'pointer', width: '100%',
                }}
              >↻ Refresh</button>
            </>
          )}
        </div>
      )}

      {/* ── Replay log ───────────────────────────────────────────────────── */}
      {section('📼 Replay Log', replayOpen, toggleReplay,
        replayTurns.length > 0 ? replayTurns.length : undefined)}
      {replayOpen && (
        <div style={{ padding: '8px 12px', borderBottom: `1px solid ${brd}` }}>
          <div style={{ color: dim, fontSize: 10, marginBottom: 8 }}>
            Every agent turn is logged with full tool trace, prompt hash, and stream snapshot
            for independent reproducibility verification.
          </div>
          {replayLoading && <div style={{ color: dim, fontSize: 12 }}>Loading…</div>}
          {!replayLoading && replayTurns.length === 0 && (
            <div style={{ color: dim, fontSize: 12 }}>
              No turns recorded yet. Log is written to{' '}
              <code style={{ color: acc, fontSize: 11 }}>~/.dwsim_agent/replay/replay_log.jsonl</code>
            </div>
          )}
          {replayTurns.map(t => (
            <div key={t.turn_id} style={{
              background: card, border: `1px solid ${brd}`, borderRadius: 8,
              padding: 9, marginBottom: 6,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 3 }}>
                <code style={{ color: '#a78bfa', fontSize: 10, fontFamily: 'monospace' }}>
                  {t.turn_id.slice(0, 8)}
                </code>
                <span style={{ color: dim, fontSize: 10 }}>
                  {new Date(t.timestamp).toLocaleTimeString()}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
                <span style={{ fontSize: 10, color: dim }}>{t.model}</span>
                <span style={{ fontSize: 10, color: dim }}>T={t.temperature}</span>
                <span style={{ fontSize: 10, color: '#7dd3fc' }}>{t.n_tools} tools</span>
                <span style={{ fontSize: 10, color: t.converged ? '#86efac' : '#f87171' }}>
                  {t.converged ? 'converged' : 'not converged'}
                </span>
                <span style={{ fontSize: 10, color: dim }}>{t.duration_s.toFixed(1)}s</span>
                {t.sf_viols > 0 && (
                  <span style={{ fontSize: 10, color: '#fbbf24' }}>
                    {t.sf_viols} SF violation{t.sf_viols > 1 ? 's' : ''}
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ color: dim, fontSize: 9 }}>hash:</span>
                <code style={{ color: '#c084fc', fontSize: 9, fontFamily: 'monospace' }}>
                  {t.prompt_hash}
                </code>
                <button
                  onClick={() => copyHash(t.prompt_hash)}
                  style={{
                    background: 'transparent', color: dim, border: 'none',
                    cursor: 'pointer', fontSize: 9, padding: '0 4px',
                  }}
                >⎘</button>
              </div>
            </div>
          ))}
          {!replayLoading && (
            <button
              onClick={loadReplay}
              style={{
                marginTop: 4, background: '#1e293b', color: acc,
                border: `1px solid ${brd}`, borderRadius: 5,
                padding: '4px 12px', fontSize: 11, cursor: 'pointer', width: '100%',
              }}
            >↻ Refresh</button>
          )}
          <div style={{ color: dim, fontSize: 10, marginTop: 8, lineHeight: 1.4 }}>
            CLI: <code style={{ color: acc }}>python replay_log.py replay --turn &lt;id&gt;</code>
          </div>
        </div>
      )}

      {/* ── Benchmark Tasks ──────────────────────────────────────────────── */}
      {section(`⚡ Benchmark Tasks`, benchOpen, () => setBenchOpen(o => !o), tasks.length || undefined)}
      {benchOpen && (
        <div style={{ padding: '8px 12px' }}>
          {tasks.length === 0 && <div style={{ color: dim, fontSize: 12 }}>No tasks available.</div>}
          {tasks.map(t => {
            const res = results[t.task_id];
            const ok  = res?.outcome === 'SUCCESS';
            return (
              <div key={t.task_id} style={{
                background: card, border: `1px solid ${brd}`, borderRadius: 8,
                padding: 10, marginBottom: 6,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontFamily: 'monospace', color: acc, fontSize: 12, fontWeight: 700 }}>
                    {t.task_id}
                  </span>
                  {t.category && (
                    <span style={{ color: dim, fontSize: 10, background: '#1e293b', padding: '1px 6px', borderRadius: 4 }}>
                      {t.category}
                    </span>
                  )}
                </div>
                <div style={{ color: dim, fontSize: 11, marginBottom: 6 }}>{t.description}</div>
                {res ? (
                  <div style={{ fontSize: 11 }}>
                    <span style={{ color: ok ? '#86efac' : '#f87171', fontWeight: 700 }}>
                      {ok ? '✓' : '✗'} {res.outcome}
                    </span>
                    {' · '}<span style={{ color: dim }}>{res.time_s}s</span>
                    {res.speedup_x && (
                      <span style={{ color: '#a3e635', marginLeft: 6 }}>⚡ {res.speedup_x}× faster</span>
                    )}
                  </div>
                ) : (
                  <button
                    onClick={() => runBenchmark(t.task_id)}
                    disabled={!!running}
                    style={{
                      background: running === t.task_id ? '#334155' : '#1e293b',
                      color: running === t.task_id ? dim : acc,
                      border: `1px solid ${brd}`, borderRadius: 5,
                      padding: '3px 10px', cursor: 'pointer', fontSize: 11,
                    }}
                  >
                    {running === t.task_id ? 'Running…' : '▶ Run'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
