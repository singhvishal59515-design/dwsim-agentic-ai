import React, { useState, useCallback } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { api } from '../utils/api';

interface PinchResult {
  success: boolean;
  min_approach_temp_C: number;
  pinch_temp_C: number | null;
  QH_current_kW: number;
  QC_current_kW: number;
  QH_min_kW: number;
  QC_min_kW: number;
  potential_savings_kW: number;
  heat_recovery_pct: number;
  hot_streams: Array<{ tag: string; T_in_C: number; T_out_C: number; duty_kW: number }>;
  cold_streams: Array<{ tag: string; T_in_C: number; T_out_C: number; duty_kW: number }>;
  interpretation: string;
  message?: string;
}

interface Props { dark?: boolean; }

function buildCompositeCurve(
  streams: Array<{ T_in_C: number; T_out_C: number; duty_kW: number }>,
  shift: number = 0,
): Array<{ T: number; H: number }> {
  if (!streams.length) return [];
  // Collect all unique temperatures
  const temps = [...new Set(
    streams.flatMap(s => [s.T_in_C + shift, s.T_out_C + shift])
  )].sort((a, b) => a - b);

  // Build enthalpy cumulative curve
  const points: Array<{ T: number; H: number }> = [];
  let H = 0;
  for (let i = 0; i < temps.length - 1; i++) {
    const Tlo = temps[i], Thi = temps[i + 1];
    points.push({ T: Tlo, H });
    const dH = streams.reduce((sum, s) => {
      const sLo = Math.min(s.T_in_C, s.T_out_C) + shift;
      const sHi = Math.max(s.T_in_C, s.T_out_C) + shift;
      if (sLo <= Tlo && sHi >= Thi) {
        return sum + s.duty_kW * (Thi - Tlo) / Math.max(sHi - sLo, 0.001);
      }
      return sum;
    }, 0);
    H += dH;
  }
  points.push({ T: temps[temps.length - 1], H });
  return points;
}

export default function PinchChart({ dark = true }: Props) {
  const [result, setResult]   = useState<PinchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [dTmin,   setDTmin]   = useState(10);
  const [error,   setError]   = useState('');

  const BG   = dark ? '#0f172a' : '#f8fafc';
  const PNL  = dark ? '#1e293b' : '#fff';
  const BRD  = dark ? '#334155' : '#e2e8f0';
  const DIM  = dark ? '#64748b' : '#94a3b8';
  const TXT  = dark ? '#e2e8f0' : '#1e293b';
  const TTBG = dark ? '#1e293b' : '#fff';

  const run = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const r: any = await api.pinchAnalysis(dTmin);
      if (r.success === false) { setError(r.error || 'Pinch analysis failed'); }
      else { setResult(r); }
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, [dTmin]);

  const hotCurve  = result ? buildCompositeCurve(result.hot_streams)         : [];
  const coldCurve = result ? buildCompositeCurve(result.cold_streams, dTmin)  : [];

  // Merge into single dataset keyed by enthalpy position
  const maxH = Math.max(
    hotCurve.length  ? hotCurve[hotCurve.length - 1].H  : 0,
    coldCurve.length ? coldCurve[coldCurve.length - 1].H : 0,
  );
  const chartData: Array<{ H: number; hot?: number; cold?: number }> = [];
  const allH = [...new Set([
    ...hotCurve.map(p => p.H),
    ...coldCurve.map(p => p.H),
  ])].sort((a, b) => a - b);

  const interp = (curve: Array<{ T: number; H: number }>, h: number) => {
    if (!curve.length) return undefined;
    for (let i = 0; i < curve.length - 1; i++) {
      if (h >= curve[i].H && h <= curve[i + 1].H) {
        const frac = (h - curve[i].H) / Math.max(curve[i + 1].H - curve[i].H, 0.001);
        return curve[i].T + frac * (curve[i + 1].T - curve[i].T);
      }
    }
    return undefined;
  };

  allH.forEach(h => {
    chartData.push({ H: Math.round(h), hot: interp(hotCurve, h), cold: interp(coldCurve, h) });
  });

  return (
    <div style={{ background: BG, height: '100%', overflowY: 'auto', padding: 12 }}>
      {/* Controls */}
      <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:12, flexWrap:'wrap' }}>
        <span style={{ fontSize:13, fontWeight:700, color:'#38bdf8' }}>🌡 Pinch Analysis</span>
        <label style={{ fontSize:11, color:DIM }}>ΔT<sub>min</sub> (°C):</label>
        <input
          type="number" min={1} max={50} value={dTmin}
          onChange={e => setDTmin(Number(e.target.value))}
          style={{ width:60, background:PNL, border:`1px solid ${BRD}`, borderRadius:4,
                   color:TXT, padding:'3px 6px', fontSize:12 }}
        />
        <button onClick={run} disabled={loading} style={{
          background: loading ? '#334155' : '#0ea5e9', color:'#fff', border:'none',
          borderRadius:6, padding:'5px 14px', cursor: loading ? 'not-allowed' : 'pointer',
          fontWeight:600, fontSize:12,
        }}>
          {loading ? 'Running…' : 'Run Analysis'}
        </button>
      </div>

      {error && (
        <div style={{ background:'#3b1f1f', border:`1px solid #7f1d1d`, borderRadius:6,
                      padding:'8px 12px', color:'#f87171', fontSize:12, marginBottom:10 }}>
          ⚠ {error}
        </div>
      )}

      {!result && !loading && (
        <div style={{ textAlign:'center', color:DIM, fontSize:12, paddingTop:40 }}>
          <div style={{ fontSize:32, marginBottom:8 }}>🔥❄️</div>
          Load a flowsheet with heaters/coolers, then click Run Analysis.
        </div>
      )}

      {result?.message && (
        <div style={{ color:DIM, fontSize:12, padding:'20px 0', textAlign:'center' }}>
          {result.message}
        </div>
      )}

      {result && !result.message && (
        <>
          {/* Summary cards */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:8, marginBottom:12 }}>
            {[
              { label:'QH Current', val:`${result.QH_current_kW} kW`,  color:'#f87171' },
              { label:'QH Minimum', val:`${result.QH_min_kW} kW`,      color:'#34d399' },
              { label:'Savings',    val:`${result.potential_savings_kW} kW (${result.heat_recovery_pct}%)`, color:'#fbbf24' },
              { label:'QC Current', val:`${result.QC_current_kW} kW`,  color:'#60a5fa' },
              { label:'QC Minimum', val:`${result.QC_min_kW} kW`,      color:'#34d399' },
              { label:'Pinch Temp', val: result.pinch_temp_C != null ? `${result.pinch_temp_C}°C` : '—', color:'#a78bfa' },
            ].map(({ label, val, color }) => (
              <div key={label} style={{ background:PNL, border:`1px solid ${BRD}`,
                                        borderRadius:6, padding:'8px 10px' }}>
                <div style={{ fontSize:10, color:DIM, marginBottom:2 }}>{label}</div>
                <div style={{ fontSize:13, fontWeight:700, color }}>{val}</div>
              </div>
            ))}
          </div>

          {/* Composite curves */}
          {chartData.length > 1 && (
            <>
              <div style={{ fontSize:11, color:DIM, marginBottom:4 }}>
                Composite Curves — hot (red) vs cold (blue, shifted +ΔT<sub>min</sub>)
              </div>
              <ResponsiveContainer width="100%" height={240}>
                <ComposedChart data={chartData} margin={{ top:4, right:8, left:0, bottom:4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRD} />
                  <XAxis dataKey="H" stroke={DIM} tick={{ fontSize:10, fill:DIM }}
                         label={{ value:'Enthalpy (kW)', position:'insideBottom', fill:DIM, fontSize:10, dy:10 }} />
                  <YAxis stroke={DIM} tick={{ fontSize:10, fill:DIM }}
                         label={{ value:'T (°C)', angle:-90, position:'insideLeft', fill:DIM, fontSize:10 }} />
                  <Tooltip contentStyle={{ background:TTBG, border:`1px solid ${BRD}`, color:TXT, fontSize:11 }}
                           formatter={(v: any, name: string) => [`${Number(v).toFixed(1)}°C`, name]}
                           labelFormatter={(l: any) => `H = ${l} kW`} />
                  <Legend wrapperStyle={{ fontSize:10 }} />
                  {result.pinch_temp_C && (
                    <ReferenceLine y={result.pinch_temp_C} stroke="#a78bfa"
                                   strokeDasharray="4 4" label={{ value:`Pinch ${result.pinch_temp_C}°C`, fill:'#a78bfa', fontSize:9 }} />
                  )}
                  <Line type="monotone" dataKey="hot"  name="Hot Composite"  stroke="#f87171" strokeWidth={2} dot={false} connectNulls />
                  <Line type="monotone" dataKey="cold" name="Cold Composite" stroke="#60a5fa" strokeWidth={2} dot={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </>
          )}

          {/* Interpretation */}
          <div style={{ marginTop:10, background:PNL, border:`1px solid ${BRD}`, borderRadius:6,
                        padding:'8px 12px', fontSize:12, color:DIM, lineHeight:1.6 }}>
            {result.interpretation}
          </div>

          {/* Stream tables */}
          {(result.hot_streams.length > 0 || result.cold_streams.length > 0) && (
            <div style={{ marginTop:10, display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
              {[
                { title:'🔥 Hot Streams (need cooling)', streams: result.hot_streams, color:'#f87171' },
                { title:'❄️ Cold Streams (need heating)',  streams: result.cold_streams, color:'#60a5fa' },
              ].map(({ title, streams, color }) => (
                <div key={title}>
                  <div style={{ fontSize:11, fontWeight:600, color, marginBottom:4 }}>{title}</div>
                  <table style={{ width:'100%', fontSize:10, borderCollapse:'collapse' }}>
                    <thead>
                      <tr>
                        {['Tag','T_in','T_out','Duty(kW)'].map(h => (
                          <th key={h} style={{ textAlign:'left', padding:'3px 6px', color:DIM, borderBottom:`1px solid ${BRD}` }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {streams.map(s => (
                        <tr key={s.tag}>
                          <td style={{ padding:'3px 6px', color:TXT, fontFamily:'monospace' }}>{s.tag}</td>
                          <td style={{ padding:'3px 6px', color:TXT, fontFamily:'monospace' }}>{s.T_in_C.toFixed(1)}°C</td>
                          <td style={{ padding:'3px 6px', color:TXT, fontFamily:'monospace' }}>{s.T_out_C.toFixed(1)}°C</td>
                          <td style={{ padding:'3px 6px', color:TXT, fontFamily:'monospace' }}>{s.duty_kW.toFixed(1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
