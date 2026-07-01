import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../utils/api';
import { IntentVerification, IntentFinding } from '../types';

interface Props { dark: boolean; }

type TargetKind = 'product_purity' | 'max_impurity' | 'min_yield' | 'unit_setpoint';

interface DraftTarget {
  kind:          TargetKind;
  stream_tag?:   string;
  unit_tag?:     string;
  property_name?:string;
  compound?:     string;
  expected:      string;
  tolerance?:    string;
}

const EMPTY_TARGET: DraftTarget = { kind: 'product_purity', expected: '0.95' };

export default function IntentPanel({ dark }: Props) {
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  const [feedStreams,    setFeedStreams]    = useState<string>('');
  const [productStreams, setProductStreams] = useState<string>('');
  const [note,           setNote]           = useState<string>('');
  const [targets,        setTargets]        = useState<DraftTarget[]>([{...EMPTY_TARGET}]);
  const [status,         setStatus]         = useState<any>(null);
  const [verification,   setVerification]   = useState<IntentVerification | null>(null);
  const [busy,           setBusy]           = useState<boolean>(false);
  const [error,          setError]          = useState<string | null>(null);

  const refreshStatus = useCallback(async () => {
    try { setStatus(await api.intentStatus()); } catch { setStatus(null); }
  }, []);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);

  const submit = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const built = targets.map(t => {
        const expected = parseFloat(t.expected);
        const tol      = t.tolerance ? parseFloat(t.tolerance) : undefined;
        const base: any = { kind: t.kind, expected };
        if (tol !== undefined && !isNaN(tol)) base.tolerance = tol;
        if (t.stream_tag)    base.stream_tag    = t.stream_tag;
        if (t.unit_tag)      base.unit_tag      = t.unit_tag;
        if (t.property_name) base.property_name = t.property_name;
        if (t.compound)      base.compound      = t.compound;
        return base;
      }).filter(t => !isNaN(t.expected));
      const r = await api.declareIntent({
        feed_streams:    feedStreams.split(',').map(s => s.trim()).filter(Boolean),
        product_streams: productStreams.split(',').map(s => s.trim()).filter(Boolean),
        note,
        targets: built,
      });
      if (!r?.success) setError(r?.error || 'declare_intent failed.');
      await refreshStatus();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }, [feedStreams, productStreams, note, targets, refreshStatus]);

  const runVerify = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const r: any = await api.intentVerify();
      if (r?.active === false) {
        setError('No intent active. Declare one first.');
        setVerification(null);
      } else {
        setVerification(r as IntentVerification);
      }
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }, []);

  const clearIntent = useCallback(async () => {
    setBusy(true);
    try { await api.intentClear(); setVerification(null); await refreshStatus(); }
    catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); }
  }, [refreshStatus]);

  const updateTarget = (i: number, patch: Partial<DraftTarget>) =>
    setTargets(prev => prev.map((t, idx) => idx === i ? { ...t, ...patch } : t));

  return (
    <div style={{height:'100%', overflow:'auto', background:bg, padding:'8px 10px'}}>
      <div style={{fontSize:11, color:dim, marginBottom:6, letterSpacing:0.6, fontWeight:700}}>
        DECLARE INTENT
      </div>

      <div style={{background:card, border:`1px solid ${brd}`, borderRadius:8, padding:'8px 10px', marginBottom:8}}>
        <label style={{fontSize:10, color:dim}}>Feed streams (comma-sep)</label>
        <input value={feedStreams} onChange={e => setFeedStreams(e.target.value)}
          placeholder="BIOGAS-IN, WATER-IN"
          style={inp(card, brd, txt)} />

        <label style={{fontSize:10, color:dim, marginTop:6, display:'block'}}>
          Product streams (comma-sep)
        </label>
        <input value={productStreams} onChange={e => setProductStreams(e.target.value)}
          placeholder="HYDROGEN"
          style={inp(card, brd, txt)} />

        <label style={{fontSize:10, color:dim, marginTop:6, display:'block'}}>
          Note (optional, shown in verification report)
        </label>
        <input value={note} onChange={e => setNote(e.target.value)}
          placeholder="Biogas-to-H2 SMR, target 99% H2 purity"
          style={inp(card, brd, txt)} />

        <div style={{marginTop:10, marginBottom:4, display:'flex', alignItems:'center'}}>
          <span style={{fontSize:11, fontWeight:700, color:txt}}>Targets</span>
          <button onClick={() => setTargets(prev => [...prev, {...EMPTY_TARGET}])}
            style={{marginLeft:'auto', background:'#0ea5e9', color:'#fff',
                    border:'none', borderRadius:5, padding:'2px 8px', fontSize:10,
                    cursor:'pointer'}}>+ Add</button>
        </div>

        {targets.map((t, i) => (
          <div key={i} style={{border:`1px solid ${brd}`, borderRadius:6, padding:'6px 8px',
                                 marginBottom:6, background: dark ? '#0f172a' : '#f1f5f9'}}>
            <div style={{display:'flex', gap:6, marginBottom:4}}>
              <select value={t.kind}
                onChange={e => updateTarget(i, { kind: e.target.value as TargetKind })}
                style={{background:card, color:txt, border:`1px solid ${brd}`, borderRadius:5,
                         fontSize:10, padding:'2px 4px', flex:'1 1 auto'}}>
                <option value="product_purity">product_purity</option>
                <option value="max_impurity">max_impurity</option>
                <option value="min_yield">min_yield</option>
                <option value="unit_setpoint">unit_setpoint</option>
              </select>
              <button onClick={() => setTargets(prev => prev.filter((_, idx) => idx !== i))}
                style={{background:'#dc2626', color:'#fff', border:'none', borderRadius:5,
                         fontSize:10, padding:'2px 8px', cursor:'pointer'}}>×</button>
            </div>

            {(t.kind === 'product_purity' || t.kind === 'max_impurity') && (
              <>
                <input value={t.stream_tag || ''}
                  onChange={e => updateTarget(i, { stream_tag: e.target.value })}
                  placeholder="stream tag (e.g. HYDROGEN)"
                  style={inp(card, brd, txt)} />
                <input value={t.compound || ''}
                  onChange={e => updateTarget(i, { compound: e.target.value })}
                  placeholder="compound (e.g. Hydrogen)"
                  style={inp(card, brd, txt)} />
              </>
            )}

            {t.kind === 'min_yield' && (
              <input value={t.compound || ''}
                onChange={e => updateTarget(i, { compound: e.target.value })}
                placeholder="compound (e.g. Hydrogen)"
                style={inp(card, brd, txt)} />
            )}

            {t.kind === 'unit_setpoint' && (
              <>
                <input value={t.unit_tag || ''}
                  onChange={e => updateTarget(i, { unit_tag: e.target.value })}
                  placeholder="unit tag (e.g. H-101)"
                  style={inp(card, brd, txt)} />
                <input value={t.property_name || ''}
                  onChange={e => updateTarget(i, { property_name: e.target.value })}
                  placeholder="property_name (e.g. OutletTemperature)"
                  style={inp(card, brd, txt)} />
              </>
            )}

            <div style={{display:'flex', gap:4, marginTop:4}}>
              <input value={t.expected}
                onChange={e => updateTarget(i, { expected: e.target.value })}
                placeholder="expected value"
                style={{...inp(card, brd, txt), flex:'1 1 auto'}} />
              {t.kind === 'unit_setpoint' && (
                <input value={t.tolerance || ''}
                  onChange={e => updateTarget(i, { tolerance: e.target.value })}
                  placeholder="tol"
                  style={{...inp(card, brd, txt), width:60}} />
              )}
            </div>
          </div>
        ))}

        <div style={{display:'flex', gap:6, marginTop:6}}>
          <button onClick={submit} disabled={busy}
            style={btn(busy ? '#475569' : '#16a34a')}>
            {busy ? '…' : 'Declare'}
          </button>
          <button onClick={runVerify} disabled={busy}
            style={btn(busy ? '#475569' : '#0ea5e9')}>
            Verify now
          </button>
          <button onClick={clearIntent} disabled={busy}
            style={btn(busy ? '#475569' : '#7c2d12')}>
            Clear
          </button>
        </div>
      </div>

      {/* Status */}
      {status?.active && (
        <div style={{background:card, border:`1px solid ${brd}`, borderRadius:8, padding:'6px 10px', marginBottom:6}}>
          <div style={{fontSize:10, color:dim, marginBottom:2}}>ACTIVE INTENT</div>
          <div style={{fontSize:11, color:txt}}>{status.intent?.note || '(no note)'}</div>
          <div style={{fontSize:10, color:dim, marginTop:2}}>
            feeds: {(status.intent?.feed_streams || []).join(', ') || '—'} ·
            products: {(status.intent?.product_streams || []).join(', ') || '—'} ·
            {' '}{(status.intent?.targets || []).length} targets
          </div>
        </div>
      )}

      {error && (
        <div style={{background:'#1c0a0a', color:'#f87171', padding:'6px 10px',
                     borderRadius:6, fontSize:11, marginBottom:8}}>
          ✕ {error}
        </div>
      )}

      {/* Verification result */}
      {verification && (
        <div style={{background:card, border:`1px solid ${brd}`, borderRadius:8, padding:'8px 10px'}}>
          <div style={{display:'flex', alignItems:'center', gap:6, marginBottom:6}}>
            <span style={{fontWeight:700, color:txt, fontSize:12}}>Verification</span>
            <span style={{
              fontSize:10, fontWeight:700, padding:'2px 8px', borderRadius:10,
              background: verification.passed ? '#16a34a' : '#dc2626', color:'#fff',
            }}>{verification.passed ? 'PASS' : 'FAIL'}</span>
          </div>
          <div style={{fontSize:11, color:dim, marginBottom:6}}>
            {verification.summary} · {verification.n_failed} failed, {verification.n_warnings} warn
          </div>
          {verification.findings.length === 0 && (
            <div style={{fontSize:11, color:'#86efac'}}>All declared targets met.</div>
          )}
          {verification.findings.map((f: IntentFinding, i: number) => (
            <div key={i} style={{
              borderLeft:`3px solid ${f.severity === 'error' ? '#ef4444'
                                     : f.severity === 'warning' ? '#f59e0b' : '#0ea5e9'}`,
              background: dark ? '#0f172a' : '#f1f5f9',
              padding:'4px 8px', marginTop:4, borderRadius:'0 4px 4px 0',
            }}>
              <div style={{fontSize:10, color:dim, marginBottom:2,
                            textTransform:'uppercase', letterSpacing:0.5}}>
                {f.severity} · {f.target}
              </div>
              <div style={{fontSize:11, color:txt}}>{f.message}</div>
              {f.repair_hint && (
                <div style={{fontSize:10, color:'#86efac', marginTop:3,
                              fontStyle:'italic'}}>→ {f.repair_hint}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function inp(card: string, brd: string, txt: string): React.CSSProperties {
  return {
    width: '100%', background: card, color: txt, border: `1px solid ${brd}`,
    borderRadius: 5, padding: '3px 6px', fontSize: 11, marginTop: 2,
  };
}

function btn(bg: string): React.CSSProperties {
  return {
    background: bg, color: '#fff', border: 'none', borderRadius: 5,
    padding: '4px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer', flex: '0 0 auto',
  };
}
