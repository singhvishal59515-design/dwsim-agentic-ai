import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '../utils/api';
import { ChatMessage, SafetyWarning, StreamProps, PropertyChange } from '../types';

export interface UseChatReturn {
  messages:       ChatMessage[];
  loading:        boolean;
  streamingText:  string;
  lastSafety:     { status: string; warnings: SafetyWarning[] } | null;
  liveResults:    Record<string, StreamProps>;
  undoStack:      PropertyChange[];
  canUndo:        boolean;
  sendMessage:    (text: string) => Promise<void>;
  reset:          () => void;
  exportChat:     () => void;
  undo:           () => Promise<void>;
}

const STORAGE_KEY = 'dwsim_react_chat';
const MAX_STORED  = 40;

export function useChat(
  onToolResult?: (name: string, result: any) => void,
  onStreamResults?: (results: Record<string, StreamProps>) => void,
): UseChatReturn {
  const [messages,      setMessages]      = useState<ChatMessage[]>([]);
  const [loading,       setLoading]       = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [lastSafety,    setLastSafety]    = useState<{ status: string; warnings: SafetyWarning[] } | null>(null);
  const [liveResults,   setLiveResults]   = useState<Record<string, StreamProps>>({});
  const [undoStack,     setUndoStack]     = useState<PropertyChange[]>([]);
  const abortRef = useRef<(() => void) | null>(null);

  // Session recovery from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const { msgs, ts } = JSON.parse(saved);
        if (msgs?.length && Date.now() - ts < 4 * 3600 * 1000) {
          setMessages(msgs);
        }
      }
    } catch { /* ignore */ }
  }, []);

  // Auto-save chat history
  useEffect(() => {
    if (messages.length === 0) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        msgs: messages.slice(-MAX_STORED),
        ts:   Date.now(),
      }));
    } catch { /* quota exceeded */ }
  }, [messages]);

  const sendMessage = useCallback(async (text: string) => {
    if (loading) return;
    setMessages(prev => [...prev, { role: 'user', content: text, ts: Date.now() }]);
    setLoading(true);
    setStreamingText('');

    await new Promise<void>((resolve) => {
      const cancel = api.chatStream(text, (evt) => {
        if (evt.type === 'token') {
          setStreamingText(prev => prev + evt.data);

        } else if (evt.type === 'tool_call') {
          const d = evt.data as { name: string; args?: any; result?: any };

          // Notify parent for simulation refresh
          if (d.name && d.result && onToolResult) onToolResult(d.name, d.result);

          // Capture live stream results immediately
          if (['save_and_solve', 'run_simulation', 'load_flowsheet'].includes(d.name)
              && d.result?.success && d.result?.stream_results) {
            setLiveResults(d.result.stream_results);
            if (onStreamResults) onStreamResults(d.result.stream_results);
          }

          // Safety status
          if (['save_and_solve', 'run_simulation'].includes(d.name) && d.result) {
            setLastSafety({
              status:   d.result.safety_status  || 'UNKNOWN',
              warnings: d.result.safety_warnings || [],
            });
          }

          // Track undoable property changes
          if (['set_stream_property', 'set_unit_op_property'].includes(d.name)
              && d.result?.success && d.args) {
            setUndoStack(prev => [
              ...prev.slice(-29),
              {
                tag:      d.args?.tag || '',
                property: d.args?.property_name || '',
                oldValue: d.result?.old_value,
                newValue: d.args?.value,
                unit:     d.args?.unit || '',
              },
            ]);
          }

          // Merge consecutive tool bubbles
          setMessages(prev => {
            const last = prev[prev.length - 1];
            if (last?.role === 'tool') {
              return [...prev.slice(0, -1), { ...last, tools: [...(last.tools || []), d] }];
            }
            return [...prev, { role: 'tool', content: '', tools: [d], ts: Date.now() }];
          });

        } else if (evt.type === 'done') {
          setStreamingText('');
          const answer = typeof evt.data === 'string' ? evt.data : '';
          setMessages(prev => [...prev, { role: 'assistant', content: answer, ts: Date.now() }]);
          resolve();

        } else if (evt.type === 'error') {
          setStreamingText('');
          setMessages(prev => [
            ...prev,
            { role: 'error', content: String(evt.data), ts: Date.now() },
          ]);
          resolve();
        }
      });
      abortRef.current = cancel;
    });

    setLoading(false);
    setStreamingText('');
  }, [loading, onToolResult, onStreamResults]);

  const reset = useCallback(() => {
    abortRef.current?.();
    setMessages([]);
    setStreamingText('');
    setLoading(false);
    setLastSafety(null);
    setLiveResults({});
    setUndoStack([]);
    localStorage.removeItem(STORAGE_KEY);
    api.chatReset().catch(() => {});
  }, []);

  const exportChat = useCallback(() => {
    if (messages.length === 0) return;
    let md = '# DWSIM Agentic AI — Chat Export\n\n';
    md += `_Exported: ${new Date().toISOString()}_\n\n---\n\n`;
    for (const msg of messages) {
      if (msg.role === 'user')
        md += `**You:**\n\n${msg.content}\n\n---\n\n`;
      else if (msg.role === 'assistant')
        md += `**Agent:**\n\n${msg.content}\n\n---\n\n`;
      else if (msg.role === 'tool')
        md += `**Tools:** ${(msg.tools || []).map(t => t.name).join(', ')}\n\n---\n\n`;
    }
    const blob = new Blob([md], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `dwsim_chat_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [messages]);

  const undo = useCallback(async () => {
    if (undoStack.length === 0) return;
    const last = undoStack[undoStack.length - 1];
    if (last.oldValue == null) return;
    try {
      await api.setStreamProperty(last.tag, last.property, last.oldValue, last.unit || '');
      setUndoStack(prev => prev.slice(0, -1));
    } catch { /* ignore */ }
  }, [undoStack]);

  return {
    messages, loading, streamingText, lastSafety, liveResults,
    undoStack, canUndo: undoStack.length > 0,
    sendMessage, reset, exportChat, undo,
  };
}
