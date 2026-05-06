import React, { useRef, useEffect, useState } from 'react';
import { ChatMessage } from '../types';

function renderMd(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/```(\w*)\n([\s\S]*?)```/g,
      '<pre style="background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px;font-size:11px;overflow-x:auto;margin:6px 0;color:#a3e635">$2</pre>')
    .replace(/\*\*(.+?)\*\*/g, '<strong style="color:#f8fafc">$1</strong>')
    .replace(/`([^`]+)`/g, '<code style="background:#1e293b;padding:1px 4px;border-radius:3px;font-size:.9em;color:#38bdf8">$1</code>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<div style="font-weight:700;font-size:13px;margin:8px 0 4px">$1</div>')
    .replace(/^## (.+)$/gm,  '<div style="font-weight:700;font-size:14px;margin:10px 0 4px">$1</div>')
    .replace(/^[-*]\s+(.+)$/gm, '<div style="padding-left:14px;margin:2px 0">• $1</div>')
    .replace(/\n/g, '<br/>');
}

const TOOL_COLORS: Record<string, string> = {
  new_flowsheet:'#4f46e5', add_object:'#0891b2', connect_streams:'#059669',
  set_stream_property:'#d97706', set_unit_op_property:'#7c3aed',
  save_and_solve:'#dc2626', run_simulation:'#dc2626', load_flowsheet:'#0284c7',
  parametric_study:'#9333ea', search_knowledge:'#0ea5e9',
  generate_report:'#ea580c', optimize_parameter:'#7c3aed',
};
function tc(n: string) { return TOOL_COLORS[n] || '#475569'; }

const EXAMPLES = [
  'Create a water heater: 25°C to 80°C, Steam Tables, 1 kg/s',
  'Build a benzene-toluene distillation column with 15 stages',
  'Run a parametric study varying feed temperature from 20°C to 100°C',
  'What streams are in the loaded flowsheet?',
  'Set Feed temperature to 60°C then run simulation',
];

interface Props {
  dark:          boolean;
  messages:      ChatMessage[];
  loading:       boolean;
  streamingText: string;
  onSend:        (t: string) => void;
  onReset:       () => void;
  onExport:      () => void;
  inputRef?:     React.RefObject<HTMLTextAreaElement>;
}

export default function ChatPanel({ dark, messages, loading, streamingText, onSend, onReset, onExport, inputRef }: Props) {
  const [input, setInput] = useState('');
  const internalRef = useRef<HTMLTextAreaElement>(null);
  const ref = inputRef || internalRef;
  const endRef = useRef<HTMLDivElement>(null);

  const BG   = dark ? '#0f172a' : '#f8fafc';
  const BRD  = dark ? '#1e293b' : '#e2e8f0';
  const DIM  = dark ? '#475569' : '#94a3b8';
  const TXT  = dark ? '#e2e8f0' : '#1e293b';
  const AINP = dark ? '#1e293b' : '#fff';

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streamingText]);

  const send = () => {
    const t = input.trim();
    if (!t || loading) return;
    setInput('');
    ref.current && (ref.current.style.height = 'auto');
    onSend(t);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const autoResize = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: BG }}>
      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>

        {/* Empty state */}
        {messages.length === 0 && !streamingText && (
          <div style={{ color: DIM, fontSize: 13, textAlign: 'center', marginTop: 40 }}>
            <div style={{ fontSize: 36, marginBottom: 12 }}>⚗️</div>
            <div style={{ fontWeight: 700, color: TXT, marginBottom: 4 }}>DWSIM Agentic AI</div>
            <div style={{ marginBottom: 20 }}>Ask me to build a flowsheet, analyse results, or answer engineering questions.</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'center' }}>
              {EXAMPLES.map(ex => (
                <button
                  key={ex}
                  onClick={() => onSend(ex)}
                  style={{
                    background: dark ? '#1e293b' : '#fff',
                    border: `1px solid ${dark ? '#334155' : '#e2e8f0'}`,
                    borderRadius: 8, padding: '6px 14px',
                    color: dark ? '#94a3b8' : '#475569',
                    cursor: 'pointer', fontSize: 12, maxWidth: 420, textAlign: 'left',
                  }}
                >{ex}</button>
              ))}
            </div>
          </div>
        )}

        {/* Messages */}
        {messages.map((msg, i) => {
          if (msg.role === 'user') return (
            <div key={i} style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <div style={{
                background: '#1e40af', color: '#fff',
                borderRadius: '16px 16px 4px 16px',
                padding: '8px 14px', maxWidth: '80%', fontSize: 13, lineHeight: 1.5,
              }}>{msg.content}</div>
            </div>
          );

          if (msg.role === 'tool') return (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span style={{ color: DIM, fontSize: 11 }}>🔧</span>
              {(msg.tools || []).map((t2, j) => (
                <span key={j} style={{
                  background: tc(t2.name) + '22',
                  border: '1px solid ' + tc(t2.name) + '55',
                  color: tc(t2.name),
                  borderRadius: 4, padding: '2px 8px',
                  fontSize: 11, fontFamily: 'monospace',
                }}>
                  {t2.name}
                  {t2.result?.success === false && <span style={{ color: '#f87171', marginLeft: 4 }}>✗</span>}
                  {t2.result?.success === true  && <span style={{ color: '#86efac', marginLeft: 4 }}>✓</span>}
                </span>
              ))}
            </div>
          );

          if (msg.role === 'error') return (
            <div key={i} style={{
              background: '#1c0a0a', border: '1px solid #7f1d1d',
              borderRadius: 8, padding: '8px 14px', color: '#f87171', fontSize: 13,
            }}>⚠ {msg.content}</div>
          );

          return (
            <div key={i} style={{ display: 'flex', justifyContent: 'flex-start' }}>
              <div
                style={{
                  background: AINP, border: `1px solid ${dark ? '#334155' : '#e2e8f0'}`,
                  borderRadius: '16px 16px 16px 4px',
                  padding: '10px 14px', maxWidth: '90%', fontSize: 13, lineHeight: 1.6, color: TXT,
                }}
                dangerouslySetInnerHTML={{ __html: renderMd(msg.content) }}
              />
            </div>
          );
        })}

        {/* Streaming */}
        {streamingText && (
          <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
            <div style={{
              background: AINP, border: `1px solid ${dark ? '#334155' : '#e2e8f0'}`,
              borderRadius: '16px 16px 16px 4px',
              padding: '10px 14px', maxWidth: '90%', fontSize: 13, lineHeight: 1.6, color: TXT,
            }}>
              <span dangerouslySetInnerHTML={{ __html: renderMd(streamingText) }} />
              <span style={{ display: 'inline-block', width: 7, height: 14, background: '#38bdf8', marginLeft: 2, verticalAlign: 'middle', animation: 'blink 1s step-end infinite' }} />
            </div>
          </div>
        )}

        {loading && !streamingText && (
          <div style={{ color: DIM, fontSize: 12 }}>⏳ Thinking…</div>
        )}
        <div ref={endRef} />
      </div>

      {/* Input area */}
      <div style={{ padding: '10px 12px', borderTop: `1px solid ${BRD}`, background: BG, display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          ref={ref}
          value={input}
          onChange={autoResize}
          onKeyDown={onKey}
          disabled={loading}
          placeholder="Ask about your flowsheet or create a new one… (Enter=send, Shift+Enter=newline)"
          rows={2}
          style={{
            flex: 1, background: AINP, border: `1px solid ${dark ? '#334155' : '#e2e8f0'}`,
            borderRadius: 8, color: TXT, padding: '8px 12px', fontSize: 13,
            resize: 'none', outline: 'none', fontFamily: 'inherit', minHeight: 42,
          }}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            style={{
              background: loading || !input.trim() ? (dark ? '#334155' : '#e2e8f0') : '#0ea5e9',
              color: loading || !input.trim() ? DIM : '#fff',
              border: 'none', borderRadius: 8, padding: '8px 16px',
              fontWeight: 700, cursor: loading ? 'not-allowed' : 'pointer', fontSize: 13,
            }}
          >{loading ? '…' : 'Send'}</button>
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={onReset}
              style={{ flex: 1, background: 'none', border: `1px solid ${dark ? '#334155' : '#e2e8f0'}`, borderRadius: 6, color: DIM, padding: '3px 8px', cursor: 'pointer', fontSize: 11 }}
            >Reset</button>
            <button
              onClick={onExport}
              title="Export chat as Markdown"
              style={{ background: 'none', border: `1px solid ${dark ? '#334155' : '#e2e8f0'}`, borderRadius: 6, color: DIM, padding: '3px 8px', cursor: 'pointer', fontSize: 11 }}
            >⬇ MD</button>
          </div>
        </div>
      </div>

      <style>{`@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }`}</style>
    </div>
  );
}
