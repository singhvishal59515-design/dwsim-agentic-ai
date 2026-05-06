import React from 'react';
import { StreamProps } from '../types';

type UnitSys = 'si' | 'imperial';

interface Props {
  results:  Record<string, StreamProps>;
  dark:     boolean;
  unitSys?: UnitSys;
}

function getTemp(s: StreamProps, u: UnitSys) {
  const c = s.temperature_C ?? (s.temperature_K != null ? s.temperature_K - 273.15 : null);
  return u === 'imperial' && c != null ? c * 9/5 + 32 : c;
}
function getPress(s: StreamProps, u: UnitSys) {
  const bar = s.pressure_bar ?? (s.pressure_Pa != null ? s.pressure_Pa / 1e5 : null);
  return u === 'imperial' && bar != null ? bar * 14.5038 : bar;
}
function getFlow(s: StreamProps, u: UnitSys) {
  const kgh = s.mass_flow_kgh ?? (s.mass_flow_kgs != null ? s.mass_flow_kgs * 3600 : null);
  return u === 'imperial' && kgh != null ? kgh * 2.20462 : kgh;
}

function getMolarFlow(s: StreamProps, u: UnitSys) {
  const kmolh = s.molar_flow_kmolh ?? null;
  // imperial: lbmol/h = kmol/h × 2.20462
  return u === 'imperial' && kmolh != null ? kmolh * 2.20462 : kmolh;
}

function fmt(v: number | null | undefined, dec = 4): string {
  if (v == null || isNaN(Number(v))) return '—';
  return Number(v).toFixed(dec);
}

// Unit labels per system
const LABELS: Record<UnitSys, Record<string, string>> = {
  si:       { T: 'T (°C)', P: 'P (bar)',   massFlow: 'Flow (kg/h)',   molarFlow: 'Flow (kmol/h)' },
  imperial: { T: 'T (°F)', P: 'P (psi)',   massFlow: 'Flow (lb/h)',   molarFlow: 'Flow (lbmol/h)' },
};

export default function StreamTable({ results, dark, unitSys = 'si' }: Props) {
  const names = Object.keys(results);

  const BG  = dark ? '#0f172a' : '#f8fafc';
  const HDR = dark ? '#1e293b' : '#f1f5f9';
  const BRD = dark ? '#334155' : '#e2e8f0';
  const DIM = dark ? '#64748b' : '#94a3b8';
  const TXT = dark ? '#e2e8f0' : '#1e293b';

  if (names.length === 0) {
    return (
      <div style={{ padding: 24, color: DIM, fontSize: 13, textAlign: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
        No results yet.<br />
        <span style={{ fontSize: 12 }}>Load a flowsheet and run the simulation.</span>
      </div>
    );
  }

  const compKeys = new Set<string>();
  names.forEach(n => Object.keys(results[n].mole_fractions || {}).forEach(c => compKeys.add(c)));

  const th: React.CSSProperties = {
    background: HDR, color: DIM, fontWeight: 700, fontSize: 10,
    letterSpacing: 0.6, padding: '6px 10px', textAlign: 'left',
    position: 'sticky', top: 0, whiteSpace: 'nowrap',
    borderBottom: `1px solid ${BRD}`,
  };
  const td: React.CSSProperties = {
    padding: '5px 10px', fontSize: 12, color: TXT,
    borderBottom: `1px solid ${dark ? '#1e293b' : '#f1f5f9'}`,
    fontFamily: 'monospace',
  };
  const tdProp: React.CSSProperties = { ...td, color: DIM, fontFamily: 'inherit', fontWeight: 600, fontSize: 11 };

  const L = LABELS[unitSys];
  const rows = [
    { label: L.T,         vals: names.map(n => fmt(getTemp(results[n], unitSys))) },
    { label: L.P,         vals: names.map(n => fmt(getPress(results[n], unitSys))) },
    { label: L.massFlow,  vals: names.map(n => fmt(getFlow(results[n], unitSys), 1)) },
    { label: L.molarFlow, vals: names.map(n => fmt(getMolarFlow(results[n], unitSys), 3)) },
    { label: 'VF',        vals: names.map(n => fmt(results[n].vapor_fraction)) },
  ];

  return (
    <div style={{ background: BG, height: '100%', overflowY: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={th}>Property</th>
            {names.map(n => <th key={n} style={th}>{n}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.label}>
              <td style={tdProp}>{r.label}</td>
              {r.vals.map((v, i) => <td key={i} style={td}>{v}</td>)}
            </tr>
          ))}
          {compKeys.size > 0 && (
            <tr>
              <td colSpan={names.length + 1} style={{ ...tdProp, background: HDR, paddingTop: 8, paddingBottom: 4, fontSize: 10, letterSpacing: 0.6 }}>
                MOLE FRACTIONS
              </td>
            </tr>
          )}
          {Array.from(compKeys).map(comp => (
            <tr key={comp}>
              <td style={{ ...tdProp, fontWeight: 400, fontFamily: 'monospace', fontSize: 11 }}>x({comp})</td>
              {names.map(n => (
                <td key={n} style={{ ...td, color: dark ? '#94a3b8' : '#64748b' }}>
                  {fmt(results[n]?.mole_fractions?.[comp])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
