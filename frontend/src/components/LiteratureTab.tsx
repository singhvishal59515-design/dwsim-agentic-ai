import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../utils/api';
import {
  ProcessLibraryEntry, LiteratureCompareResult, LiteratureStreamCmp, LiteratureRow,
} from '../types';

interface Props { dark: boolean; }

export default function LiteratureTab({ dark }: Props) {
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';
  const sub  = dark ? '#0f172a' : '#f1f5f9';

  const [processes,  setProcesses]  = useState<ProcessLibraryEntry[]>([]);
  const [selected,   setSelected]   = useState<string>('');
  const [tolerance,  setTolerance]  = useState<number>(5);
  const [result,     setResult]     = useState<LiteratureCompareResult | null>(null);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [detail,     setDetail]     = useState<any | null>(null);

  useEffect(() => {
    api.processLibraryList()
      .then((d: any) => {
        const list: ProcessLibraryEntry[] = d?.processes || [];
        setProcesses(list);
        if (list.length > 0 && !selected) setSelected(list[0].key);
      })
      .catch((e: any) => setError(String(e?.message || e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    api.processLibraryDetail(selected)
      .then(setDetail)
      .catch(() => setDetail(null));
  }, [selected]);

  const runCompare = useCallback(async () => {
    if (!selected) return;
    setLoading(true); setError(null);
    try {
      const r = await api.compareToLiterature(selected, tolerance, true);
      if (r?.success) setResult(r as LiteratureCompareResult);
      else setError(r?.error || 'Comparison failed.');
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [selected, tolerance]);

  const sel = processes.find(p => p.key === selected);

  return (
    <div style={{height:'100%', overflow:'auto', background:bg, padding:'8px 10px'}}>
      <div style={{fontSize:11, color:dim, marginBottom:6, letterSpacing:0.6, fontWeight:700}}>
        LITERATURE BENCHMARK
      </div>

      {/* Process picker */}
      <div style={{display:'flex', gap:6, marginBottom:8, flexWrap:'wrap'}}>
        <select value={selected} onChange={e => setSelected(e.target.value)}
          style={{background:card, color:txt, border:`1px solid ${brd}`, borderRadius:6,
                  padding:'4px 6px', fontSize:11, flex:'1 1 auto', minWidth:140}}>
          {processes.length === 0 && <option value="">(none — backend offline?)</option>}
          {processes.map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
        </select>
        <span style={{fontSize:10, color:dim, alignSelf:'center'}}>tol ±</span>
        <input type="number" min={1} max={50} value={tolerance}
          onChange={e => setTolerance(Number(e.target.value) || 5)}
          style={{width:46, background:card, color:txt, border:`1px solid ${brd}`,
                  borderRadius:6, padding:'4px 6px', fontSize:11}}/>
        <span style={{fontSize:10, color:dim, alignSelf:'center'}}>%</span>
        <button onClick={runCompare} disabled={loading || !selected}
          style={{background: loading ? '#475569' : '#0ea5e9', color:'#fff',
                  border:'none', borderRadius:6, padding:'4px 10px', fontSize:11,
                  fontWeight:600, cursor: loading ? 'wait' : 'pointer'}}>
          {loading ? 'Comparing…' : 'Run Compare'}
        </button>
      </div>

      {/* Reference metadata */}
      {sel && (
        <div style={{background:card, border:`1px solid ${brd}`, borderRadius:8, padding:'8px 10px', marginBottom:8}}>
          <div style={{fontWeight:600, color:txt, fontSize:12, marginBottom:4}}>{sel.name}</div>
          <div style={{color:dim, fontSize:10, lineHeight:1.5}}>
            <div><b>Source:</b> {sel.source || '—'}</div>
            {sel.doi && <div><b>DOI:</b> {sel.doi}</div>}
          </div>
          {detail?.meta?.base_case && (
            <div style={{marginTop:6, paddingTop:6, borderTop:`1px solid ${brd}`}}>
              <div style={{fontSize:10, color:dim, marginBottom:3}}>Base case (literature):</div>
              <div style={{display:'flex', flexWrap:'wrap', gap:4}}>
                {Object.entries(detail.meta.base_case).map(([k, v]) => (
                  <span key={k} style={{fontSize:10, background:sub, color:txt,
                                        padding:'2px 6px', borderRadius:4,
                                        border:`1px solid ${brd}`}}>
                    {k}: <b>{String(v)}</b>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div style={{background:'#1c0a0a', color:'#f87171', padding:'6px 10px',
                     borderRadius:6, fontSize:11, marginBottom:8}}>
          ✕ {error}
        </div>
      )}

      {/* Comparison results */}
      {result && (
        <div style={{background:card, border:`1px solid ${brd}`, borderRadius:8, padding:'8px 10px'}}>
          <div style={{display:'flex', alignItems:'center', gap:8, marginBottom:6}}>
            <span style={{fontWeight:700, color:txt, fontSize:13}}>Result</span>
            <span style={{
              fontSize:10, fontWeight:700, padding:'2px 8px', borderRadius:10,
              background: result.n_streams_passed === result.n_streams_compared ? '#16a34a' : '#dc2626',
              color:'#fff',
            }}>
              {result.n_streams_passed}/{result.n_streams_compared} STREAMS PASS
            </span>
          </div>
          <div style={{fontSize:11, color:dim, marginBottom:8}}>{result.summary}</div>

          {result.stream_comparisons.map((sc: LiteratureStreamCmp) => (
            <div key={sc.stream_tag} style={{marginBottom:10}}>
              <div style={{display:'flex', alignItems:'center', gap:6, marginBottom:3}}>
                <span style={{fontSize:12, fontWeight:600, color:txt}}>{sc.stream_tag}</span>
                <span style={{
                  fontSize:9, fontWeight:700, padding:'1px 6px', borderRadius:8,
                  background: sc.overall_match === 'PASS' ? '#16a34a'
                           : sc.overall_match === 'PARTIAL' ? '#f59e0b' : '#dc2626',
                  color:'#fff',
                }}>{sc.overall_match}</span>
                <span style={{fontSize:10, color:dim}}>
                  mean Δ {sc.mean_deviation_pct.toFixed(2)}%
                </span>
              </div>
              <table style={{width:'100%', fontSize:10, borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{color:dim, textAlign:'left'}}>
                    <th style={{padding:'2px 4px'}}>Property</th>
                    <th style={{padding:'2px 4px', textAlign:'right'}}>Sim</th>
                    <th style={{padding:'2px 4px', textAlign:'right'}}>Lit</th>
                    <th style={{padding:'2px 4px', textAlign:'right'}}>Δ%</th>
                    <th style={{padding:'2px 4px'}}>OK</th>
                  </tr>
                </thead>
                <tbody>
                  {sc.rows.map((row: LiteratureRow) => (
                    <tr key={row.property} style={{borderTop:`1px solid ${brd}`}}>
                      <td style={{padding:'2px 4px', color:txt}}>
                        {row.property}{row.unit ? ` (${row.unit})` : ''}
                      </td>
                      <td style={{padding:'2px 4px', textAlign:'right', color:txt}}>
                        {fmtNum(row.sim_value)}
                      </td>
                      <td style={{padding:'2px 4px', textAlign:'right', color:dim}}>
                        {fmtNum(row.ref_value)}
                      </td>
                      <td style={{padding:'2px 4px', textAlign:'right',
                                  color: row.status === 'PASS' ? '#86efac' : '#fca5a5'}}>
                        {row.deviation_pct.toFixed(2)}
                      </td>
                      <td style={{padding:'2px 4px',
                                  color: row.status === 'PASS' ? '#86efac' : '#fca5a5'}}>
                        {row.status === 'PASS' ? '✓' : '✗'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

          {result.kpi_comparison && (
            <div style={{marginTop:8, paddingTop:8, borderTop:`1px solid ${brd}`}}>
              <div style={{fontSize:11, fontWeight:700, color:txt, marginBottom:4}}>
                Process KPIs
              </div>
              <table style={{width:'100%', fontSize:10, borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{color:dim, textAlign:'left'}}>
                    <th style={{padding:'2px 4px'}}>KPI</th>
                    <th style={{padding:'2px 4px', textAlign:'right'}}>Sim</th>
                    <th style={{padding:'2px 4px', textAlign:'right'}}>Lit</th>
                    <th style={{padding:'2px 4px', textAlign:'right'}}>Δ%</th>
                    <th style={{padding:'2px 4px'}}>OK</th>
                  </tr>
                </thead>
                <tbody>
                  {(result.kpi_comparison.rows || []).map((row: any) => (
                    <tr key={row.kpi || row.property} style={{borderTop:`1px solid ${brd}`}}>
                      <td style={{padding:'2px 4px', color:txt}}>
                        {row.kpi || row.property}{row.unit ? ` (${row.unit})` : ''}
                      </td>
                      <td style={{padding:'2px 4px', textAlign:'right', color:txt}}>
                        {fmtNum(row.sim_value)}
                      </td>
                      <td style={{padding:'2px 4px', textAlign:'right', color:dim}}>
                        {fmtNum(row.ref_value)}
                      </td>
                      <td style={{padding:'2px 4px', textAlign:'right',
                                  color: row.status === 'PASS' ? '#86efac' : '#fca5a5'}}>
                        {(row.deviation_pct ?? 0).toFixed(2)}
                      </td>
                      <td style={{padding:'2px 4px',
                                  color: row.status === 'PASS' ? '#86efac' : '#fca5a5'}}>
                        {row.status === 'PASS' ? '✓' : '✗'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <button
            onClick={() => downloadMarkdown(result.publication_table,
                                            `literature_${result.process}.md`)}
            style={{marginTop:10, background:'#7c3aed', color:'#fff', border:'none',
                    borderRadius:6, padding:'4px 10px', fontSize:11, cursor:'pointer'}}>
            ⬇ Export markdown table
          </button>
        </div>
      )}

      {!result && !error && (
        <div style={{color:dim, fontSize:11, fontStyle:'italic', padding:'8px 4px'}}>
          Solve a flowsheet first, then pick a literature reference and click <b>Run Compare</b>.
          The agent will produce a publication-quality deviation table.
        </div>
      )}
    </div>
  );
}

function fmtNum(v: any): string {
  const n = Number(v);
  if (!isFinite(n)) return String(v ?? '—');
  if (Math.abs(n) >= 100) return n.toFixed(2);
  if (Math.abs(n) >= 1)   return n.toFixed(3);
  return n.toFixed(4);
}

function downloadMarkdown(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
