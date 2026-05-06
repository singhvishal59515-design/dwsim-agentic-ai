import React from 'react';

interface Props {
  svg:  string | null;
  name: string;
  dark?: boolean;
}

export default function FlowsheetDiagram({ svg, name, dark = true }: Props) {
  const DIM = dark ? '#334155' : '#94a3b8';
  const BG  = dark ? '#0f172a' : '#f8fafc';

  if (!svg) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', color: DIM, fontSize: 13,
        flexDirection: 'column', gap: 8,
      }}>
        <div style={{ fontSize: 32 }}>📐</div>
        <div>No diagram available</div>
        <div style={{ fontSize: 11, color: dark ? '#1e293b' : '#cbd5e1' }}>
          Load a flowsheet or ask the AI to build one
        </div>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', height: '100%', overflow: 'auto', background: BG, padding: 8 }}>
      <div style={{ fontSize: 11, color: DIM, marginBottom: 4 }}>{name}</div>
      <div dangerouslySetInnerHTML={{ __html: svg }} style={{ width: '100%' }} />
    </div>
  );
}
