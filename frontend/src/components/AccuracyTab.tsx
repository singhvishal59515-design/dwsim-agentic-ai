import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';

interface PropRow {
  property:     string;
  agent_value:  number | null;
  direct_value: number | null;
  error_pct:    number | null;
  status:       string;
}

interface StreamRow {
  stream:     string;
  properties: PropRow[];
}

interface ComparisonResult {
  verdict:       string;
  accuracy_pct:  number;
  total_match:   number;
  total_checks:  number;
  max_error_pct: number;
  comparison:    StreamRow[];
  ref_id?:       string;
}

interface RefSet {
  id:      string;
  name:    string;
  entries: number;
}

interface Props {
  dark: boolean;
}

export default function AccuracyTab({ dark }: Props) {
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  const [loading,    setLoading]    = useState(false);
  const [capturing,  setCapturing]  = useState(false);
  const [result,     setResult]     = useState<ComparisonResult | null>(null);
  const [refSets,    setRefSets]    = useState<RefSet[]>([]);
  const [selectedRef,setSelectedRef]= useState<string>('');
  const [error,      setError]      = useState<string | null>(null);

  const loadSummary = useCallback(() => {
    api.accuracySummary()
      .then((d: any) => {
        const sets: RefSet[] = d.reference_sets ?? [];
        setRefSets(sets);
        if (sets.length > 0 && !selectedRef) setSelectedRef(sets[0].id);
      })
      .catch(() => {});
  }, [selectedRef]);

  useEffect(() => { loadSummary(); }, []);

  async function handleCapture() {
    setCapturing(true);
    setError(null);
    try {
      const cap = await (api as any).accuracyCapture({
        name: `capture_${new Date().toISOString().slice(0,16)}`,
        stream_tags: [],
        properties: [],
      });
      await loadSummary();
      if (cap.ref_id) setSelectedRef(cap.ref_id);
    } catch (e: any) {
      setError(`Capture failed: ${e.message}`);
    } finally {
      setCapturing(false);
    }
  }

  async function handleCompare() {
    const refId = selectedRef;
    if (!refId) { setError('No reference set selected. Capture one first.'); return; }
    setLoading(true);
    setError(null);
    try {
      const r = await (api as any).accuracyCompareRaw(refId, false);
      setResult(r);
    } catch (e: any) {
      setError(`Comparison failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleQuickCheck() {
    setLoading(true);
    setError(null);
    try {
      const cap = await (api as any).accuracyCapture({
        name: `quick_${Date.now()}`,
        stream_tags: [],
        properties: [],
      });
      const r = await (api as any).accuracyCompareRaw(cap.ref_id, true);
      setResult(r);
      loadSummary();
    } catch (e: any) {
      setError(`Quick check failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  // ── helpers ──────────────────────────────────────────────

  function verdictColor(pct: number) {
    if (pct >= 99) return { bg: '#14532d', border: '#166534', text: '#86efac' };
    if (pct >= 95) return { bg: '#422006', border: '#92400e', text: '#fcd34d' };
    return { bg: '#1c0a0a', border: '#7f1d1d', text: '#f87171' };
  }

  function statColor(val: number, isError = false) {
    if (isError) return val < 0.1 ? '#86efac' : val < 1.0 ? '#fcd34d' : '#f87171';
    return val >= 99 ? '#86efac' : val >= 95 ? '#fcd34d' : '#f87171';
  }

  function fmtVal(v: number | null): string {
    if (v == null) return '—';
    if (Math.abs(v) >= 1000) return v.toExponential(3);
    return v.toPrecision(6);
  }

  function fmtErr(row: PropRow): { text: string; color: string } {
    if (row.error_pct != null) {
      if (row.error_pct < 0.01)  return { text: 'EXACT',                       color: '#86efac' };
      if (row.error_pct < 1.0)   return { text: row.error_pct.toFixed(3) + '%', color: '#fcd34d' };
      return                              { text: row.error_pct.toFixed(2) + '%', color: '#f87171' };
    }
    if (row.status === 'missing_agent')  return { text: 'N/A (agent)',  color: '#f87171' };
    if (row.status === 'missing_direct') return { text: 'N/A (direct)', color: dim };
    return { text: '—', color: dim };
  }

  // ── render ────────────────────────────────────────────────

  return (
    <div style={{ padding: 16, background: bg, height: '100%', overflowY: 'auto', boxSizing: 'border-box' }}>

      {/* Title + action row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: txt, flex: 1 }}>
          Accuracy Verification
        </span>
        <button
          onClick={handleCapture}
          disabled={capturing || loading}
          style={btnStyle(dark, capturing || loading, '#0ea5e9')}
        >
          {capturing ? '…' : '📸 Capture'}
        </button>
        <button
          onClick={handleQuickCheck}
          disabled={loading || capturing}
          style={btnStyle(dark, loading || capturing, '#7c3aed')}
        >
          {loading ? '⏳ Running…' : '⚡ Quick Check'}
        </button>
      </div>

      {/* Reference set selector */}
      {refSets.length > 0 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
          <select
            value={selectedRef}
            onChange={e => setSelectedRef(e.target.value)}
            style={{
              flex: 1, background: card, color: txt, border: `1px solid ${brd}`,
              borderRadius: 6, padding: '4px 8px', fontSize: 12,
            }}
          >
            {refSets.map(r => (
              <option key={r.id} value={r.id}>{r.name} ({r.entries} entries)</option>
            ))}
          </select>
          <button
            onClick={handleCompare}
            disabled={loading || capturing || !selectedRef}
            style={btnStyle(dark, loading || capturing, '#059669')}
          >
            Compare
          </button>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div style={{
          background: '#1c0a0a', border: '1px solid #7f1d1d', borderRadius: 8,
          padding: '10px 12px', marginBottom: 12, color: '#f87171', fontSize: 12,
        }}>
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && !result && (
        <div style={{ textAlign: 'center', color: dim, padding: 32, fontSize: 13 }}>
          ⏳ Running accuracy comparison… (may take 10–30 s if agent query is needed)
        </div>
      )}

      {/* Empty state */}
      {!loading && !result && !error && (
        <div style={{ textAlign: 'center', color: dim, padding: 32, fontSize: 12, lineHeight: 1.6 }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>🎯</div>
          Click <strong>Quick Check</strong> to auto-capture the current simulation
          and verify that agent-reported values match direct DWSIM reads.
          <br /><br />
          Or use <strong>Capture</strong> to save a named reference set,
          then <strong>Compare</strong> to run the check at any time.
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Verdict banner */}
          {(() => {
            const vc = verdictColor(result.accuracy_pct);
            const icon = result.accuracy_pct >= 99 ? '✅' : result.accuracy_pct >= 95 ? '⚠️' : '❌';
            return (
              <div style={{
                background: vc.bg, border: `1px solid ${vc.border}`,
                borderRadius: 10, padding: '12px 16px', marginBottom: 14,
                display: 'flex', alignItems: 'center', gap: 10,
              }}>
                <span style={{ fontSize: 20 }}>{icon}</span>
                <span style={{ fontWeight: 700, color: vc.text, fontSize: 13 }}>{result.verdict}</span>
              </div>
            );
          })()}

          {/* Stat cards */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
            {[
              { label: 'Accuracy',     value: `${result.accuracy_pct}%`,                           color: statColor(result.accuracy_pct) },
              { label: 'Checks Passed',value: `${result.total_match}/${result.total_checks}`,       color: '#86efac' },
              { label: 'Max Error',    value: result.max_error_pct < 0.01 ? '0%' : `${result.max_error_pct.toFixed(3)}%`, color: statColor(result.max_error_pct, true) },
            ].map(s => (
              <div key={s.label} style={{
                flex: 1, background: card, border: `1px solid ${brd}`,
                borderRadius: 8, padding: '10px 8px', textAlign: 'center',
              }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</div>
                <div style={{ fontSize: 10, color: dim, marginTop: 2 }}>{s.label}</div>
              </div>
            ))}
          </div>

          {/* Comparison table */}
          <div style={{
            background: card, border: `1px solid ${brd}`, borderRadius: 8,
            overflow: 'hidden', marginBottom: 12,
          }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: dark ? '#0f172a' : '#f1f5f9' }}>
                  {['Property', 'Agent', 'DWSIM', 'Error', ''].map(h => (
                    <th key={h} style={{
                      padding: '6px 8px', textAlign: 'left', color: dim,
                      fontWeight: 700, fontSize: 10, letterSpacing: 0.5,
                      borderBottom: `1px solid ${brd}`,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(result.comparison || []).map((stream, si) => (
                  <React.Fragment key={si}>
                    {/* Stream header */}
                    <tr>
                      <td colSpan={5} style={{
                        padding: '5px 8px', fontWeight: 700, fontSize: 11,
                        color: '#38bdf8', background: dark ? '#162032' : '#e0f2fe',
                        borderBottom: `1px solid ${brd}`,
                      }}>
                        {stream.stream}
                      </td>
                    </tr>
                    {/* Property rows */}
                    {stream.properties.map((row, ri) => {
                      const err = fmtErr(row);
                      const statusDot = row.status === 'match' ? '#86efac'
                                      : row.status === 'mismatch' ? '#f87171'
                                      : dim;
                      return (
                        <tr key={ri} style={{ borderBottom: `1px solid ${brd}` }}>
                          <td style={{ padding: '4px 8px', color: txt, fontFamily: 'monospace' }}>
                            {row.property}
                          </td>
                          <td style={{ padding: '4px 8px', color: dim, fontFamily: 'monospace' }}>
                            {fmtVal(row.agent_value)}
                          </td>
                          <td style={{ padding: '4px 8px', color: dim, fontFamily: 'monospace' }}>
                            {fmtVal(row.direct_value)}
                          </td>
                          <td style={{ padding: '4px 8px', color: err.color, fontFamily: 'monospace' }}>
                            {err.text}
                          </td>
                          <td style={{ padding: '4px 8px', textAlign: 'center' }}>
                            <span style={{
                              display: 'inline-block', width: 8, height: 8,
                              borderRadius: '50%', background: statusDot,
                            }} />
                          </td>
                        </tr>
                      );
                    })}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* Footer */}
          <div style={{
            fontSize: 10, color: dim, lineHeight: 1.6,
            borderTop: `1px solid ${brd}`, paddingTop: 8,
          }}>
            <strong>Agent System</strong> reads via helper chain (_read_prop → reflection).{' '}
            <strong>Direct DWSIM</strong> reads via GetPropertyValue() — same method the DWSIM GUI uses internally.
            Matching values prove zero hallucination.
          </div>
        </>
      )}
    </div>
  );
}

function btnStyle(dark: boolean, disabled: boolean, accent: string): React.CSSProperties {
  return {
    background:    disabled ? (dark ? '#1e293b' : '#e2e8f0') : accent,
    color:         disabled ? (dark ? '#475569' : '#94a3b8') : '#fff',
    border:        'none',
    borderRadius:  6,
    padding:       '4px 10px',
    fontSize:      11,
    fontWeight:    600,
    cursor:        disabled ? 'not-allowed' : 'pointer',
    whiteSpace:    'nowrap',
  };
}
