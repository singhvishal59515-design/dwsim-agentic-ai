import React, { useState, useCallback, useEffect, useRef } from 'react';
import ChatPanel        from './components/ChatPanel';
import StreamTable      from './components/StreamTable';
import FlowsheetPanel   from './components/FlowsheetPanel';
import FlowsheetBrowser from './components/FlowsheetBrowser';
import FlowsheetDiagram from './components/FlowsheetDiagram';
import LLMSelector      from './components/LLMSelector';
import SafetyBadge      from './components/SafetyBadge';
import ParametricChart  from './components/ParametricChart';
import ConvergenceTab   from './components/ConvergenceTab';
import UnitOpsTab       from './components/UnitOpsTab';
import ReportsTab       from './components/ReportsTab';
import EconomicsTab     from './components/EconomicsTab';
import DiagramModal     from './components/DiagramModal';
import TemplatePanel    from './components/TemplatePanel';
import CompoundSearch   from './components/CompoundSearch';
import SafetyPanel         from './components/SafetyPanel';
import PinchChart          from './components/PinchChart';
import FlowsheetComparison from './components/FlowsheetComparison';
import MonteCarloTab       from './components/MonteCarloTab';
import BayesianOptTab      from './components/BayesianOptTab';
import AccuracyTab         from './components/AccuracyTab';
import DiagnosticsPanel    from './components/DiagnosticsPanel';
import MemoryPanel         from './components/MemoryPanel';
import AblationPanel       from './components/AblationPanel';
import JudgeDashboard      from './components/JudgeDashboard';
import { useChat }         from './hooks/useChat';
import { useSimulation }   from './hooks/useSimulation';
import { ParametricData, ReportCard, DiagramData, StreamProps } from './types';

type RightTab = 'streams' | 'convergence' | 'unitops' | 'reports' | 'parametric' | 'montecarlo' | 'bayesian' | 'economics' | 'diagram' | 'pinch' | 'compare' | 'accuracy';
type LeftTab  = 'browser' | 'controls' | 'templates' | 'compounds' | 'safety' | 'memory' | 'judge';
type UnitSys  = 'si' | 'imperial';

// ── colour helpers ─────────────────────────────────────────────────────────────

function c(dark: boolean, d: string, l: string) { return dark ? d : l; }

// ── sub-components ────────────────────────────────────────────────────────────

function TabBtn({
  label, active, onClick, badge, dark,
}: { label: string; active: boolean; onClick: () => void; badge?: number; dark: boolean }) {
  return (
    <button onClick={onClick} style={{
      background: 'none', border: 'none', padding: '4px 8px', fontSize: 11,
      cursor: 'pointer', whiteSpace: 'nowrap',
      color:       active ? (dark ? '#e2e8f0' : '#1e293b') : (dark ? '#64748b' : '#94a3b8'),
      fontWeight:  active ? 600 : 400,
      borderBottom: active
        ? '2px solid #0ea5e9'
        : '2px solid transparent',
      position: 'relative',
    }}>
      {label}
      {badge != null && badge > 0 && (
        <span style={{
          position: 'absolute', top: 0, right: 0,
          background: '#dc2626', color: '#fff',
          borderRadius: '50%', fontSize: 9, fontWeight: 700,
          width: 14, height: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
          transform: 'translate(4px,-4px)',
        }}>{badge > 9 ? '9+' : badge}</span>
      )}
    </button>
  );
}

// ── main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [dark,        setDark]        = useState<boolean>(() => {
    try { return localStorage.getItem('dwsim_theme') !== 'light'; } catch { return true; }
  });
  const [unitSys,     setUnitSys]     = useState<UnitSys>('si');
  const [rightTab,    setRightTab]    = useState<RightTab>('streams');
  const [leftTab,     setLeftTab]     = useState<LeftTab>('browser');
  const [parametric,  setParametric]  = useState<ParametricData | null>(null);
  const [reports,     setReports]     = useState<ReportCard[]>([]);
  const [diagramOpen, setDiagramOpen] = useState(false);
  const [showHelp,    setShowHelp]    = useState(false);
  const chatInputRef  = useRef<HTMLTextAreaElement>(null);

  const sim = useSimulation();
  // Destructure stable function refs so useCallback deps don't change every render
  const { refresh: simRefresh, loadFlowsheet, runSim, saveSim, switchSheet, setError: simSetError } = sim;

  const handleStreamResults = useCallback((_sr: Record<string, StreamProps>) => {
    simRefresh();
  }, [simRefresh]);

  const handleToolResult = useCallback((name: string, result: any) => {
    if (['save_and_solve','run_simulation','load_flowsheet','new_flowsheet'].includes(name)) {
      simRefresh();
    }
    if (name === 'parametric_study' && result?.success && result.table) {
      setParametric({
        input_label:   result.input_label   || 'Input',
        output_labels: result.output_labels || [],
        table:         result.table,
      });
      setRightTab('parametric');
    }
    if (name === 'generate_report' && result?.success && result.pdf_path) {
      setReports(prev => [{
        title:       result.title       || 'Report',
        pdf_path:    result.pdf_path,
        timestamp:   new Date().toLocaleString(),
        data_points: result.data_points,
        plot_count:  (result.plot_paths || []).length,
        sections:    result.sections_present,
      }, ...prev]);
      setRightTab('reports');
    }
  }, [sim]);

  const chat = useChat(handleToolResult, handleStreamResults);

  // Theme persistence
  useEffect(() => {
    try { localStorage.setItem('dwsim_theme', dark ? 'dark' : 'light'); } catch { /* ignore */ }
    document.body.style.background = dark ? '#0f172a' : '#f8fafc';
    document.body.style.color      = dark ? '#e2e8f0' : '#1e293b';
  }, [dark]);

  // Global keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        if (e.key === 'Escape') (e.target as any).blur?.();
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        switch (e.key) {
          case 'k': e.preventDefault(); chatInputRef.current?.focus(); break;
          case 's': e.preventDefault(); saveSim().catch(()=>{}); break;
          case 'l': e.preventDefault(); chat.reset(); break;
          case 'z': e.preventDefault(); chat.undo().catch(()=>{}); break;
          default:  break;
        }
      }
      if (e.key === '?' && !e.ctrlKey) setShowHelp(h => !h);
      if (e.key === 'Escape')          setDiagramOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chat, sim]);

  // ── colours ──────────────────────────────────────────────────────────────────
  const BG   = c(dark, '#0f172a', '#f8fafc');
  const HDR  = c(dark, '#1e293b', '#ffffff');
  const PNL  = c(dark, '#1e293b', '#ffffff');
  const BRD  = c(dark, '#334155', '#e2e8f0');
  const DIM  = c(dark, '#64748b', '#94a3b8');
  const TXT  = c(dark, '#e2e8f0', '#1e293b');

  const safetyBadgeCount = chat.lastSafety?.warnings?.length || 0;

  // ── diagram data extraction ──────────────────────────────────────────────────
  const diagramData: DiagramData | null = sim.diagram?.nodes
    ? sim.diagram as DiagramData
    : sim.state
      ? {
          name: sim.state.name,
          nodes: [
            ...sim.state.streams.map(s => ({
              id: s, type: (sim.state!.object_types[s] || 'MaterialStream'), category: 'stream',
            })),
            ...sim.state.unit_ops.map(u => ({
              id: u, type: (sim.state!.object_types[u] || 'Heater'), category: 'unit_op',
            })),
          ],
          connections: [],
        }
      : null;

  const svgText: string | undefined = sim.diagram?.svg ?? undefined;

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:BG, color:TXT,
                  fontFamily:"'Segoe UI',system-ui,sans-serif", overflow:'hidden' }}>

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div style={{
        display:'flex', alignItems:'center', padding:'6px 14px',
        background:HDR, borderBottom:`1px solid ${BRD}`, gap:8, flexShrink:0,
        position:'relative', zIndex:100, flexWrap:'wrap',
      }}>
        <span style={{fontSize:16,fontWeight:700,color:'#38bdf8',letterSpacing:.3}}>DWSIM Agentic AI</span>
        <span style={{fontSize:10,color:DIM}}>v2 · React</span>

        {sim.state?.name && (
          <span style={{fontSize:11,color:'#94a3b8',marginLeft:4}}>
            📄 {sim.state.name}
          </span>
        )}
        {sim.state?.property_package && (
          <span style={{fontSize:11,color:'#a3e635',marginLeft:2}}>
            ⚗ {sim.state.property_package}
          </span>
        )}

        {/* Multi-flowsheet tabs */}
        {sim.loadedSheets.length > 1 && (
          <div style={{display:'flex',gap:4,marginLeft:8}}>
            {sim.loadedSheets.map(alias => (
              <button
                key={alias}
                onClick={() => switchSheet(alias)}
                style={{
                  background: alias === sim.state?.name ? '#0ea5e9' : '#1e293b',
                  color: alias === sim.state?.name ? '#fff' : DIM,
                  border:`1px solid ${BRD}`, borderRadius:5,
                  padding:'2px 8px', cursor:'pointer', fontSize:11,
                }}
              >{alias}</button>
            ))}
          </div>
        )}

        <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:8}}>
          {/* Undo */}
          {chat.canUndo && (
            <button
              onClick={() => chat.undo()}
              title="Undo last property change (Ctrl+Z)"
              style={{ background:'#334155', color:'#e2e8f0', border:'none', borderRadius:6, padding:'3px 8px', cursor:'pointer', fontSize:12 }}
            >↩</button>
          )}

          {/* Unit system toggle */}
          <div style={{display:'flex',border:`1px solid ${BRD}`,borderRadius:6,overflow:'hidden'}}>
            {(['si','imperial'] as UnitSys[]).map(u => (
              <button key={u} onClick={() => setUnitSys(u)} style={{
                background: unitSys===u ? '#0ea5e9' : HDR, color: unitSys===u ? '#fff' : DIM,
                border:'none', padding:'3px 8px', cursor:'pointer', fontSize:11, fontWeight:600,
              }}>{u.toUpperCase()}</button>
            ))}
          </div>

          {/* Theme toggle */}
          <button
            onClick={() => setDark(d => !d)}
            title="Toggle dark/light theme"
            style={{ background:'none', border:`1px solid ${BRD}`, borderRadius:6, padding:'3px 8px', cursor:'pointer', fontSize:14, color:TXT }}
          >{dark ? '☀' : '🌙'}</button>

          {/* Keyboard help */}
          <button
            onClick={() => setShowHelp(h => !h)}
            title="Keyboard shortcuts (?)"
            style={{ background:'none', border:`1px solid ${BRD}`, borderRadius:6, padding:'3px 8px', cursor:'pointer', fontSize:11, color:DIM }}
          >?</button>

          <LLMSelector dark={dark} />
        </div>
      </div>

      {/* Safety badge */}
      {chat.lastSafety && (chat.lastSafety.warnings.length > 0 || chat.lastSafety.status !== 'PASSED') && (
        <SafetyBadge status={chat.lastSafety.status} warnings={chat.lastSafety.warnings} />
      )}

      {/* Feed warnings */}
      {sim.feedWarnings.length > 0 && (
        <div style={{background:'#451a03',borderBottom:`1px solid #92400e`,padding:'4px 16px',fontSize:11,color:'#fbbf24',flexShrink:0}}>
          ⚠ Feed warnings: {sim.feedWarnings.join(' · ')}
        </div>
      )}

      {/* Simulation error */}
      {sim.error && (
        <div style={{background:'#1c0a0a',borderBottom:`1px solid #7f1d1d`,padding:'4px 16px',fontSize:11,color:'#f87171',flexShrink:0,cursor:'pointer'}}
          onClick={() => simSetError(null)}>
          ✕ {sim.error}
        </div>
      )}

      {/* ── Body ─────────────────────────────────────────────────────────────── */}
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>

        {/* ── Left panel ─────────────────────────────────────────────────────── */}
        <div style={{ width:260, flexShrink:0, borderRight:`1px solid ${BRD}`, display:'flex', flexDirection:'column', background:PNL }}>
          {/* Left tab bar */}
          <div style={{display:'flex',borderBottom:`1px solid ${BRD}`,background:BG,flexShrink:0,overflowX:'auto'}}>
            {([
              ['browser',   'Files'],
              ['controls',  'Controls'],
              ['templates', '⚡'],
              ['compounds', '⚗'],
              ['memory',    '🧠'],
              ['safety',    '🛡'],
              ['judge',     '⚖️'],
            ] as [LeftTab, string][]).map(([key, label]) => (
              <TabBtn key={key} label={label} active={leftTab===key} dark={dark} onClick={()=>setLeftTab(key)} />
            ))}
          </div>
          <div style={{flex:1,overflow:'auto'}}>
            {leftTab==='browser' && (
              <FlowsheetBrowser
                dark={dark}
                onLoaded={simRefresh}
                currentFlowsheet={sim.state?.name}
              />
            )}
            {leftTab==='controls' && (
              <div style={{height:'100%',overflowY:'auto'}}>
                <FlowsheetPanel
                  dark={dark}
                  state={sim.state}
                  onLoad={loadFlowsheet}
                  onRun={runSim}
                  onSave={saveSim}
                  simLoading={sim.loading}
                />
                <DiagnosticsPanel dark={dark} />
                <AblationPanel dark={dark} />
              </div>
            )}
            {leftTab==='templates' && (
              <TemplatePanel
                dark={dark}
                onUse={(prompt) => {
                  chatInputRef.current?.focus();
                  chat.sendMessage(prompt);
                }}
              />
            )}
            {leftTab==='compounds' && (
              <CompoundSearch
                dark={dark}
                onPaste={(text) => {
                  chatInputRef.current?.focus();
                  const el = chatInputRef.current;
                  if (el) {
                    const v = el.value;
                    const s = el.selectionStart || v.length;
                    el.value = v.slice(0, s) + text + v.slice(s);
                    el.selectionStart = el.selectionEnd = s + text.length;
                  }
                }}
              />
            )}
            {leftTab==='memory' && (
              <MemoryPanel dark={dark} />
            )}
            {leftTab==='safety' && (
              <SafetyPanel dark={dark} />
            )}
            {leftTab==='judge' && (
              <JudgeDashboard dark={dark} />
            )}
          </div>
        </div>

        {/* ── Centre: chat ──────────────────────────────────────────────────── */}
        <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minWidth:0 }}>
          <ChatPanel
            dark={dark}
            messages={chat.messages}
            loading={chat.loading}
            streamingText={chat.streamingText}
            onSend={chat.sendMessage}
            onReset={chat.reset}
            onExport={chat.exportChat}
            inputRef={chatInputRef}
          />
        </div>

        {/* ── Right: results panels ─────────────────────────────────────────── */}
        <div style={{ width:400, flexShrink:0, borderLeft:`1px solid ${BRD}`, display:'flex', flexDirection:'column', background:PNL }}>

          {/* Right tab bar */}
          <div style={{display:'flex',borderBottom:`1px solid ${BRD}`,padding:'0 4px',background:BG,flexShrink:0,overflowX:'auto'}}>
            {([
              ['streams',     'Streams'],
              ['convergence', 'Conv.'],
              ['unitops',     'Ops'],
              ['reports',     'Reports'],
              ['parametric',  'Param'],
              ['montecarlo',  '🎲 MC'],
              ['bayesian',    '🔬 BO'],
              ['economics',   '💰'],
              ['pinch',       '🌡'],
              ['compare',     '⚖'],
              ['diagram',     '📐'],
              ['accuracy',    '🎯'],
            ] as [RightTab, string][]).map(([key, label]) => (
              <TabBtn
                key={key} label={label} dark={dark} active={rightTab===key}
                onClick={() => { setRightTab(key); if (key==='diagram') setDiagramOpen(true); }}
                badge={key==='reports' ? reports.length : key==='convergence' && sim.convergence && !sim.convergence.all_converged ? sim.convergence.not_converged.length : undefined}
              />
            ))}
          </div>

          {/* Unit system bar (only for streams) */}
          {rightTab === 'streams' && (
            <div style={{display:'flex',gap:4,padding:'4px 8px',borderBottom:`1px solid ${BRD}`,background:BG,flexShrink:0}}>
              {(['si','imperial'] as UnitSys[]).map(u => (
                <button key={u} onClick={() => setUnitSys(u)} style={{
                  background: unitSys===u ? '#0ea5e9' : 'none',
                  color: unitSys===u ? '#fff' : DIM,
                  border:`1px solid ${unitSys===u ? '#0ea5e9' : BRD}`,
                  borderRadius:5, padding:'2px 8px', cursor:'pointer', fontSize:11, fontWeight:600,
                }}>{u.toUpperCase()}</button>
              ))}
              <span style={{marginLeft:'auto',color:DIM,fontSize:11,alignSelf:'center'}}>
                {Object.keys(sim.results).length > 0 || Object.keys(chat.liveResults).length > 0
                  ? `${Object.keys(chat.liveResults).length || Object.keys(sim.results).length} streams`
                  : ''}
              </span>
              <a href={`/results/export/excel`} download style={{textDecoration:'none'}}>
                <button style={{background:'none',border:`1px solid ${BRD}`,borderRadius:5,color:'#86efac',padding:'2px 8px',cursor:'pointer',fontSize:11}}>⬇ XLS</button>
              </a>
              <a href={`/results/export/csv`} download style={{textDecoration:'none'}}>
                <button style={{background:'none',border:`1px solid ${BRD}`,borderRadius:5,color:DIM,padding:'2px 8px',cursor:'pointer',fontSize:11}}>⬇ CSV</button>
              </a>
            </div>
          )}

          {/* Tab content */}
          <div style={{ flex:1, overflow:'auto' }}>
            {rightTab==='streams' && (
              <StreamTable
                dark={dark}
                unitSys={unitSys}
                results={Object.keys(chat.liveResults).length > 0 ? chat.liveResults : sim.results}
              />
            )}
            {rightTab==='convergence' && (
              <ConvergenceTab data={sim.convergence} dark={dark} />
            )}
            {rightTab==='unitops' && (
              <UnitOpsTab unitOps={sim.unitOps} dark={dark} />
            )}
            {rightTab==='reports' && (
              <ReportsTab reports={reports} dark={dark} />
            )}
            {rightTab==='parametric' && (
              <ParametricChart data={parametric} dark={dark} />
            )}
            {rightTab==='montecarlo' && (
              <MonteCarloTab dark={dark} />
            )}
            {rightTab==='bayesian' && (
              <BayesianOptTab dark={dark} />
            )}
            {rightTab==='economics' && (
              <EconomicsTab dark={dark} fsName={sim.state?.name} />
            )}
            {rightTab==='pinch' && (
              <PinchChart dark={dark} />
            )}
            {rightTab==='compare' && (
              <FlowsheetComparison dark={dark} />
            )}
            {rightTab==='accuracy' && (
              <AccuracyTab dark={dark} />
            )}
            {rightTab==='diagram' && (
              <div style={{padding:16,color:DIM,fontSize:12,textAlign:'center'}}>
                <button
                  onClick={() => setDiagramOpen(true)}
                  style={{background:'#0ea5e9',color:'#fff',border:'none',borderRadius:8,padding:'10px 20px',cursor:'pointer',fontWeight:700,fontSize:14}}
                >
                  📐 Open Full-Screen Diagram
                </button>
                <div style={{marginTop:8,fontSize:11,color:DIM}}>
                  {sim.state?.name ? `${sim.state.streams.length} streams · ${sim.state.unit_ops.length} unit ops` : 'Load a flowsheet first'}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Full-screen Diagram Modal ───────────────────────────────────────── */}
      {diagramOpen && (
        <DiagramModal
          data={diagramData}
          svgText={svgText}
          onClose={() => setDiagramOpen(false)}
        />
      )}

      {/* ── Keyboard help modal ─────────────────────────────────────────────── */}
      {showHelp && (
        <div
          style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.7)',zIndex:9998,display:'flex',alignItems:'center',justifyContent:'center'}}
          onClick={() => setShowHelp(false)}
        >
          <div style={{background:PNL,border:`1px solid ${BRD}`,borderRadius:12,padding:24,minWidth:320,maxWidth:420}} onClick={e=>e.stopPropagation()}>
            <div style={{fontWeight:700,color:TXT,marginBottom:14,fontSize:15}}>⌨ Keyboard Shortcuts</div>
            {[
              ['Ctrl+K',     'Focus chat input'],
              ['Ctrl+S',     'Save flowsheet'],
              ['Ctrl+L',     'Reset chat'],
              ['Ctrl+Z',     'Undo last property change'],
              ['Enter',      'Send message'],
              ['Shift+Enter','New line in chat'],
              ['?',          'Toggle this help'],
              ['Esc',        'Close modal / blur input'],
            ].map(([k, v]) => (
              <div key={k} style={{display:'flex',justifyContent:'space-between',padding:'4px 0',borderBottom:`1px solid ${BRD}`,fontSize:13}}>
                <span style={{fontFamily:'monospace',color:'#38bdf8'}}>{k}</span>
                <span style={{color:DIM}}>{v}</span>
              </div>
            ))}
            <button onClick={()=>setShowHelp(false)} style={{marginTop:14,width:'100%',background:'#0ea5e9',color:'#fff',border:'none',borderRadius:6,padding:'7px',cursor:'pointer',fontWeight:600}}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
