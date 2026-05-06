import React, { useState } from 'react';
import { UnitOpData } from '../types';

const TYPE_COLORS: Record<string, string> = {
  Heater: '#fb923c', Cooler: '#fb923c', HeatExchanger: '#fb923c',
  ShellAndTubeHeatExchanger: '#fb923c',
  Mixer: '#a3e635', Splitter: '#a3e635',
  Pump: '#818cf8', Compressor: '#818cf8', Expander: '#818cf8',
  Valve: '#fbbf24', Pipe: '#94a3b8',
  Reactor_Conversion: '#f472b6', Reactor_Equilibrium: '#f472b6',
  Reactor_Gibbs: '#f472b6', Reactor_CSTR: '#f472b6', Reactor_PFR: '#f472b6',
  DistillationColumn: '#c084fc', AbsorptionColumn: '#c084fc', ShortcutColumn: '#c084fc',
  Separator: '#22d3ee', ComponentSeparator: '#22d3ee',
  Tank: '#94a3b8', Filter: '#94a3b8',
};

function fmt(v: any): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return isNaN(v) ? '—' : v.toFixed(4);
  return String(v);
}

interface CardProps {
  tag:  string;
  data: UnitOpData;
  dark: boolean;
}

function UOCard({ tag, data, dark }: CardProps) {
  const [open, setOpen] = useState(false);
  const color = TYPE_COLORS[data.type] || '#94a3b8';
  const card  = dark ? '#1e293b' : '#fff';
  const brd   = dark ? '#334155' : '#e2e8f0';
  const dim   = dark ? '#64748b' : '#94a3b8';
  const txt   = dark ? '#e2e8f0' : '#1e293b';

  const props = Object.entries(data.summary || {})
    .filter(([k, v]) => v !== null && v !== undefined && k !== 'tag' && k !== 'type');

  return (
    <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, marginBottom: 8, overflow: 'hidden' }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '8px 12px', cursor: 'pointer', borderLeft: `3px solid ${color}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14 }}>{open ? '▼' : '▶'}</span>
          <span style={{ fontFamily: 'monospace', fontWeight: 600, color: txt, fontSize: 13 }}>{tag}</span>
        </div>
        <span style={{
          background: color + '33', color, borderRadius: 4,
          padding: '1px 7px', fontSize: 10, fontWeight: 600,
        }}>{data.type}</span>
      </div>
      {open && (
        <div style={{ padding: '8px 12px', borderTop: `1px solid ${brd}` }}>
          {props.length === 0 ? (
            <div style={{ color: dim, fontSize: 12 }}>No properties available</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <tbody>
                {props.map(([k, v]) => (
                  <tr key={k}>
                    <td style={{ color: dim, padding: '2px 0', paddingRight: 12, width: '50%' }}>{k}</td>
                    <td style={{ color: txt, fontFamily: 'monospace' }}>{fmt(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

interface Props {
  unitOps: Record<string, UnitOpData>;
  dark:    boolean;
}

export default function UnitOpsTab({ unitOps, dark }: Props) {
  const bg  = dark ? '#0f172a' : '#f8fafc';
  const dim = dark ? '#64748b' : '#94a3b8';
  const tags = Object.keys(unitOps);

  if (tags.length === 0) {
    return (
      <div style={{ padding: 24, color: dim, fontSize: 13, textAlign: 'center' }}>
        No unit operations in flowsheet.
      </div>
    );
  }

  return (
    <div style={{ padding: 12, background: bg, height: '100%', overflowY: 'auto' }}>
      {tags.map(tag => (
        <UOCard key={tag} tag={tag} data={unitOps[tag]} dark={dark} />
      ))}
    </div>
  );
}
