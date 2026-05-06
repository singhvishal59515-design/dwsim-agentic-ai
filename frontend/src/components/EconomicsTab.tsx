import React, { useState } from 'react';
import { EconomicsResult } from '../types';
import { api } from '../utils/api';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';

interface Props {
  dark: boolean;
  fsName?: string;
}

function fmt(v: any, decimals = 2): string {
  if (v == null || isNaN(Number(v))) return '—';
  const n = Number(v);
  if (Math.abs(n) >= 1e6)  return `$${(n / 1e6).toFixed(decimals)}M`;
  if (Math.abs(n) >= 1e3)  return `$${(n / 1e3).toFixed(decimals)}K`;
  return `$${n.toFixed(decimals)}`;
}

export default function EconomicsTab({ dark, fsName }: Props) {
  const [result,  setResult]  = useState<EconomicsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState('');

  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  const estimate = async () => {
    setLoading(true); setError('');
    try {
      const defaults = await api.economicsDefaults();
      const res = await api.economicsEstimate({
        ...defaults,
        flowsheet_name: fsName || 'flowsheet',
      });
      setResult(res);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  if (!result) {
    return (
      <div style={{ padding: 24, background: bg, height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>💰</div>
        <div style={{ color: txt, fontWeight: 600, marginBottom: 8 }}>Economic Analysis</div>
        <div style={{ color: dim, fontSize: 12, marginBottom: 20, textAlign: 'center' }}>
          Estimates CapEx, OpEx, NPV, and payback period<br/>based on current simulation results.
        </div>
        {error && <div style={{ color: '#f87171', fontSize: 12, marginBottom: 12 }}>⚠ {error}</div>}
        <button
          onClick={estimate}
          disabled={loading}
          style={{
            background: loading ? '#334155' : '#0ea5e9', color: '#fff',
            border: 'none', borderRadius: 8, padding: '10px 24px',
            cursor: loading ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: 14,
          }}
        >
          {loading ? 'Estimating…' : '⚡ Estimate Economics'}
        </button>
      </div>
    );
  }

  const StatCard = ({ label, value, color }: { label: string; value: string; color: string }) => (
    <div style={{
      background: card, border: `1px solid ${brd}`, borderRadius: 8,
      padding: 12, textAlign: 'center', flex: 1,
    }}>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 11, color: dim, marginTop: 2 }}>{label}</div>
    </div>
  );

  const npvData = result.npv_rows?.map(r => ({ year: r.year, npv: r.cumulative_npv })) || [];
  const breakEven = result.payback_yr ? Math.round(result.payback_yr) : null;

  return (
    <div style={{ padding: 14, background: bg, height: '100%', overflowY: 'auto' }}>
      {/* Summary cards */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <StatCard label="CapEx" value={fmt(result.tcc)}              color="#f97316" />
        <StatCard label="Payback"  value={breakEven ? `${breakEven} yr` : '—'} color="#a3e635" />
      </div>

      {result.opex && (
        <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: 12, marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: dim, letterSpacing: 0.8, marginBottom: 8 }}>OPEX BREAKDOWN</div>
          {Object.entries(result.opex).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
              <span style={{ color: dim }}>{k.replace(/_/g, ' ')}</span>
              <span style={{ color: txt, fontFamily: 'monospace' }}>{fmt(v)}</span>
            </div>
          ))}
        </div>
      )}

      {/* NPV curve */}
      {npvData.length > 1 && (
        <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: dim, letterSpacing: 0.8, marginBottom: 8 }}>
            NPV CURVE
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={npvData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={dark ? '#1e293b' : '#e2e8f0'} />
              <XAxis dataKey="year" tick={{ fill: dim, fontSize: 10 }} label={{ value: 'Year', position: 'insideBottom', fill: dim, fontSize: 10 }} />
              <YAxis tickFormatter={v => fmt(v, 0)} tick={{ fill: dim, fontSize: 10 }} />
              <Tooltip
                formatter={(v: any) => [fmt(v), 'Cumulative NPV']}
                contentStyle={{ background: card, border: `1px solid ${brd}`, color: txt, fontSize: 11 }}
              />
              {breakEven && <ReferenceLine x={breakEven} stroke="#f97316" strokeDasharray="4 2" label={{ value: 'Payback', fill: '#f97316', fontSize: 10 }} />}
              <ReferenceLine y={0} stroke={dim} strokeDasharray="2 2" />
              <Line type="monotone" dataKey="npv" stroke="#38bdf8" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <button
        onClick={() => setResult(null)}
        style={{
          marginTop: 12, background: 'none', border: `1px solid ${brd}`,
          borderRadius: 6, color: dim, padding: '5px 12px', cursor: 'pointer',
          fontSize: 12, width: '100%',
        }}
      >
        ↺ Re-estimate
      </button>
    </div>
  );
}
