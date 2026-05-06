import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';

interface MemEntry {
  id?: string; entry_type: string; timestamp?: string;
  name?: string; compounds?: string[]; property_package?: string;
  prompt?: string; text?: string; value?: string; converged?: boolean;
}

interface KBResult { title: string; text: string; score?: number; tags?: string[]; }

interface Props { dark: boolean; }

const TYPE_COLOR: Record<string, string> = {
  flowsheet_built: '#38bdf8',
  goal:           '#a78bfa',
  constraint:     '#fbbf24',
  outcome:        '#4ade80',
  note:           '#94a3b8',
};

export default function MemoryPanel({ dark }: Props) {
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';
  const bg   = dark ? '#0f172a' : '#f8fafc';

  const [memOpen,  setMemOpen]  = useState(false);
  const [kbOpen,   setKbOpen]   = useState(false);
  const [entries,  setEntries]  = useState<MemEntry[]>([]);
  const [goals,    setGoals]    = useState<any[]>([]);
  const [kbQuery,  setKbQuery]  = useState('');
  const [kbResults,setKbResults]= useState<KBResult[]>([]);
  const [kbTopics, setKbTopics] = useState<string[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [kbLoading,setKbLoading]= useState(false);

  const loadMemory = useCallback(async () => {
    setLoading(true);
    try {
      const [r, g] = await Promise.allSettled([
        api.memoryRecent(20),
        api.memoryGoals(),
      ]);
      if (r.status === 'fulfilled') setEntries(r.value?.entries || r.value?.journal || []);
      if (g.status === 'fulfilled') setGoals(g.value?.goals || []);
    } finally { setLoading(false); }
  }, []);

  const loadKbTopics = useCallback(async () => {
    try {
      const d: any = await api.knowledgeTopics();
      setKbTopics(d?.topics || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { if (memOpen) loadMemory(); }, [memOpen, loadMemory]);
  useEffect(() => { if (kbOpen) loadKbTopics(); }, [kbOpen, loadKbTopics]);

  const searchKb = async () => {
    if (!kbQuery.trim()) return;
    setKbLoading(true);
    try {
      const d: any = await api.knowledgeSearch(kbQuery, 5);
      setKbResults(d?.results || []);
    } catch { setKbResults([]); }
    finally { setKbLoading(false); }
  };

  const section = (label: string, open: boolean, toggle: () => void) => (
    <div
      onClick={toggle}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 12px', cursor: 'pointer', borderBottom: `1px solid ${brd}`,
        background: card, userSelect: 'none',
      }}
    >
      <span style={{ fontWeight: 700, color: txt, fontSize: 12 }}>{label}</span>
      <span style={{ color: dim, fontSize: 11 }}>{open ? '▲' : '▼'}</span>
    </div>
  );

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: bg }}>

      {/* Agent Memory */}
      {section('🧠 Agent Memory', memOpen, () => setMemOpen(o => !o))}
      {memOpen && (
        <div style={{ padding: '8px 12px', borderBottom: `1px solid ${brd}` }}>
          {/* Goals */}
          {goals.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ color: dim, fontSize: 10, fontWeight: 600, marginBottom: 4 }}>ACTIVE GOALS</div>
              {goals.map((g: any, i) => (
                <div key={i} style={{
                  background: card, border: `1px solid #7c3aed`, borderRadius: 6,
                  padding: '5px 8px', marginBottom: 4, fontSize: 11,
                }}>
                  <span style={{ color: '#a78bfa' }}>◎ </span>
                  <span style={{ color: txt }}>{g.text || g.goal || JSON.stringify(g)}</span>
                </div>
              ))}
            </div>
          )}

          {loading && <div style={{ color: dim, fontSize: 11 }}>Loading…</div>}

          {entries.length === 0 && !loading && (
            <div style={{ color: dim, fontSize: 11 }}>
              No memory entries yet. The agent records flowsheets built, goals, constraints, and outcomes.
            </div>
          )}

          {entries.map((e, i) => {
            const col = TYPE_COLOR[e.entry_type] || dim;
            const label = e.name || e.prompt?.slice(0, 40) || e.text?.slice(0, 40) || e.value || '';
            return (
              <div key={i} style={{
                background: card, border: `1px solid ${brd}`, borderRadius: 7,
                padding: '6px 10px', marginBottom: 6,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                  <span style={{
                    background: col + '22', color: col, fontSize: 9, fontWeight: 700,
                    padding: '1px 6px', borderRadius: 4,
                  }}>{e.entry_type?.replace('_', ' ').toUpperCase()}</span>
                  {e.timestamp && (
                    <span style={{ color: dim, fontSize: 9 }}>
                      {new Date(e.timestamp).toLocaleString()}
                    </span>
                  )}
                  {e.converged !== undefined && (
                    <span style={{ color: e.converged ? '#4ade80' : '#f87171', fontSize: 9 }}>
                      {e.converged ? '✓ converged' : '✗ failed'}
                    </span>
                  )}
                </div>
                {label && <div style={{ color: txt, fontSize: 11 }}>{label}</div>}
                {e.compounds && e.compounds.length > 0 && (
                  <div style={{ color: dim, fontSize: 10, marginTop: 2 }}>
                    {e.compounds.join(', ')} · {e.property_package}
                  </div>
                )}
              </div>
            );
          })}

          <button onClick={loadMemory} style={{
            width: '100%', marginTop: 4, background: '#1e293b', color: '#38bdf8',
            border: `1px solid ${brd}`, borderRadius: 5, padding: '4px 0', fontSize: 11, cursor: 'pointer',
          }}>↻ Refresh Memory</button>
        </div>
      )}

      {/* Knowledge Base */}
      {section('📚 Knowledge Base', kbOpen, () => setKbOpen(o => !o))}
      {kbOpen && (
        <div style={{ padding: '8px 12px', borderBottom: `1px solid ${brd}` }}>
          {/* Topic chips */}
          {kbTopics.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ color: dim, fontSize: 10, fontWeight: 600, marginBottom: 5 }}>TOPICS</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {kbTopics.map(t => (
                  <button key={t} onClick={() => { setKbQuery(t); }}
                    style={{
                      background: kbQuery === t ? '#0ea5e9' : '#1e293b',
                      color: kbQuery === t ? '#fff' : '#7dd3fc',
                      border: `1px solid ${kbQuery === t ? '#0ea5e9' : brd}`,
                      borderRadius: 12, padding: '2px 8px', fontSize: 10, cursor: 'pointer',
                    }}>{t}</button>
                ))}
              </div>
            </div>
          )}

          {/* Search bar */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            <input
              value={kbQuery}
              onChange={e => setKbQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && searchKb()}
              placeholder="Search knowledge base…"
              style={{
                flex: 1, background: dark ? '#0f172a' : '#f8fafc',
                border: `1px solid ${brd}`, borderRadius: 5,
                color: txt, fontSize: 11, padding: '5px 8px',
              }}
            />
            <button onClick={searchKb} disabled={kbLoading} style={{
              background: '#0ea5e9', color: '#fff', border: 'none',
              borderRadius: 5, padding: '0 12px', fontSize: 11,
              cursor: kbLoading ? 'not-allowed' : 'pointer',
            }}>{kbLoading ? '…' : '🔍'}</button>
          </div>

          {kbResults.length === 0 && !kbLoading && kbQuery && (
            <div style={{ color: dim, fontSize: 11 }}>No results. Try different keywords.</div>
          )}

          {kbResults.map((r, i) => (
            <div key={i} style={{
              background: card, border: `1px solid ${brd}`, borderRadius: 8,
              padding: '8px 10px', marginBottom: 8,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ color: '#38bdf8', fontSize: 12, fontWeight: 600 }}>{r.title}</span>
                {r.score != null && (
                  <span style={{ color: dim, fontSize: 10 }}>score: {r.score.toFixed(3)}</span>
                )}
              </div>
              <div style={{ color: txt, fontSize: 11, lineHeight: 1.5 }}>
                {r.text.slice(0, 250)}{r.text.length > 250 ? '…' : ''}
              </div>
              {r.tags && r.tags.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 5 }}>
                  {r.tags.map(t => (
                    <span key={t} style={{
                      background: '#1e3a5f', color: '#93c5fd', fontSize: 9,
                      padding: '1px 5px', borderRadius: 4,
                    }}>{t}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
