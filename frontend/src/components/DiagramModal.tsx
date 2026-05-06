import React, { useEffect, useRef, useCallback, useState } from 'react';
import { DiagramData, DiagramNode, DiagramConnection } from '../types';

const TYPE_COLORS: Record<string, string> = {
  MaterialStream: '#60a5fa', EnergyStream: '#f87171',
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

interface LayoutNode extends DiagramNode {
  dx: number;
  dy: number;
}

function layoutNodes(nodes: DiagramNode[], W: number, H: number): LayoutNode[] {
  const unitOps    = nodes.filter(n => n.category === 'unit_op');
  const matStreams  = nodes.filter(n => n.category === 'stream');
  const enStreams   = nodes.filter(n => n.category === 'energy');
  const pad = 60;
  const uw  = W - pad * 2;

  const laid: LayoutNode[] = [];

  unitOps.forEach((n, i) => laid.push({ ...n,
    dx: pad + (uw / (unitOps.length + 1))   * (i + 1),
    dy: H * 0.5,
  }));
  matStreams.forEach((n, i) => laid.push({ ...n,
    dx: pad + (uw / (matStreams.length + 1)) * (i + 1),
    dy: H * 0.22,
  }));
  enStreams.forEach((n, i) => laid.push({ ...n,
    dx: pad + (uw / (enStreams.length + 1))  * (i + 1),
    dy: H * 0.8,
  }));

  return laid;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawArrow(ctx: CanvasRenderingContext2D, fx: number, fy: number, tx: number, ty: number) {
  const angle = Math.atan2(ty - fy, tx - fx);
  const len = 9;
  ctx.beginPath();
  ctx.moveTo(tx, ty);
  ctx.lineTo(tx - len * Math.cos(angle - 0.4), ty - len * Math.sin(angle - 0.4));
  ctx.moveTo(tx, ty);
  ctx.lineTo(tx - len * Math.cos(angle + 0.4), ty - len * Math.sin(angle + 0.4));
  ctx.stroke();
}

function drawDiagram(
  canvas: HTMLCanvasElement,
  data: DiagramData,
  zoom: number,
  pan: { x: number; y: number },
) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = canvas.clientWidth  * dpr;
  canvas.height = canvas.clientHeight * dpr;
  const ctx = canvas.getContext('2d')!;
  ctx.scale(dpr, dpr);

  const W = canvas.clientWidth;
  const H = canvas.clientHeight;

  ctx.fillStyle = '#0a0f1e';
  ctx.fillRect(0, 0, W, H);

  if (!data?.nodes?.length) {
    ctx.fillStyle = '#475569';
    ctx.font = '13px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No flowsheet loaded', W / 2, H / 2);
    return;
  }

  ctx.save();
  ctx.translate(W / 2 + pan.x, H / 2 + pan.y);
  ctx.scale(zoom, zoom);
  ctx.translate(-W / 2, -H / 2);

  const nodes = layoutNodes(data.nodes, W, H);
  const nodeMap = Object.fromEntries(nodes.map(n => [n.id, n]));

  // Draw connections
  ctx.strokeStyle = '#475569';
  ctx.lineWidth = 1.5;
  for (const conn of (data.connections || [])) {
    const from = nodeMap[conn.from];
    const to   = nodeMap[conn.to];
    if (!from || !to) continue;
    const mx = (from.dx + to.dx) / 2;
    const my = (from.dy + to.dy) / 2 - 25;
    ctx.beginPath();
    ctx.moveTo(from.dx, from.dy);
    ctx.quadraticCurveTo(mx, my, to.dx, to.dy);
    ctx.stroke();
    drawArrow(ctx, mx, my, to.dx, to.dy);
  }

  // Draw nodes
  for (const node of nodes) {
    const color   = TYPE_COLORS[node.type] || '#94a3b8';
    const isUnit  = node.category === 'unit_op';
    const nw = isUnit ? 100 : 90;
    const nh = isUnit ?  44 : 36;

    ctx.shadowColor  = 'rgba(0,0,0,0.4)';
    ctx.shadowBlur   = 8;
    ctx.shadowOffsetY = 2;

    ctx.fillStyle   = color;
    ctx.globalAlpha = 0.9;
    roundRect(ctx, node.dx - nw / 2, node.dy - nh / 2, nw, nh, 8);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.shadowBlur  = 0;

    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth   = 1.2;
    roundRect(ctx, node.dx - nw / 2, node.dy - nh / 2, nw, nh, 8);
    ctx.stroke();

    ctx.fillStyle    = '#0a0f1e';
    ctx.font         = `bold ${isUnit ? 9 : 8}px Inter, sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    const label = node.id.length > 14 ? node.id.slice(0, 13) + '…' : node.id;
    ctx.fillText(label, node.dx, node.dy);

    ctx.fillStyle = '#22c55e';
    ctx.beginPath();
    ctx.arc(node.dx + nw / 2 - 6, node.dy - nh / 2 + 6, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // Title
  ctx.fillStyle    = '#e2e8f0';
  ctx.font         = 'bold 12px Inter, sans-serif';
  ctx.textAlign    = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(data.name || 'Flowsheet', 12, 10);

  ctx.restore();
}

interface Props {
  data:     DiagramData | null;
  svgText?: string;
  onClose:  () => void;
}

export default function DiagramModal({ data, svgText, onClose }: Props) {
  const canvasRef  = useRef<HTMLCanvasElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan,  setPan]  = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;
    drawDiagram(canvas, data, zoom, pan);
  }, [data, zoom, pan]);

  useEffect(() => { redraw(); }, [redraw]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape')           onClose();
      if (e.key === '+' || e.key === '=') setZoom(z => Math.min(z * 1.2, 5));
      if (e.key === '-')                  setZoom(z => Math.max(z / 1.2, 0.1));
      if (e.key === 'f' || e.key === 'F') { setZoom(1); setPan({ x: 0, y: 0 }); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom(z => Math.max(0.1, Math.min(5, z * (e.deltaY < 0 ? 1.1 : 0.9))));
  };

  const onMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current) return;
    setPan({
      x: dragRef.current.panX + (e.clientX - dragRef.current.startX),
      y: dragRef.current.panY + (e.clientY - dragRef.current.startY),
    });
  };
  const onMouseUp = () => { dragRef.current = null; };

  // Fallback: show SVG if available
  const showCanvas = !svgText && data?.nodes?.length;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: 'rgba(0,0,0,0.85)', display: 'flex', flexDirection: 'column',
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 16px', background: '#1e293b', flexShrink: 0,
      }}>
        <span style={{ fontWeight: 700, color: '#38bdf8', marginRight: 'auto' }}>
          📐 Flowsheet Diagram — {data?.name || ''}
        </span>
        <button onClick={() => setZoom(z => Math.min(z * 1.2, 5))} style={btnStyle}>+</button>
        <button onClick={() => setZoom(z => Math.max(z / 1.2, 0.1))} style={btnStyle}>−</button>
        <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }} style={btnStyle}>Fit</button>
        <span style={{ color: '#64748b', fontSize: 12 }}>{Math.round(zoom * 100)}%</span>
        <span style={{ color: '#334155', fontSize: 12, marginLeft: 8 }}>
          Scroll=zoom · Drag=pan · Esc=close
        </span>
        <button onClick={onClose} style={{ ...btnStyle, marginLeft: 8, background: '#7f1d1d', color: '#fca5a5' }}>✕</button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {svgText ? (
          /* SVG mode */
          <div
            style={{
              width: '100%', height: '100%', overflow: 'auto',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onWheel={onWheel as any}
          >
            <div
              style={{ transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`, transformOrigin: 'center', cursor: 'grab' }}
              onMouseDown={onMouseDown}
              onMouseMove={onMouseMove}
              onMouseUp={onMouseUp}
              dangerouslySetInnerHTML={{ __html: svgText }}
            />
          </div>
        ) : showCanvas ? (
          /* Canvas P&ID mode */
          <canvas
            ref={canvasRef}
            style={{ width: '100%', height: '100%', cursor: dragRef.current ? 'grabbing' : 'grab', display: 'block' }}
            onWheel={onWheel}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
          />
        ) : (
          <div style={{ color: '#475569', fontSize: 14, textAlign: 'center', marginTop: 80 }}>
            No flowsheet diagram available.<br />
            <span style={{ fontSize: 12 }}>Load a flowsheet first.</span>
          </div>
        )}
      </div>

      {/* Legend */}
      {showCanvas && (
        <div style={{
          display: 'flex', gap: 12, flexWrap: 'wrap',
          padding: '6px 16px', background: '#0a0f1e', flexShrink: 0, fontSize: 11,
        }}>
          {[
            ['MaterialStream', 'Stream'],
            ['Heater', 'Heat'],
            ['Pump', 'Pump/Comp'],
            ['DistillationColumn', 'Column'],
            ['Separator', 'Separator'],
            ['Mixer', 'Mixer'],
          ].map(([type, label]) => (
            <span key={type} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 10, height: 10, background: TYPE_COLORS[type] || '#94a3b8', borderRadius: 2, display: 'inline-block' }} />
              <span style={{ color: '#64748b' }}>{label}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: '#334155', color: '#e2e8f0', border: 'none',
  borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
};
