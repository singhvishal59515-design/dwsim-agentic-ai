/**
 * JudgeDashboard.tsx
 * ──────────────────
 * LangSmith-equivalent visibility panel for the DWSIM Agentic AI.
 * Shows: quality score trends, session table, per-session trace drill-down.
 *
 * Data sources:
 *   GET /eval/sessions       → paginated session list with judge_scores
 *   GET /eval/extended       → aggregate metrics (EOS rate, tool efficiency…)
 *   GET /eval/sessions/{id}  → full trace for one session
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../utils/api';

// ── types ─────────────────────────────────────────────────────────────────────

interface JudgeScores {
  property_package_correctness?: number;
  physical_plausibility?:        number;
  completeness?:                 number;
  hallucination_absence?:        number;
  overall?:                      number;
}

interface Session {
  session_id:           string;
  user_message:         string;
  timestamp_iso?:       string;
  duration_s?:          number;
  success:              boolean;
  tool_count:           number;
  failed_tools:         number;
  convergence_achieved? : boolean;
  judge_scores?:        JudgeScores;
  tools_used?:          string[];
  reliability_issues?:  any[];
  benchmark_id?:        string;
}

interface ExtendedMetrics {
  eos_sessions_pct?:          number;
  first_solve_success_rate?:  number;
  avg_tools_per_success?:     number;
  avg_sf_violations?:         number;
  sessions_with_sf?:          number;
  precondition_violations?:   number;
}

// ── score bar ─────────────────────────────────────────────────────────────────

function ScoreBar({ score, max = 5, dark }: { score?: number; max?: number; dark: boolean }) {
  if (score == null) return <span style={{ color: '#475569', fontSize: 10 }}>—</span>;
  const pct = Math.round((score / max) * 100);
  const color = score >= 4 ? '#22c55e' : score >= 3 ? '#f59e0b' : '#ef4444';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div style={{
        width: 48, height: 6, borderRadius: 3,
        background: dark ? '#1e2a45' : '#e2e8f0', overflow: 'hidden',
      }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 10, color, fontWeight: 600 }}>{score.toFixed(1)}</span>
    </div>
  );
}

// ── overall badge ─────────────────────────────────────────────────────────────

function OverallBadge({ score }: { score?: number }) {
  if (score == null) return <span style={{ color: '#475569', fontSize: 11 }}>pending</span>;
  const color = score >= 4 ? '#22c55e' : score >= 3 ? '#f59e0b' : '#ef4444';
  const bg    = score >= 4 ? '#052e16' : score >= 3 ? '#1c1500' : '#1c0a0a';
  return (
    <span style={{
      background: bg, color, border: `1px solid ${color}`,
      borderRadius: 5, padding: '1px 6px', fontSize: 11, fontWeight: 700,
    }}>
      {score.toFixed(1)}/5
    </span>
  );
}

// ── sparkline (mini trend chart — pure CSS/SVG, no library) ──────────────────

function Sparkline({ values, color, dark }: { values: number[]; color: string; dark: boolean }) {
  if (values.length < 2) return null;
  const W = 120, H = 32, PAD = 2;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const pts = values.map((v, i) => {
    const x = PAD + (i / (values.length - 1)) * (W - 2 * PAD);
    const y = H - PAD - ((v - min) / range) * (H - 2 * PAD);
    return `${x},${y}`;
  }).join(' ');
  return (
    <svg width={W} height={H} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      {/* last dot */}
      {values.length > 0 && (() => {
        const i = values.length - 1;
        const x = PAD + (i / (values.length - 1)) * (W - 2 * PAD);
        const y = H - PAD - ((values[i] - min) / range) * (H - 2 * PAD);
        return <circle cx={x} cy={y} r={2.5} fill={color} />;
      })()}
    </svg>
  );
}

// ── session detail modal ──────────────────────────────────────────────────────

function SessionDetailModal({
  sessionId, dark, onClose,
}: { sessionId: string; dark: boolean; onClose: () => void }) {
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const BG  = dark ? '#0f172a' : '#f8fafc';
  const PNL = dark ? '#1e293b' : '#ffffff';
  const BRD = dark ? '#334155' : '#e2e8f0';
  const TXT = dark ? '#e2e8f0' : '#1e293b';
  const DIM = dark ? '#64748b' : '#94a3b8';

  useEffect(() => {
    api.evalSessionDetail(sessionId)
      .then(r => { setDetail(r.session); setLoading(false); })
      .catch(() => setLoading(false));
  }, [sessionId]);

  const scores: JudgeScores = detail?.judge_scores || {};
  const CRITERIA = [
    ['property_package_correctness', 'EOS / Property Package', '#38bdf8'],
    ['physical_plausibility',        'Physical Plausibility',  '#22c55e'],
    ['completeness',                 'Completeness',           '#a855f7'],
    ['hallucination_absence',        'No Hallucinations',      '#f59e0b'],
    ['overall',                      'Overall',                '#e2e8f0'],
  ] as [keyof JudgeScores, string, string][];

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        background: PNL, border: `1px solid ${BRD}`, borderRadius: 12,
        width: 620, maxHeight: '85vh', overflow: 'auto', padding: 20, color: TXT,
      }} onClick={e => e.stopPropagation()}>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: '#38bdf8' }}>Session Detail</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: DIM, cursor: 'pointer', fontSize: 16 }}>✕</button>
        </div>

        {loading && <div style={{ color: DIM, textAlign: 'center', padding: 24 }}>Loading…</div>}

        {detail && !loading && (
          <>
            {/* Query */}
            <div style={{ background: BG, borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 12 }}>
              <span style={{ color: DIM, fontSize: 10, display: 'block', marginBottom: 4 }}>USER QUERY</span>
              {detail.user_message}
            </div>

            {/* Meta row */}
            <div style={{ display: 'flex', gap: 16, marginBottom: 14, fontSize: 11, color: DIM }}>
              <span>{detail.timestamp_iso?.slice(0, 16).replace('T', ' ')}</span>
              <span>⏱ {detail.duration_s?.toFixed(1)}s</span>
              <span>🔧 {detail.tool_count} tools</span>
              {detail.failed_tools > 0 && <span style={{ color: '#ef4444' }}>✗ {detail.failed_tools} failed</span>}
              <span style={{ color: detail.success ? '#22c55e' : '#ef4444' }}>
                {detail.success ? '✔ Succeeded' : '✗ Failed'}
              </span>
            </div>

            {/* Judge scores */}
            {scores.overall != null ? (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontWeight: 600, fontSize: 11, color: DIM, marginBottom: 8, letterSpacing: '0.06em' }}>
                  🧑‍⚖️ AI JUDGE SCORES
                </div>
                {CRITERIA.map(([key, label, color]) => (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <span style={{ fontSize: 11, width: 175, color: TXT }}>{label}</span>
                    <div style={{ flex: 1, height: 6, borderRadius: 3, background: BG, overflow: 'hidden' }}>
                      <div style={{
                        width: `${((scores[key] ?? 0) / 5) * 100}%`,
                        height: '100%', background: color, borderRadius: 3,
                      }} />
                    </div>
                    <span style={{ fontSize: 11, fontWeight: 700, color, width: 28, textAlign: 'right' }}>
                      {scores[key]?.toFixed(1) ?? '—'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ background: BG, borderRadius: 6, padding: 8, fontSize: 11, color: DIM, marginBottom: 12 }}>
                No judge scores yet (evaluation runs async ~5-15s after response)
              </div>
            )}

            {/* Tools used */}
            {detail.tools_used?.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 11, color: DIM, marginBottom: 6, letterSpacing: '0.06em' }}>
                  🔧 TOOLS CALLED ({detail.tools_used.length})
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {detail.tools_used.map((t: string, i: number) => (
                    <span key={i} style={{
                      background: BG, border: `1px solid ${BRD}`,
                      borderRadius: 4, padding: '2px 6px', fontSize: 10, color: '#94a3b8',
                      fontFamily: 'monospace',
                    }}>{t}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Reliability issues */}
            {detail.reliability_issues?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 11, color: '#f87171', marginBottom: 6, letterSpacing: '0.06em' }}>
                  ⚠ RELIABILITY ISSUES
                </div>
                {detail.reliability_issues.map((iss: any, i: number) => (
                  <div key={i} style={{ background: '#1c0a0a', borderRadius: 6, padding: 8, fontSize: 11, color: '#fca5a5', marginBottom: 4 }}>
                    {typeof iss === 'string' ? iss : JSON.stringify(iss)}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── main dashboard ────────────────────────────────────────────────────────────

export default function JudgeDashboard({ dark }: { dark: boolean }) {
  const BG  = dark ? '#0f172a' : '#f8fafc';
  const PNL = dark ? '#1e293b' : '#ffffff';
  const BRD = dark ? '#334155' : '#e2e8f0';
  const TXT = dark ? '#e2e8f0' : '#1e293b';
  const DIM = dark ? '#64748b' : '#94a3b8';

  const [sessions,  setSessions]  = useState<Session[]>([]);
  const [extended,  setExtended]  = useState<ExtendedMetrics>({});
  const [baseMetrics, setBase]    = useState<any>({});
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState<string | null>(null);
  const [selected,  setSelected]  = useState<string | null>(null);
  const [filterMin, setFilterMin] = useState(0);
  const [page,      setPage]      = useState(0);
  const PER_PAGE = 20;
  const totalRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [ext, sess] = await Promise.all([
        api.evalExtended().catch(() => ({ base_metrics: {}, extended_metrics: {} })),
        api.evalSessions(PER_PAGE, page * PER_PAGE, filterMin).catch(() => ({ sessions: [], total: 0 })),
      ]);
      setExtended(ext.extended_metrics || {});
      setBase(ext.base_metrics || {});
      setSessions(sess.sessions || []);
      totalRef.current = sess.total || 0;
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [page, filterMin]);

  useEffect(() => { load(); }, [load]);

  // Trend data from sessions (newest last → oldest first for chart)
  const overallTrend = [...sessions]
    .filter(s => s.judge_scores?.overall != null)
    .reverse()
    .map(s => s.judge_scores!.overall!);

  const successRate = sessions.length > 0
    ? Math.round((sessions.filter(s => s.success).length / sessions.length) * 100)
    : null;

  const avgOverall = overallTrend.length > 0
    ? overallTrend.reduce((a, b) => a + b, 0) / overallTrend.length
    : null;

  const scoredCount = sessions.filter(s => s.judge_scores?.overall != null).length;

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{ height: '100%', overflow: 'auto', background: BG, color: TXT, fontSize: 12 }}>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 12px', borderBottom: `1px solid ${BRD}`, background: PNL,
        position: 'sticky', top: 0, zIndex: 10,
      }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: '#a855f7' }}>🧑‍⚖️ Judge Dashboard</span>
        <button
          onClick={load}
          disabled={loading}
          style={{
            background: 'none', border: `1px solid ${BRD}`, borderRadius: 5,
            color: DIM, cursor: loading ? 'wait' : 'pointer', padding: '2px 8px', fontSize: 11,
          }}
        >{loading ? '…' : '↻ Refresh'}</button>
      </div>

      {error && (
        <div style={{ background: '#1c0a0a', padding: 8, fontSize: 11, color: '#f87171', margin: 8, borderRadius: 6 }}>
          {error} — make sure the backend is running
        </div>
      )}

      {/* ── Summary cards ─────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, padding: 10 }}>
        {[
          { label: 'Sessions',      value: totalRef.current || sessions.length, color: '#38bdf8' },
          { label: 'Success rate',  value: successRate != null ? `${successRate}%` : '—', color: '#22c55e' },
          { label: 'Avg quality',   value: avgOverall != null ? `${avgOverall.toFixed(2)}/5` : '—', color: '#a855f7' },
          { label: 'Scored',        value: `${scoredCount}/${sessions.length}`, color: '#f59e0b' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{
            background: PNL, border: `1px solid ${BRD}`, borderRadius: 8, padding: '8px 10px',
          }}>
            <div style={{ fontSize: 10, color: DIM, letterSpacing: '0.06em' }}>{label.toUpperCase()}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color, marginTop: 2 }}>{value}</div>
          </div>
        ))}
      </div>

      {/* ── Extended metrics ───────────────────────────────────────────────── */}
      {Object.keys(extended).length > 0 && (
        <div style={{ padding: '0 10px 10px' }}>
          <div style={{ fontSize: 10, color: DIM, letterSpacing: '0.06em', marginBottom: 6 }}>
            EXTENDED METRICS
          </div>
          <div style={{ background: PNL, border: `1px solid ${BRD}`, borderRadius: 8, padding: 10 }}>
            {[
              ['EOS sessions',        `${extended.eos_sessions_pct?.toFixed(1) ?? '—'}%`],
              ['First-solve rate',    `${extended.first_solve_success_rate?.toFixed(1) ?? '—'}%`],
              ['Avg tools/success',   extended.avg_tools_per_success?.toFixed(1) ?? '—'],
              ['Avg SF violations',   extended.avg_sf_violations?.toFixed(2) ?? '—'],
              ['Precond violations',  extended.precondition_violations ?? '—'],
            ].map(([k, v]) => (
              <div key={String(k)} style={{
                display: 'flex', justifyContent: 'space-between',
                borderBottom: `1px solid ${BRD}`, padding: '4px 0', fontSize: 11,
              }}>
                <span style={{ color: DIM }}>{k}</span>
                <span style={{ fontWeight: 600, color: TXT }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Trend sparkline ────────────────────────────────────────────────── */}
      {overallTrend.length >= 3 && (
        <div style={{ padding: '0 10px 10px' }}>
          <div style={{ fontSize: 10, color: DIM, letterSpacing: '0.06em', marginBottom: 6 }}>
            OVERALL QUALITY TREND (last {overallTrend.length} scored)
          </div>
          <div style={{ background: PNL, border: `1px solid ${BRD}`, borderRadius: 8, padding: '8px 12px' }}>
            <Sparkline values={overallTrend} color="#a855f7" dark={dark} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: DIM, marginTop: 2 }}>
              <span>oldest</span>
              <span>newest</span>
            </div>
          </div>
        </div>
      )}

      {/* ── Criteria breakdown (last 10 scored sessions avg) ───────────────── */}
      {sessions.some(s => s.judge_scores?.overall != null) && (() => {
        const scored = sessions.filter(s => s.judge_scores?.overall != null).slice(0, 10);
        const avg = (key: keyof JudgeScores) => {
          const vals = scored.map(s => s.judge_scores![key] ?? 0).filter(v => v > 0);
          return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : undefined;
        };
        return (
          <div style={{ padding: '0 10px 10px' }}>
            <div style={{ fontSize: 10, color: DIM, letterSpacing: '0.06em', marginBottom: 6 }}>
              CRITERIA AVERAGES (last {scored.length} scored)
            </div>
            <div style={{ background: PNL, border: `1px solid ${BRD}`, borderRadius: 8, padding: 10 }}>
              {([
                ['property_package_correctness', 'EOS / Property Package', '#38bdf8'],
                ['physical_plausibility',        'Physical Plausibility',  '#22c55e'],
                ['completeness',                 'Completeness',           '#a855f7'],
                ['hallucination_absence',        'No Hallucinations',      '#f59e0b'],
              ] as [keyof JudgeScores, string, string][]).map(([key, label, color]) => {
                const val = avg(key);
                const pct = val != null ? Math.round((val / 5) * 100) : 0;
                return (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 10, width: 150, color: DIM }}>{label}</span>
                    <div style={{ flex: 1, height: 5, borderRadius: 3, background: BG }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
                    </div>
                    <span style={{ fontSize: 10, color, fontWeight: 700, width: 28, textAlign: 'right' }}>
                      {val?.toFixed(2) ?? '—'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* ── Filters ────────────────────────────────────────────────────────── */}
      <div style={{ padding: '0 10px 6px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 10, color: DIM }}>Min score:</span>
        {[0, 2, 3, 4].map(v => (
          <button key={v} onClick={() => { setFilterMin(v); setPage(0); }} style={{
            background: filterMin === v ? '#a855f7' : 'none',
            color:      filterMin === v ? '#fff' : DIM,
            border: `1px solid ${filterMin === v ? '#a855f7' : BRD}`,
            borderRadius: 4, padding: '2px 6px', cursor: 'pointer', fontSize: 10,
          }}>{v === 0 ? 'all' : `≥${v}`}</button>
        ))}
      </div>

      {/* ── Session table ──────────────────────────────────────────────────── */}
      <div style={{ padding: '0 10px 10px' }}>
        <div style={{ fontSize: 10, color: DIM, letterSpacing: '0.06em', marginBottom: 6 }}>
          SESSIONS
        </div>
        {loading && sessions.length === 0 && (
          <div style={{ color: DIM, textAlign: 'center', padding: 16 }}>Loading sessions…</div>
        )}
        {!loading && sessions.length === 0 && (
          <div style={{ color: DIM, textAlign: 'center', padding: 16, fontSize: 11 }}>
            No sessions recorded yet. Start chatting with the agent!
          </div>
        )}
        {sessions.map(s => (
          <div
            key={s.session_id}
            onClick={() => setSelected(s.session_id)}
            style={{
              background: PNL, border: `1px solid ${BRD}`,
              borderRadius: 8, padding: '8px 10px', marginBottom: 6,
              cursor: 'pointer',
              borderColor: selected === s.session_id ? '#a855f7' : BRD,
              transition: 'border-color 0.15s',
            }}
          >
            {/* Row 1: query preview + overall badge */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 6 }}>
              <span style={{ fontSize: 11, color: TXT, lineHeight: 1.4, flex: 1,
                             overflow: 'hidden', display: '-webkit-box',
                             WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' } as any}>
                {s.user_message}
              </span>
              <OverallBadge score={s.judge_scores?.overall} />
            </div>

            {/* Row 2: meta + mini scores */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, color: s.success ? '#22c55e' : '#ef4444' }}>
                {s.success ? '✔' : '✗'}
              </span>
              <span style={{ fontSize: 10, color: DIM }}>⏱ {s.duration_s?.toFixed(0) ?? '?'}s</span>
              <span style={{ fontSize: 10, color: DIM }}>🔧 {s.tool_count}</span>
              {s.failed_tools > 0 && (
                <span style={{ fontSize: 10, color: '#f87171' }}>✗{s.failed_tools}</span>
              )}
              <span style={{ fontSize: 10, color: DIM, marginLeft: 'auto' }}>
                {s.timestamp_iso?.slice(0, 16).replace('T', ' ')}
              </span>
            </div>

            {/* Row 3: criteria bars (compact) */}
            {s.judge_scores?.overall != null && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 10px', marginTop: 6 }}>
                {([
                  ['property_package_correctness', 'EOS',   '#38bdf8'],
                  ['physical_plausibility',        'Phys',  '#22c55e'],
                  ['completeness',                 'Comp',  '#a855f7'],
                  ['hallucination_absence',        'NoHall','#f59e0b'],
                ] as [keyof JudgeScores, string, string][]).map(([key, label, color]) => (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <span style={{ fontSize: 9, color: DIM, width: 34 }}>{label}</span>
                    <ScoreBar score={s.judge_scores![key]} dark={dark} />
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Pagination ─────────────────────────────────────────────────────── */}
      {totalRef.current > PER_PAGE && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 8, padding: '0 10px 16px' }}>
          <button
            disabled={page === 0} onClick={() => setPage(p => p - 1)}
            style={{ background: 'none', border: `1px solid ${BRD}`, borderRadius: 5, color: DIM, padding: '2px 10px', cursor: 'pointer', fontSize: 11 }}
          >← Prev</button>
          <span style={{ color: DIM, fontSize: 11, alignSelf: 'center' }}>
            {page + 1} / {Math.ceil(totalRef.current / PER_PAGE)}
          </span>
          <button
            disabled={(page + 1) * PER_PAGE >= totalRef.current}
            onClick={() => setPage(p => p + 1)}
            style={{ background: 'none', border: `1px solid ${BRD}`, borderRadius: 5, color: DIM, padding: '2px 10px', cursor: 'pointer', fontSize: 11 }}
          >Next →</button>
        </div>
      )}

      {/* ── Detail modal ───────────────────────────────────────────────────── */}
      {selected && (
        <SessionDetailModal sessionId={selected} dark={dark} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
