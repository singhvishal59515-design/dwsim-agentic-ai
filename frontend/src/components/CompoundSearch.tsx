import React, { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../utils/api';

interface Props {
  dark:    boolean;
  onPaste: (text: string) => void;
}

export default function CompoundSearch({ dark, onPaste }: Props) {
  const [query,     setQuery]     = useState('');
  const [results,   setResults]   = useState<string[]>([]);
  const [loading,   setLoading]   = useState(false);
  const [packages,  setPackages]  = useState<string[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const brd  = dark ? '#334155' : '#e2e8f0';
  const card = dark ? '#0f172a' : '#f1f5f9';
  const inp  = dark ? '#1e293b' : '#fff';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  useEffect(() => {
    api.getPropertyPackages().then((d: any) => {
      if (Array.isArray(d?.property_packages)) setPackages(d.property_packages);
    }).catch(() => {});
  }, []);

  const search = useCallback((q: string) => {
    if (!q.trim()) { setResults([]); return; }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const d: any = await api.getCompounds(q);
        setResults((d?.compounds || []).slice(0, 20));
      } catch { setResults([]); }
      finally { setLoading(false); }
    }, 300);
  }, []);

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: dark ? '#0f172a' : '#f8fafc', padding: 12 }}>
      {/* Compound search */}
      <div style={{ fontSize: 10, fontWeight: 700, color: dim, letterSpacing: 0.8, marginBottom: 8 }}>
        COMPOUND SEARCH
      </div>
      <input
        value={query}
        onChange={e => { setQuery(e.target.value); search(e.target.value); }}
        placeholder="Search 1 000+ compounds…"
        style={{
          width: '100%', boxSizing: 'border-box',
          background: inp, border: `1px solid ${brd}`, borderRadius: 6,
          color: txt, padding: '6px 10px', fontSize: 12, outline: 'none', marginBottom: 8,
        }}
      />
      {loading && <div style={{ color: dim, fontSize: 12 }}>Searching…</div>}
      {results.map(c => (
        <div
          key={c}
          onClick={() => {
            onPaste(c);
            setQuery('');
            setResults([]);
          }}
          style={{
            background: inp, border: `1px solid ${brd}`, borderRadius: 6,
            padding: '5px 10px', marginBottom: 4, cursor: 'pointer', fontSize: 12, color: txt,
          }}
          onMouseEnter={e => (e.currentTarget.style.borderColor = '#0ea5e9')}
          onMouseLeave={e => (e.currentTarget.style.borderColor = brd)}
        >
          {c}
        </div>
      ))}
      {query && results.length === 0 && !loading && (
        <div style={{ color: dim, fontSize: 12 }}>No compounds found for "{query}"</div>
      )}

      {/* Property packages */}
      {packages.length > 0 && (
        <>
          <div style={{ fontSize: 10, fontWeight: 700, color: dim, letterSpacing: 0.8, marginTop: 16, marginBottom: 8 }}>
            PROPERTY PACKAGES
          </div>
          {packages.slice(0, 20).map(p => (
            <div
              key={p}
              onClick={() => onPaste(p)}
              style={{
                background: card, border: `1px solid ${brd}`, borderRadius: 6,
                padding: '4px 10px', marginBottom: 4, cursor: 'pointer', fontSize: 11, color: dim,
              }}
              title={`Click to insert: ${p}`}
            >
              ⚗ {p}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
