import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { ParametricData } from '../types';

const COLORS = ['#38bdf8', '#f59e0b', '#10b981', '#8b5cf6', '#f43f5e', '#a3e635', '#f97316'];

interface Props { data: ParametricData | null; dark?: boolean; }

export default function ParametricChart({ data, dark = true }: Props) {
  const DIM  = dark ? '#475569' : '#94a3b8';
  const BG   = dark ? '#0f172a' : '#f8fafc';
  const CBRD = dark ? '#1e293b' : '#f1f5f9';
  const TTBG = dark ? '#1e293b' : '#fff';
  const TT   = dark ? '#e2e8f0' : '#1e293b';

  if (!data) {
    return (
      <div style={{ padding: 32, color: DIM, fontSize: 13, textAlign: 'center', background: BG, height: '100%' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>📈</div>
        No parametric data yet.<br />
        <span style={{ fontSize: 12 }}>Ask the agent to run a parametric study.</span>
      </div>
    );
  }

  const cd = data.table.map(row => ({ input: row.input, ...row.outputs }));

  return (
    <div style={{ padding: 12, background: BG, height: '100%', overflowY: 'auto' }}>
      <div style={{ fontSize: 12, color: DIM, marginBottom: 8 }}>
        📈 Parametric Study — <strong style={{ color: '#38bdf8' }}>{data.input_label}</strong>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={cd} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CBRD} />
          <XAxis dataKey="input" stroke={DIM} tick={{ fontSize: 11, fill: DIM }} label={{ value: data.input_label, position: 'insideBottom', fill: DIM, fontSize: 10 }} />
          <YAxis stroke={DIM} tick={{ fontSize: 11, fill: DIM }} />
          <Tooltip contentStyle={{ background: TTBG, border: `1px solid ${CBRD}`, color: TT, fontSize: 12 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {data.output_labels.map((lbl, i) => (
            <Line key={lbl} type="monotone" dataKey={lbl}
              stroke={COLORS[i % COLORS.length]} strokeWidth={2}
              dot={{ r: 3, fill: COLORS[i % COLORS.length] }} />
          ))}
        </LineChart>
      </ResponsiveContainer>

      {/* Data table */}
      <div style={{ marginTop: 12, fontSize: 11, color: DIM, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ padding: '4px 8px', textAlign: 'left', borderBottom: `1px solid ${CBRD}` }}>{data.input_label}</th>
              {data.output_labels.map(l => (
                <th key={l} style={{ padding: '4px 8px', textAlign: 'left', borderBottom: `1px solid ${CBRD}` }}>{l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.table.map((row, i) => (
              <tr key={i}>
                <td style={{ padding: '3px 8px', fontFamily: 'monospace', borderBottom: `1px solid ${CBRD}` }}>{row.input}</td>
                {data.output_labels.map(l => (
                  <td key={l} style={{ padding: '3px 8px', fontFamily: 'monospace', borderBottom: `1px solid ${CBRD}` }}>
                    {row.outputs[l] != null ? Number(row.outputs[l]).toFixed(4) : '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
