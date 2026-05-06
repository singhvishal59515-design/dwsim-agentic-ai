import React, { useState } from 'react';
import { api } from '../utils/api';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Cell,
} from 'recharts';

interface MCResult {
  variable: string;
  mean: number; std: number;
  p5: number; p25: number; p50: number; p75: number; p95: number;
  unit?: string;
}

interface Props { dark: boolean; }

const COLORS = ['#38bdf8','#818cf8','#34d399','#fb923c','#f472b6','#a78bfa'];

export default function MonteCarloTab({ dark }: Props) {
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  const [target, setTarget]       = useState('');
  const [param,  setParam]        = useState('');
  const [pMin,   setPMin]         = useState('');
  const [pMax,   setPMax]         = useState('');
  const [dist,   setDist]         = useState<'normal'|'uniform'|'triangular'>('normal');
  const [nSamp,  setNSamp]        = useState(200);
  const [results, setResults]     = useState<MCResult[]>([]);
  const [running, setRunning]     = useState(false);
  const [error,   setError]       = useState('');
  const [raw,     setRaw]         = useState<any>(null);

  const run = async () => {
    if (!target || !param) { setError('Target stream/property and parameter required'); return; }
    setError(''); setRunning(true);
    try {
      const req: any = {
        target_variable: target,
        parameters: [{
          name: param,
          distribution: dist,
          min: parseFloat(pMin) || undefined,
          max: parseFloat(pMax) || undefined,
          mean: (parseFloat(pMin) + parseFloat(pMax)) / 2 || undefined,
          std: (parseFloat(pMax) - parseFloat(pMin)) / 6 || undefined,
        }],
        n_samples: nSamp,
        seed: 42,
      };
      const d: any = await api.monteCarlo(req);
      setRaw(d);
      if (d?.results) {
        const mapped: MCResult[] = Object.entries(d.results).map(([k, v]: any) => ({
          variable: k,
          mean: v.mean, std: v.std,
          p5: v.p5, p25: v.p25, p50: v.p50, p75: v.p75, p95: v.p95,
          unit: v.unit || '',
        }));
        setResults(mapped);
      }
    } catch (e: any) { setError(e.message || 'Monte Carlo failed'); }
    finally { setRunning(false); }
  };

  const input = (val: string, set: (s: string) => void, ph: string) => (
    <input
      value={val} onChange={e => set(e.target.value)} placeholder={ph}
      style={{
        background: dark ? '#0f172a' : '#f8fafc',
        border: `1px solid ${brd}`, borderRadius: 5,
        color: txt, fontSize: 11, padding: '4px 8px', width: '100%',
      }}
    />
  );

  const boxPlotData = results.map((r, i) => ({
    name: r.variable.length > 14 ? r.variable.slice(0,12)+'…' : r.variable,
    p5: r.p5, p25: r.p25, median: r.p50, p75: r.p75, p95: r.p95, mean: r.mean,
    color: COLORS[i % COLORS.length],
  }));

  return (
    <div style={{ padding: 12, overflowY: 'auto', height: '100%' }}>
      {/* Config card */}
      <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 10, padding: 12, marginBottom: 12 }}>
        <div style={{ fontWeight: 700, color: txt, fontSize: 12, marginBottom: 10 }}>
          🎲 Monte Carlo Uncertainty Analysis
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
          <div>
            <div style={{ color: dim, fontSize: 10, marginBottom: 2 }}>Target variable (stream.property)</div>
            {input(target, setTarget, 'e.g. Product.temperature_C')}
          </div>
          <div>
            <div style={{ color: dim, fontSize: 10, marginBottom: 2 }}>Uncertain parameter</div>
            {input(param, setParam, 'e.g. Feed.mass_flow_kgh')}
          </div>
          <div>
            <div style={{ color: dim, fontSize: 10, marginBottom: 2 }}>Min value</div>
            {input(pMin, setPMin, 'e.g. 900')}
          </div>
          <div>
            <div style={{ color: dim, fontSize: 10, marginBottom: 2 }}>Max value</div>
            {input(pMax, setPMax, 'e.g. 1100')}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <div style={{ color: dim, fontSize: 11 }}>Distribution:</div>
          {(['normal','uniform','triangular'] as const).map(d => (
            <button key={d} onClick={() => setDist(d)} style={{
              background: dist === d ? '#0ea5e9' : 'none',
              color: dist === d ? '#fff' : dim,
              border: `1px solid ${dist === d ? '#0ea5e9' : brd}`,
              borderRadius: 5, padding: '2px 8px', fontSize: 10, cursor: 'pointer',
            }}>{d}</button>
          ))}
          <span style={{ marginLeft: 'auto', color: dim, fontSize: 10 }}>N=</span>
          <select value={nSamp} onChange={e => setNSamp(+e.target.value)} style={{
            background: dark ? '#0f172a' : '#f8fafc', border: `1px solid ${brd}`,
            color: txt, fontSize: 10, borderRadius: 4, padding: '2px 4px',
          }}>
            {[100, 200, 500, 1000].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        {error && <div style={{ color: '#f87171', fontSize: 11, marginBottom: 6 }}>{error}</div>}
        <button onClick={run} disabled={running} style={{
          width: '100%', background: running ? '#334155' : '#0ea5e9',
          color: '#fff', border: 'none', borderRadius: 6,
          padding: '7px 0', fontWeight: 700, fontSize: 12, cursor: running ? 'not-allowed' : 'pointer',
        }}>
          {running ? '⏳ Running…' : '▶ Run Monte Carlo'}
        </button>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <>
          {/* Stats table */}
          <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 10, padding: 12, marginBottom: 12 }}>
            <div style={{ fontWeight: 600, color: txt, fontSize: 11, marginBottom: 8 }}>Results Summary</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${brd}` }}>
                    {['Variable','Mean','Std','P5','P50','P95','Unit'].map(h => (
                      <th key={h} style={{ textAlign: 'left', color: dim, padding: '3px 6px', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr key={r.variable} style={{ borderBottom: `1px solid ${brd}` }}>
                      <td style={{ color: COLORS[i % COLORS.length], padding: '4px 6px', fontFamily: 'monospace', fontWeight: 600 }}>{r.variable}</td>
                      <td style={{ color: txt, padding: '4px 6px' }}>{r.mean.toFixed(3)}</td>
                      <td style={{ color: '#fbbf24', padding: '4px 6px' }}>±{r.std.toFixed(3)}</td>
                      <td style={{ color: dim, padding: '4px 6px' }}>{r.p5.toFixed(3)}</td>
                      <td style={{ color: txt, padding: '4px 6px', fontWeight: 600 }}>{r.p50.toFixed(3)}</td>
                      <td style={{ color: dim, padding: '4px 6px' }}>{r.p95.toFixed(3)}</td>
                      <td style={{ color: dim, padding: '4px 6px' }}>{r.unit}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Box-plot style chart */}
          <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 10, padding: 12, marginBottom: 12 }}>
            <div style={{ fontWeight: 600, color: txt, fontSize: 11, marginBottom: 8 }}>P5 – P95 Range (bar = P25–P75, dot = mean)</div>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={boxPlotData} margin={{ top: 5, right: 10, left: -10, bottom: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={dark ? '#334155' : '#e2e8f0'} />
                <XAxis dataKey="name" tick={{ fill: dim, fontSize: 9 }} />
                <YAxis tick={{ fill: dim, fontSize: 9 }} />
                <Tooltip
                  contentStyle={{ background: card, border: `1px solid ${brd}`, fontSize: 11 }}
                  formatter={(v: any, n: string) => [Number(v).toFixed(3), n]}
                />
                <Bar dataKey="p95" name="P95" fill="#475569" radius={[4,4,0,0]}>
                  {boxPlotData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length] + '44'} />)}
                </Bar>
                <Bar dataKey="median" name="Median" fill="#38bdf8" radius={[4,4,0,0]}>
                  {boxPlotData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* 90% CI callout */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {results.map((r, i) => (
              <div key={r.variable} style={{
                background: card, border: `1px solid ${COLORS[i % COLORS.length]}`,
                borderRadius: 8, padding: '8px 12px', flex: '1 1 160px',
              }}>
                <div style={{ color: COLORS[i % COLORS.length], fontSize: 10, fontWeight: 700 }}>{r.variable}</div>
                <div style={{ color: txt, fontSize: 13, fontWeight: 700, margin: '2px 0' }}>{r.mean.toFixed(3)}</div>
                <div style={{ color: dim, fontSize: 10 }}>90% CI: [{r.p5.toFixed(2)}, {r.p95.toFixed(2)}]</div>
                <div style={{ color: dim, fontSize: 10 }}>CV: {(r.std / Math.abs(r.mean) * 100).toFixed(1)}%</div>
              </div>
            ))}
          </div>
        </>
      )}

      {results.length === 0 && !running && (
        <div style={{ color: dim, fontSize: 12, textAlign: 'center', marginTop: 30 }}>
          Configure parameters above and click Run to perform uncertainty analysis.
          <br/><br/>
          <span style={{ fontSize: 10 }}>Uses seed=42 for reproducibility. N=200 samples recommended.</span>
        </div>
      )}
    </div>
  );
}
