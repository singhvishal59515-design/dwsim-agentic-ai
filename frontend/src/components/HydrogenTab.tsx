import React, { useState, useCallback, useEffect } from 'react';
import { api } from '../utils/api';

interface SimPoint {
  reformer_temp_C:   number;
  pressure_bar:      number;
  biogas_flow_kgh:   number;
  steam_flow_kgh:    number;
  h2_mole_fraction:  number | null;
  h2_yield_mol_pct:  number | null;
  converged:         boolean;
  duration_s:        number;
  error?:            string | null;
  mock:              boolean;
}

interface Comparison {
  paper_h2_pct?:        number;
  sim_h2_pct?:          number;
  absolute_error?:      number;
  relative_error_pct?:  number;
  within_5pct?:         boolean;
  within_10pct?:        boolean;
  status:               string;
}

interface HydrogenReport {
  timestamp:        string;
  template:         string;
  mode:             string;
  mock_mode:        boolean;
  comparison:       Comparison;
  trends_verified:  Record<string, boolean>;
  summary:          string;
  paper_reference:  any;
  base_case:        SimPoint | null;
  optimal_case:     SimPoint | null;
  sensitivity:      Record<string, SimPoint[]>;
}

// Paper defaults per parameter for the range pickers
const PARAM_DEFAULTS: Record<string, { min: number; max: number; step: number; unit: string }> = {
  temperature: { min: 700, max: 1000, step: 50,  unit: '°C'   },
  pressure:    { min: 8,   max: 24,   step: 2,   unit: 'bar'  },
  biogas_flow: { min: 20,  max: 80,   step: 10,  unit: 'kg/h' },
  steam_flow:  { min: 20,  max: 70,   step: 10,  unit: 'kg/h' },
};

interface Props { dark: boolean; }

export default function HydrogenTab({ dark }: Props) {
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';
  const sub  = dark ? '#0f172a' : '#f1f5f9';

  // Build
  const [template,     setTemplate]     = useState('biogas_smr_h2_gibbs');
  const [mock,         setMock]         = useState(false);
  const [buildStatus,  setBuildStatus]  = useState<'idle' | 'building' | 'done' | 'failed'>('idle');

  // Run
  const [mode,    setMode]    = useState('quick');
  const [running, setRunning] = useState(false);
  const [report,  setReport]  = useState<HydrogenReport | null>(null);

  // Sensitivity
  const [sensParam,   setSensParam]   = useState('temperature');
  const [sensMin,     setSensMin]     = useState(700);
  const [sensMax,     setSensMax]     = useState(1000);
  const [sensStep,    setSensStep]    = useState(50);
  const [sensRunning, setSensRunning] = useState(false);
  const [sensResults, setSensResults] = useState<SimPoint[] | null>(null);

  const [error, setError] = useState<string | null>(null);

  // Keep range defaults in sync with parameter selection
  useEffect(() => {
    const d = PARAM_DEFAULTS[sensParam];
    if (d) { setSensMin(d.min); setSensMax(d.max); setSensStep(d.step); }
  }, [sensParam]);

  const handleBuild = useCallback(async () => {
    setBuildStatus('building'); setError(null);
    try {
      const r: any = await (api as any).hydrogenBuild(template, mock);
      if (r?.success) setBuildStatus('done');
      else { setBuildStatus('failed'); setError(r?.error || 'Build failed'); }
    } catch (e: any) {
      setBuildStatus('failed');
      setError(String(e?.message || e));
    }
  }, [template, mock]);

  const handleRun = useCallback(async () => {
    setRunning(true); setError(null);
    try {
      const r: any = await (api as any).hydrogenRun(mode, mock, template);
      if (r?.success) setReport(r.report as HydrogenReport);
      else setError(r?.error || 'Run failed');
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally { setRunning(false); }
  }, [mode, mock, template]);

  const handleSensitivity = useCallback(async () => {
    setSensRunning(true); setSensResults(null); setError(null);
    const values: number[] = [];
    for (let v = sensMin; v <= sensMax + 0.001; v += sensStep) {
      values.push(Math.round(v * 1000) / 1000);
    }
    if (values.length < 2 || values.length > 60) {
      setError('Sensitivity range produces < 2 or > 60 points — adjust min/max/step');
      setSensRunning(false);
      return;
    }
    try {
      const r: any = await (api as any).hydrogenSensitivity(sensParam, values, mock);
      if (r?.success) setSensResults(r.results as SimPoint[]);
      else setError(r?.error || 'Sensitivity run failed');
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally { setSensRunning(false); }
  }, [sensParam, sensMin, sensMax, sensStep, mock]);

  const handleLoadReport = useCallback(async () => {
    setError(null);
    try {
      const r: any = await (api as any).hydrogenReport();
      if (r?.success) setReport(r.report as HydrogenReport);
      else setError('No saved report found. Run a case study first.');
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }, []);

  const cmp = report?.comparison;
  const passColor  = (v: boolean | undefined) => v ? '#86efac' : '#fca5a5';
  const statusBg   = (s: string) => s === 'PASS' ? '#16a34a' : s === 'NO_DATA' ? '#475569' : '#dc2626';

  // Mini sparkline SVG from sensitivity results
  function Sparkline({ pts }: { pts: SimPoint[] }) {
    const vals = pts.map(p => p.h2_yield_mol_pct ?? 0);
    if (vals.length < 2) return null;
    const mn  = Math.min(...vals);
    const mx  = Math.max(...vals) || 1;
    const W = 320, H = 80, pad = 8;
    const x = (i: number) => pad + (i / (vals.length - 1)) * (W - 2 * pad);
    const y = (v: number) => H - pad - ((v - mn) / (mx - mn + 0.0001)) * (H - 2 * pad);
    const d = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    // paper optimal ref line (h2_yield = 64.87)
    const paperY = y(64.87);
    const inRange = mn <= 64.87 && 64.87 <= mx;
    return (
      <svg width={W} height={H} style={{ display: 'block', margin: '6px auto' }}>
        {inRange && (
          <line x1={pad} y1={paperY} x2={W - pad} y2={paperY}
            stroke="#f59e0b" strokeWidth={1} strokeDasharray="4,3" />
        )}
        <path d={d} fill="none" stroke="#38bdf8" strokeWidth={1.8} />
        {vals.map((v, i) => (
          <circle key={i} cx={x(i)} cy={y(v)} r={3}
            fill={v === Math.max(...vals) ? '#86efac' : '#38bdf8'} />
        ))}
        <text x={pad + 2} y={12} fontSize={9} fill={dim}>H₂ {mn.toFixed(1)}%</text>
        <text x={W - pad - 2} y={12} fontSize={9} fill={dim} textAnchor="end">max {mx.toFixed(1)}%</text>
        {inRange && (
          <text x={W - pad - 2} y={paperY - 3} fontSize={8} fill="#f59e0b" textAnchor="end">paper 64.87%</text>
        )}
      </svg>
    );
  }

  const paramUnit = PARAM_DEFAULTS[sensParam]?.unit || '';
  const sensXLabel = sensResults
    ? sensResults.map(p => {
        if (sensParam === 'temperature')  return p.reformer_temp_C;
        if (sensParam === 'pressure')     return p.pressure_bar;
        if (sensParam === 'biogas_flow')  return p.biogas_flow_kgh;
        return p.steam_flow_kgh;
      })
    : [];

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: bg, padding: '8px 10px' }}>

      {/* Header */}
      <div style={{ fontSize: 11, color: dim, marginBottom: 6, letterSpacing: 0.6, fontWeight: 700 }}>
        H₂ CASE STUDY — Ullah et al. (2025)
      </div>

      {/* Paper reference card */}
      <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: '#38bdf8', marginBottom: 4 }}>
          📄 Paper Reference
        </div>
        <div style={{ fontSize: 10, color: dim, lineHeight: 1.6 }}>
          <div>Ullah, Asaad &amp; Inayat (2025) · Digital Chem Eng 14, 100205</div>
          <div style={{ marginTop: 4 }}>
            <b style={{ color: txt }}>Baseline:</b>{' '}
            T=909°C · P=16 bar · biogas=38.5 kg/h · steam=46 kg/h
          </div>
          <div>
            <b style={{ color: txt }}>Optimal:</b>{' '}
            T=954°C · P=12.5 bar · biogas=57 kg/h · steam=33.97 kg/h →{' '}
            <b style={{ color: '#86efac' }}>H₂ yield 64.87%</b>
          </div>
        </div>
      </div>

      {/* Build section */}
      <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: dim, marginBottom: 6, fontWeight: 700 }}>STEP 1 — BUILD FLOWSHEET</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <select value={template} onChange={e => setTemplate(e.target.value)}
            style={selStyle(card, brd, txt)}>
            <option value="biogas_smr_h2_gibbs">biogas_smr_h2_gibbs (Gibbs)</option>
            <option value="biogas_smr_h2">biogas_smr_h2 (Equil.)</option>
          </select>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <label style={{ fontSize: 10, color: dim, display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={mock} onChange={e => setMock(e.target.checked)} />
            Mock mode (no DWSIM)
          </label>
          <span style={{ marginLeft: 'auto', fontSize: 10, color: dim }}>
            {buildStatus === 'done'    && <span style={{ color: '#86efac' }}>✓ Built</span>}
            {buildStatus === 'failed'  && <span style={{ color: '#fca5a5' }}>✗ Failed</span>}
            {buildStatus === 'building'&& <span style={{ color: '#fbbf24' }}>Building…</span>}
          </span>
        </div>
        <button onClick={handleBuild} disabled={buildStatus === 'building'}
          style={btnStyle(buildStatus === 'building' ? '#475569' : '#0ea5e9')}>
          {buildStatus === 'building' ? '⏳ Building…' : '🔨 Build Flowsheet'}
        </button>
      </div>

      {/* Run case study section */}
      <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: dim, marginBottom: 6, fontWeight: 700 }}>STEP 2 — RUN CASE STUDY</div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 10, color: dim }}>Mode:</span>
          {(['base', 'quick', 'full'] as const).map(m => (
            <button key={m} onClick={() => setMode(m)}
              style={{
                background: mode === m ? '#0ea5e9' : sub,
                color: mode === m ? '#fff' : dim,
                border: `1px solid ${brd}`, borderRadius: 5,
                padding: '2px 8px', fontSize: 10, cursor: 'pointer',
              }}>{m}</button>
          ))}
          <span style={{ fontSize: 9, color: dim, marginLeft: 4 }}>
            {mode === 'base' && 'base + optimal only'}
            {mode === 'quick' && '≈ 29 pts, ~20 min'}
            {mode === 'full'  && '≈ 72 pts, ~60 min'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={handleRun} disabled={running}
            style={btnStyle(running ? '#475569' : '#16a34a')}>
            {running ? '⏳ Running…' : '▶ Run Case Study'}
          </button>
          <button onClick={handleLoadReport}
            style={btnStyle('#7c3aed')}>
            📂 Load Saved
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div style={{ background: '#1c0a0a', color: '#f87171', padding: '6px 10px',
                      borderRadius: 6, fontSize: 11, marginBottom: 8 }}>
          ✕ {error}
        </div>
      )}

      {/* Comparison results */}
      {report && (
        <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <span style={{ fontWeight: 700, color: txt, fontSize: 12 }}>Results</span>
            {report.mock_mode && (
              <span style={{ fontSize: 9, background: '#7c3aed', color: '#fff', padding: '1px 6px', borderRadius: 8 }}>MOCK</span>
            )}
            {cmp && cmp.status !== 'NO_DATA' && (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                              background: statusBg(cmp.status), color: '#fff' }}>
                {cmp.status}
              </span>
            )}
          </div>

          {/* Quantitative comparison */}
          {cmp && cmp.status !== 'NO_DATA' && (
            <div style={{ background: sub, borderRadius: 6, padding: '6px 8px', marginBottom: 8 }}>
              <div style={{ fontSize: 10, color: dim, marginBottom: 4, fontWeight: 700 }}>QUANTITATIVE (Optimal Case)</div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <Metric label="Sim H₂"    value={`${cmp.sim_h2_pct?.toFixed(2) ?? '—'}%`} color={txt} />
                <Metric label="Paper H₂"  value={`${cmp.paper_h2_pct?.toFixed(2) ?? '—'}%`} color={dim} />
                <Metric label="Abs error" value={`${cmp.absolute_error?.toFixed(3) ?? '—'}%`} color={txt} />
                <Metric label="Rel error" value={`${cmp.relative_error_pct?.toFixed(2) ?? '—'}%`}
                  color={cmp.within_5pct ? '#86efac' : cmp.within_10pct ? '#fbbf24' : '#fca5a5'} />
                <Metric label="< 5%"  value={cmp.within_5pct  ? '✓' : '✗'} color={passColor(cmp.within_5pct)}  />
                <Metric label="< 10%" value={cmp.within_10pct ? '✓' : '✗'} color={passColor(cmp.within_10pct)} />
              </div>
            </div>
          )}

          {/* Base / optimal cases */}
          {(report.base_case || report.optimal_case) && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 10, color: dim, fontWeight: 700, marginBottom: 4 }}>SIMULATION POINTS</div>
              <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: dim }}>
                    <th style={thStyle}>Case</th>
                    <th style={thStyle}>T (°C)</th>
                    <th style={thStyle}>P (bar)</th>
                    <th style={thStyle}>H₂ %</th>
                    <th style={thStyle}>Conv.</th>
                    <th style={thStyle}>t (s)</th>
                  </tr>
                </thead>
                <tbody>
                  {[['Base', report.base_case], ['Optimal', report.optimal_case]].map(([label, pt]) => {
                    const p = pt as SimPoint | null;
                    if (!p) return null;
                    return (
                      <tr key={label as string} style={{ borderTop: `1px solid ${brd}` }}>
                        <td style={{ padding: '3px 4px', color: txt }}>{label as string}</td>
                        <td style={{ padding: '3px 4px', color: txt, textAlign: 'right' }}>{p.reformer_temp_C}</td>
                        <td style={{ padding: '3px 4px', color: txt, textAlign: 'right' }}>{p.pressure_bar}</td>
                        <td style={{ padding: '3px 4px', textAlign: 'right',
                                      color: p.h2_yield_mol_pct !== null ? '#86efac' : '#fca5a5' }}>
                          {p.h2_yield_mol_pct !== null ? `${p.h2_yield_mol_pct.toFixed(2)}%` : 'N/A'}
                        </td>
                        <td style={{ padding: '3px 4px', textAlign: 'center',
                                      color: p.converged ? '#86efac' : '#fca5a5' }}>
                          {p.converged ? '✓' : '✗'}
                        </td>
                        <td style={{ padding: '3px 4px', color: dim, textAlign: 'right' }}>{p.duration_s.toFixed(1)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Trend verification */}
          {Object.keys(report.trends_verified).length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 10, color: dim, fontWeight: 700, marginBottom: 4 }}>QUALITATIVE TRENDS</div>
              {Object.entries(report.trends_verified).map(([k, v]) => (
                <div key={k} style={{ fontSize: 10, color: v ? '#86efac' : '#fca5a5', marginBottom: 2 }}>
                  {v ? '✓' : '✗'} {k.replace(/_/g, ' ')}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Sensitivity section */}
      <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: dim, marginBottom: 6, fontWeight: 700 }}>SENSITIVITY ANALYSIS</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
          <select value={sensParam} onChange={e => setSensParam(e.target.value)}
            style={{ ...selStyle(card, brd, txt), flex: '1 1 auto' }}>
            <option value="temperature">Temperature (°C)</option>
            <option value="pressure">Pressure (bar)</option>
            <option value="biogas_flow">Biogas flow (kg/h)</option>
            <option value="steam_flow">Steam flow (kg/h)</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 10, color: dim }}>
            Min
            <input type="number" value={sensMin} onChange={e => setSensMin(Number(e.target.value))}
              style={{ ...numStyle(card, brd, txt), marginLeft: 4, width: 60 }} />
          </label>
          <label style={{ fontSize: 10, color: dim }}>
            Max
            <input type="number" value={sensMax} onChange={e => setSensMax(Number(e.target.value))}
              style={{ ...numStyle(card, brd, txt), marginLeft: 4, width: 60 }} />
          </label>
          <label style={{ fontSize: 10, color: dim }}>
            Step
            <input type="number" value={sensStep} onChange={e => setSensStep(Number(e.target.value))}
              style={{ ...numStyle(card, brd, txt), marginLeft: 4, width: 50 }} />
          </label>
          <span style={{ fontSize: 10, color: dim }}>{paramUnit}</span>
          <span style={{ marginLeft: 'auto', fontSize: 9, color: dim }}>
            {Math.max(0, Math.floor((sensMax - sensMin) / sensStep) + 1)} pts
          </span>
        </div>
        <button onClick={handleSensitivity} disabled={sensRunning}
          style={btnStyle(sensRunning ? '#475569' : '#7c3aed')}>
          {sensRunning ? '⏳ Running sensitivity…' : '📊 Run Sensitivity'}
        </button>

        {/* Sensitivity results */}
        {sensResults && sensResults.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <Sparkline pts={sensResults} />
            <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse', marginTop: 4 }}>
              <thead>
                <tr style={{ color: dim }}>
                  <th style={thStyle}>{sensParam.replace('_', ' ')} ({paramUnit})</th>
                  <th style={thStyle}>H₂ %</th>
                  <th style={thStyle}>Conv.</th>
                  <th style={thStyle}>t (s)</th>
                </tr>
              </thead>
              <tbody>
                {sensResults.map((p, i) => {
                  const xv = sensXLabel[i] ?? 0;
                  const maxH2 = Math.max(...sensResults.map(r => r.h2_yield_mol_pct ?? 0));
                  const isMax = p.h2_yield_mol_pct === maxH2 && maxH2 > 0;
                  return (
                    <tr key={i} style={{ borderTop: `1px solid ${brd}`,
                                          background: isMax ? (dark ? '#052e16' : '#f0fdf4') : 'transparent' }}>
                      <td style={{ padding: '2px 4px', color: txt }}>{xv}</td>
                      <td style={{ padding: '2px 4px', textAlign: 'right',
                                    color: p.h2_yield_mol_pct !== null
                                      ? (isMax ? '#86efac' : txt)
                                      : '#fca5a5' }}>
                        {p.h2_yield_mol_pct !== null ? `${p.h2_yield_mol_pct.toFixed(2)}%` : 'N/A'}
                      </td>
                      <td style={{ padding: '2px 4px', textAlign: 'center',
                                    color: p.converged ? '#86efac' : '#fca5a5' }}>
                        {p.converged ? '✓' : '✗'}
                      </td>
                      <td style={{ padding: '2px 4px', color: dim, textAlign: 'right' }}>
                        {p.duration_s.toFixed(1)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Export */}
      {(report || sensResults) && (
        <div style={{ marginBottom: 8 }}>
          <button
            onClick={() => {
              const data = { report, sensitivity_standalone: sensResults };
              const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `h2_case_study_${Date.now()}.json`;
              a.click();
              URL.revokeObjectURL(a.href);
            }}
            style={btnStyle('#334155')}>
            ⬇ Export JSON
          </button>
        </div>
      )}
    </div>
  );
}

// ── helpers ───────────────────────────────────────────────────────────────────

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <span style={{ fontSize: 9, color: '#64748b', marginBottom: 1 }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 700, color }}>{value}</span>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: '2px 4px', textAlign: 'right', fontWeight: 600,
};

function btnStyle(bg: string): React.CSSProperties {
  return {
    background: bg, color: '#fff', border: 'none', borderRadius: 6,
    padding: '5px 12px', fontSize: 11, fontWeight: 600, cursor: 'pointer',
  };
}

function selStyle(card: string, brd: string, txt: string): React.CSSProperties {
  return {
    background: card, color: txt, border: `1px solid ${brd}`,
    borderRadius: 5, padding: '3px 6px', fontSize: 11,
  };
}

function numStyle(card: string, brd: string, txt: string): React.CSSProperties {
  return {
    background: card, color: txt, border: `1px solid ${brd}`,
    borderRadius: 5, padding: '2px 4px', fontSize: 11,
  };
}
