import React, { useState, useCallback } from 'react';
import { api } from '../utils/api';

interface CompRow {
  stream:  string;
  [alias: string]: any;
}

interface CompResult {
  success:    boolean;
  aliases:    string[];
  comparison: CompRow[];
  error?:     string;
}

interface Props { dark?: boolean; }

const PROPS = ['temperature_C', 'pressure_bar', 'mass_flow_kgh', 'vapor_fraction'];
const PROP_LABELS: Record<string, string> = {
  temperature_C:  'T (°C)',
  pressure_bar:   'P (bar)',
  mass_flow_kgh:  'Flow (kg/h)',
  vapor_fraction: 'VF',
};

function delta(v: number | null, base: number | null): string {
  if (v == null || base == null) return '—';
  const d = v - base;
  const sign = d >= 0 ? '+' : '';
  return `${sign}${d.toFixed(3)}`;
}

function deltaColor(v: number | null, base: number | null, dark: boolean): string {
  if (v == null || base == null) return dark ? '#64748b' : '#94a3b8';
  const d = v - base;
  if (Math.abs(d) < 0.001) return dark ? '#64748b' : '#94a3b8';
  return d > 0 ? '#34d399' : '#f87171';
}

export default function FlowsheetComparison({ dark = true }: Props) {
  const [result,  setResult]  = useState<CompResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState('');

  const BG   = dark ? '#0f172a' : '#f8fafc';
  const PNL  = dark ? '#1e293b' : '#fff';
  const BRD  = dark ? '#334155' : '#e2e8f0';
  const DIM  = dark ? '#64748b' : '#94a3b8';
  const TXT  = dark ? '#e2e8f0' : '#1e293b';
  const HDR  = dark ? '#0f172a' : '#f1f5f9';

  const run = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const r: any = await api.compareFlowsheets();
      if (!r.success) setError(r.error || 'Comparison failed');
      else setResult(r);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  const aliases   = result?.aliases ?? [];
  const baseAlias = aliases[0];

  const th: React.CSSProperties = {
    padding:'5px 8px', fontSize:10, fontWeight:700, color:DIM,
    background:HDR, borderBottom:`1px solid ${BRD}`,
    textAlign:'left', whiteSpace:'nowrap',
  };
  const td: React.CSSProperties = {
    padding:'4px 8px', fontSize:11, color:TXT, fontFamily:'monospace',
    borderBottom:`1px solid ${dark ? '#1e293b' : '#f1f5f9'}`,
  };

  return (
    <div style={{ background:BG, height:'100%', overflowY:'auto', padding:12 }}>
      <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:12, flexWrap:'wrap' }}>
        <span style={{ fontSize:13, fontWeight:700, color:'#38bdf8' }}>⚖ Flowsheet Comparison</span>
        <button onClick={run} disabled={loading} style={{
          background: loading ? '#334155' : '#0ea5e9', color:'#fff',
          border:'none', borderRadius:6, padding:'5px 14px',
          cursor: loading ? 'not-allowed' : 'pointer', fontWeight:600, fontSize:12,
        }}>
          {loading ? 'Comparing…' : 'Compare Loaded Sheets'}
        </button>
      </div>

      <div style={{ fontSize:11, color:DIM, marginBottom:10 }}>
        Load 2+ flowsheets with different aliases (e.g. "PR" and "NRTL") then click Compare.
        <br />Use: <code style={{ background:PNL, padding:'1px 4px', borderRadius:3 }}>load_flowsheet path alias="PR"</code>
      </div>

      {error && (
        <div style={{ background:'#3b1f1f', border:`1px solid #7f1d1d`, borderRadius:6,
                      padding:'8px 12px', color:'#f87171', fontSize:12, marginBottom:10 }}>
          ⚠ {error}
          {error.includes('2 flowsheets') && (
            <div style={{ marginTop:6, color:'#fca5a5', fontSize:11 }}>
              Ask the agent: "Load flowsheet X with alias PR, then load Y with alias NRTL"
            </div>
          )}
        </div>
      )}

      {!result && !loading && !error && (
        <div style={{ textAlign:'center', color:DIM, paddingTop:40, fontSize:12 }}>
          <div style={{ fontSize:32, marginBottom:8 }}>📊</div>
          No comparison data yet.
        </div>
      )}

      {result && (
        <>
          {/* Legend */}
          <div style={{ display:'flex', gap:8, marginBottom:10, flexWrap:'wrap' }}>
            {aliases.map((a, i) => (
              <span key={a} style={{ background: i===0 ? '#0c4a6e' : '#14532d',
                                      color: i===0 ? '#7dd3fc' : '#86efac',
                                      borderRadius:4, padding:'2px 8px', fontSize:11, fontWeight:600 }}>
                {i===0 ? '📌 ' : '🔄 '}{a}{i===0 ? ' (base)' : ` vs ${baseAlias}`}
              </span>
            ))}
          </div>

          {/* Comparison table */}
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Stream</th>
                  <th style={th}>Property</th>
                  {aliases.map(a => (
                    <th key={a} style={th}>{a}</th>
                  ))}
                  {aliases.slice(1).map(a => (
                    <th key={`d_${a}`} style={{ ...th, color:'#fbbf24' }}>Δ ({a}−{baseAlias})</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.comparison.map(row =>
                  PROPS.map((prop, pi) => {
                    const baseVal = row[baseAlias]?.[prop];
                    return (
                      <tr key={`${row.stream}_${prop}`}>
                        {pi === 0 && (
                          <td rowSpan={PROPS.length} style={{
                            ...td, fontWeight:700, color:'#38bdf8',
                            borderRight:`1px solid ${BRD}`, fontFamily:'inherit',
                            verticalAlign:'top', paddingTop:8,
                          }}>
                            {row.stream}
                          </td>
                        )}
                        <td style={{ ...td, color:DIM, fontFamily:'inherit', fontWeight:600, fontSize:10 }}>
                          {PROP_LABELS[prop] ?? prop}
                        </td>
                        {aliases.map(a => (
                          <td key={a} style={td}>
                            {row[a]?.[prop] != null ? Number(row[a][prop]).toFixed(4) : '—'}
                          </td>
                        ))}
                        {aliases.slice(1).map(a => {
                          const v = row[a]?.[prop];
                          const color = deltaColor(v, baseVal, dark);
                          return (
                            <td key={`d_${a}`} style={{ ...td, color }}>
                              {delta(v, baseVal)}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Summary: biggest differences */}
          {result.comparison.length > 0 && (
            <div style={{ marginTop:12, background:PNL, border:`1px solid ${BRD}`,
                          borderRadius:6, padding:'10px 12px' }}>
              <div style={{ fontSize:11, fontWeight:700, color:TXT, marginBottom:6 }}>
                📋 Largest Differences ({aliases[1]} vs {baseAlias})
              </div>
              {(() => {
                const diffs: Array<{ stream: string; prop: string; delta: number }> = [];
                result.comparison.forEach(row => {
                  PROPS.forEach(prop => {
                    const b = row[baseAlias]?.[prop];
                    const v = row[aliases[1]]?.[prop];
                    if (b != null && v != null) {
                      diffs.push({ stream: row.stream, prop: PROP_LABELS[prop] ?? prop, delta: Math.abs(v - b) });
                    }
                  });
                });
                return diffs
                  .sort((a, b) => b.delta - a.delta)
                  .slice(0, 5)
                  .map(d => (
                    <div key={`${d.stream}_${d.prop}`} style={{ display:'flex', justifyContent:'space-between',
                                                                  fontSize:11, padding:'2px 0', color:DIM }}>
                      <span>{d.stream} · {d.prop}</span>
                      <span style={{ color: d.delta > 1 ? '#f87171' : '#86efac', fontFamily:'monospace' }}>
                        |Δ| = {d.delta.toFixed(4)}
                      </span>
                    </div>
                  ));
              })()}
            </div>
          )}
        </>
      )}
    </div>
  );
}
