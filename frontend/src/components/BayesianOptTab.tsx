import React, { useState, useRef } from 'react';
import { api } from '../utils/api';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Scatter, ScatterChart,
} from 'recharts';

interface Variable { tag: string; property: string; unit: string; lower: number; upper: number; }
interface HistEntry { iteration: number; phase: string; value: number | null; best_so_far: number; params: Record<string,number>; note?: string; }
interface BOResult {
  best_params:  Record<string,number>;
  best_value:   number;
  n_evals:      number;
  converged:    boolean;
  minimize:     boolean;
  duration_s:   number;
  history:      HistEntry[];
  variables:    Array<{name:string; lower:number; upper:number; best:number}>;
}

interface Props { dark: boolean; }

export default function BayesianOptTab({ dark }: Props) {
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  const [vars,      setVars]      = useState<Variable[]>([{ tag:'', property:'', unit:'', lower:0, upper:1 }]);
  const [obsTag,    setObsTag]    = useState('');
  const [obsProp,   setObsProp]   = useState('');
  const [minimize,  setMinimize]  = useState(true);
  const [nInit,     setNInit]     = useState(5);
  const [maxIter,   setMaxIter]   = useState(20);
  const [xi,        setXi]        = useState(0.01);
  const [running,   setRunning]   = useState(false);
  const [result,    setResult]    = useState<BOResult | null>(null);
  const [error,     setError]     = useState('');
  const [liveLog,   setLiveLog]   = useState<string[]>([]);
  const logRef = useRef<HTMLDivElement>(null);

  const inp = (val: string|number, set: (v:any)=>void, ph='', type='text') => (
    <input type={type} value={val} onChange={e => set(type==='number' ? +e.target.value : e.target.value)}
      placeholder={ph}
      style={{ width:'100%', background: dark?'#0f172a':'#f8fafc', border:`1px solid ${brd}`,
               borderRadius:5, color:txt, fontSize:11, padding:'4px 8px' }} />
  );

  const updateVar = (i: number, field: keyof Variable, val: any) => {
    setVars(vs => vs.map((v,j) => j===i ? {...v, [field]: val} : v));
  };
  const addVar = () => setVars(vs => [...vs, {tag:'',property:'',unit:'',lower:0,upper:1}]);
  const removeVar = (i: number) => setVars(vs => vs.filter((_,j)=>j!==i));

  const run = async () => {
    const validVars = vars.filter(v => v.tag && v.property && v.lower < v.upper);
    if (!validVars.length || !obsTag || !obsProp) {
      setError('Fill at least 1 variable with tag/property/bounds, and set observation target.');
      return;
    }
    setError(''); setRunning(true); setResult(null); setLiveLog([]);
    try {
      const req = { variables: validVars, observe_tag: obsTag, observe_property: obsProp,
                    minimize, n_initial: nInit, max_iter: maxIter, xi };
      const d: any = await api.bayesianOptimize(req);
      setResult(d);
    } catch (e: any) { setError(e.message || 'Bayesian optimisation failed'); }
    finally { setRunning(false); }
  };

  // Chart data
  const chartData = result?.history.map(h => ({
    it:    h.iteration,
    value: h.value,
    best:  h.best_so_far,
    phase: h.phase,
  })) || [];

  const lhsData = chartData.filter(d => d.phase === 'LHS');
  const boData  = chartData.filter(d => d.phase === 'BO' && d.value != null);

  return (
    <div style={{ padding:12, overflowY:'auto', height:'100%' }}>
      {/* Header */}
      <div style={{ background:card, border:`1px solid ${brd}`, borderRadius:10, padding:12, marginBottom:12 }}>
        <div style={{ fontWeight:700, color:txt, fontSize:13, marginBottom:4 }}>
          Bayesian Optimisation
        </div>
        <div style={{ color:dim, fontSize:11, lineHeight:1.5 }}>
          GP surrogate + Expected Improvement. Best for expensive simulations with 1–4 variables.
          Total evaluations = n_initial + max_iter (default 5+20 = 25 max).
        </div>
      </div>

      {/* Variables */}
      <div style={{ background:card, border:`1px solid ${brd}`, borderRadius:10, padding:12, marginBottom:12 }}>
        <div style={{ display:'flex', justifyContent:'space-between', marginBottom:8 }}>
          <div style={{ fontWeight:700, color:txt, fontSize:12 }}>Decision Variables</div>
          <button onClick={addVar} disabled={vars.length>=4} style={{
            background:'#0ea5e9', color:'#fff', border:'none', borderRadius:5,
            padding:'2px 10px', fontSize:11, cursor:vars.length>=4?'not-allowed':'pointer',
          }}>+ Add</button>
        </div>
        {vars.map((v, i) => (
          <div key={i} style={{ display:'grid', gridTemplateColumns:'2fr 2fr 1fr 1fr 1fr auto', gap:6, marginBottom:6, alignItems:'center' }}>
            {inp(v.tag,      val => updateVar(i,'tag',val),      'Tag (e.g. Feed)')}
            {inp(v.property, val => updateVar(i,'property',val), 'Property (e.g. MassFlow)')}
            {inp(v.unit,     val => updateVar(i,'unit',val),     'Unit')}
            {inp(v.lower, val => updateVar(i,'lower',+val), 'Lo', 'number')}
            {inp(v.upper, val => updateVar(i,'upper',+val), 'Hi', 'number')}
            <button onClick={() => removeVar(i)} style={{
              background:'none', border:`1px solid ${brd}`, color:'#f87171',
              borderRadius:4, padding:'2px 6px', fontSize:11, cursor:'pointer',
            }}>×</button>
          </div>
        ))}
        <div style={{ display:'grid', gridTemplateColumns:'2fr 3fr', gap:8, marginTop:10 }}>
          <div style={{ color:dim, fontSize:10, alignSelf:'center' }}>Observe tag</div>
          {inp(obsTag, setObsTag, 'e.g. Product')}
          <div style={{ color:dim, fontSize:10, alignSelf:'center' }}>Observe property</div>
          {inp(obsProp, setObsProp, 'e.g. temperature_C, mole_fraction_Ethanol')}
        </div>
      </div>

      {/* Settings */}
      <div style={{ background:card, border:`1px solid ${brd}`, borderRadius:10, padding:12, marginBottom:12 }}>
        <div style={{ fontWeight:700, color:txt, fontSize:12, marginBottom:8 }}>Settings</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
          <div>
            <div style={{ color:dim, fontSize:10, marginBottom:3 }}>Mode</div>
            <div style={{ display:'flex', gap:6 }}>
              {(['Minimise','Maximise'] as const).map(m => (
                <button key={m} onClick={() => setMinimize(m==='Minimise')} style={{
                  flex:1, background: (minimize===( m==='Minimise')) ? '#0ea5e9' : 'none',
                  color:   (minimize===( m==='Minimise')) ? '#fff' : dim,
                  border: `1px solid ${(minimize===(m==='Minimise'))? '#0ea5e9' : brd}`,
                  borderRadius:5, padding:'4px 0', fontSize:11, cursor:'pointer',
                }}>{m}</button>
              ))}
            </div>
          </div>
          <div>
            <div style={{ color:dim, fontSize:10, marginBottom:3 }}>EI exploration (xi)</div>
            <select value={xi} onChange={e => setXi(+e.target.value)} style={{
              width:'100%', background:dark?'#0f172a':'#f8fafc', border:`1px solid ${brd}`,
              color:txt, fontSize:11, borderRadius:5, padding:'4px 8px',
            }}>
              {[0.001,0.01,0.05,0.1,0.5].map(v=><option key={v} value={v}>{v} {v===0.01?'(default)':v>=0.1?'(explore)':'(exploit)'}</option>)}
            </select>
          </div>
          <div>
            <div style={{ color:dim, fontSize:10, marginBottom:3 }}>LHS warm-up (n_initial)</div>
            <select value={nInit} onChange={e=>setNInit(+e.target.value)} style={{
              width:'100%', background:dark?'#0f172a':'#f8fafc', border:`1px solid ${brd}`,
              color:txt, fontSize:11, borderRadius:5, padding:'4px 8px',
            }}>
              {[3,5,8,10].map(n=><option key={n} value={n}>{n} evals</option>)}
            </select>
          </div>
          <div>
            <div style={{ color:dim, fontSize:10, marginBottom:3 }}>BO iterations (max_iter)</div>
            <select value={maxIter} onChange={e=>setMaxIter(+e.target.value)} style={{
              width:'100%', background:dark?'#0f172a':'#f8fafc', border:`1px solid ${brd}`,
              color:txt, fontSize:11, borderRadius:5, padding:'4px 8px',
            }}>
              {[10,15,20,30,50].map(n=><option key={n} value={n}>{n} iters  (total {nInit+n})</option>)}
            </select>
          </div>
        </div>
      </div>

      {error && <div style={{ color:'#f87171', fontSize:11, marginBottom:8, background:'#3b1f1f', padding:8, borderRadius:6 }}>{error}</div>}

      <button onClick={run} disabled={running} style={{
        width:'100%', background: running?'#334155':'#7c3aed', color:'#fff',
        border:'none', borderRadius:8, padding:'9px 0', fontWeight:700, fontSize:13,
        cursor:running?'not-allowed':'pointer', marginBottom:16,
      }}>
        {running ? `⏳ Running BO (${nInit+maxIter} evals max)…` : `Run Bayesian Optimisation (${nInit+maxIter} evals)`}
      </button>

      {/* Results */}
      {result && (
        <>
          {/* Summary cards */}
          <div style={{ display:'flex', gap:8, flexWrap:'wrap', marginBottom:12 }}>
            {[
              { label:'Best value', val:result.best_value.toFixed(5), color:'#4ade80' },
              { label:'Evals used', val:`${result.n_evals}/${nInit+maxIter}`, color:'#38bdf8' },
              { label:'Converged', val:result.converged?'Yes':'No', color:result.converged?'#4ade80':'#fbbf24' },
              { label:'Duration', val:`${result.duration_s}s`, color:'#a78bfa' },
            ].map(c => (
              <div key={c.label} style={{ background:card, border:`1px solid ${brd}`, borderRadius:8, padding:'8px 14px', flex:'1 1 100px' }}>
                <div style={{ color:dim, fontSize:10 }}>{c.label}</div>
                <div style={{ color:c.color, fontSize:16, fontWeight:700 }}>{c.val}</div>
              </div>
            ))}
          </div>

          {/* Best params */}
          <div style={{ background:card, border:`1px solid #4ade80`, borderRadius:10, padding:12, marginBottom:12 }}>
            <div style={{ fontWeight:700, color:'#4ade80', fontSize:12, marginBottom:8 }}>Optimal Parameters Found</div>
            {result.variables.map(v => {
              const pct = ((v.best - v.lower)/(v.upper - v.lower)*100).toFixed(0);
              return (
                <div key={v.name} style={{ marginBottom:8 }}>
                  <div style={{ display:'flex', justifyContent:'space-between', marginBottom:3 }}>
                    <span style={{ color:txt, fontSize:12, fontFamily:'monospace' }}>{v.name}</span>
                    <span style={{ color:'#4ade80', fontWeight:700, fontSize:13 }}>{v.best.toFixed(5)}</span>
                  </div>
                  <div style={{ position:'relative', height:6, background:dark?'#334155':'#e2e8f0', borderRadius:3, overflow:'hidden' }}>
                    <div style={{ position:'absolute', left:0, top:0, height:'100%', width:`${pct}%`, background:'#4ade80', borderRadius:3 }} />
                  </div>
                  <div style={{ color:dim, fontSize:9, marginTop:2 }}>
                    [{v.lower}, {v.upper}] — at {pct}% of range
                  </div>
                </div>
              );
            })}
          </div>

          {/* Convergence chart */}
          <div style={{ background:card, border:`1px solid ${brd}`, borderRadius:10, padding:12, marginBottom:12 }}>
            <div style={{ fontWeight:600, color:txt, fontSize:12, marginBottom:8 }}>
              Convergence  <span style={{ color:dim, fontWeight:400 }}>(LHS = blue, BO = pink, green = best so far)</span>
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={chartData} margin={{ top:5, right:10, left:-15, bottom:20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={dark?'#334155':'#e2e8f0'} />
                <XAxis dataKey="it" label={{ value:'Eval #', position:'insideBottom', offset:-8, fill:dim, fontSize:10 }} tick={{ fill:dim, fontSize:9 }} />
                <YAxis tick={{ fill:dim, fontSize:9 }} />
                <Tooltip contentStyle={{ background:card, border:`1px solid ${brd}`, fontSize:11 }}
                  formatter={(v: any) => [typeof v === 'number' ? v.toFixed(5) : v]} />
                <Line type="monotone" dataKey="best" stroke="#4ade80" strokeWidth={2} dot={false} name="Best so far" />
                <Line type="monotone" dataKey="value" stroke="#94a3b8" strokeWidth={1} dot={{ fill:'#f472b6', r:3 }} name="Eval value" />
                <ReferenceLine x={nInit + 0.5} stroke="#64748b" strokeDasharray="4 2" label={{ value:'LHS→BO', fill:dim, fontSize:9 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* History table (last 15) */}
          <div style={{ background:card, border:`1px solid ${brd}`, borderRadius:10, padding:12 }}>
            <div style={{ fontWeight:600, color:txt, fontSize:12, marginBottom:8 }}>Evaluation History (last 15)</div>
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:10 }}>
                <thead>
                  <tr style={{ borderBottom:`1px solid ${brd}` }}>
                    {['#','Phase','Value','Best'].map(h=>(
                      <th key={h} style={{ textAlign:'left', color:dim, padding:'2px 6px', fontWeight:600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.history.slice(-15).map((h,i)=>(
                    <tr key={i} style={{ borderBottom:`1px solid ${brd}` }}>
                      <td style={{ color:dim, padding:'3px 6px' }}>{h.iteration}</td>
                      <td style={{ color: h.phase==='LHS'?'#38bdf8':'#f472b6', padding:'3px 6px', fontWeight:600 }}>{h.phase}</td>
                      <td style={{ color: h.value==null?'#f87171':txt, padding:'3px 6px' }}>
                        {h.value!=null ? h.value.toFixed(5) : 'failed'}
                      </td>
                      <td style={{ color:'#4ade80', padding:'3px 6px', fontWeight:h.iteration===result.n_evals?700:400 }}>
                        {h.best_so_far.toFixed(5)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {!result && !running && (
        <div style={{ color:dim, fontSize:12, textAlign:'center', padding:'20px 0' }}>
          Configure variables and objective above, then click Run.<br/>
          <span style={{ fontSize:10 }}>
            Tip: the agent can also call bayesian_optimize automatically when you ask it to optimise operating conditions.
          </span>
        </div>
      )}
    </div>
  );
}
