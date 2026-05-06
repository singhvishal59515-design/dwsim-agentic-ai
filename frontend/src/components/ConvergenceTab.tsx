import React from 'react';
import { ConvergenceData } from '../types';

interface Props {
  data:  ConvergenceData | null;
  dark:  boolean;
}

export default function ConvergenceTab({ data, dark }: Props) {
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';

  if (!data) {
    return (
      <div style={{ padding: 24, color: dim, fontSize: 13, textAlign: 'center' }}>
        Run simulation to check convergence.
      </div>
    );
  }

  const ok = data.all_converged;

  return (
    <div style={{ padding: 16, background: bg, height: '100%', overflowY: 'auto' }}>
      {/* Status banner */}
      <div style={{
        background: ok ? '#14532d' : '#1c0a0a',
        border: `1px solid ${ok ? '#166534' : '#7f1d1d'}`,
        borderRadius: 10, padding: '12px 16px', marginBottom: 16,
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <span style={{ fontSize: 22 }}>{ok ? '✅' : '❌'}</span>
        <div>
          <div style={{ fontWeight: 700, color: ok ? '#86efac' : '#f87171', fontSize: 14 }}>
            {ok ? 'All streams converged' : `${data.not_converged?.length || 0} stream(s) not converged`}
          </div>
          {data.auto_corrected && (
            <div style={{ fontSize: 11, color: '#a3e635', marginTop: 2 }}>
              ✓ Auto-correction applied
            </div>
          )}
        </div>
      </div>

      {/* Not-converged list */}
      {!ok && data.not_converged?.length > 0 && (
        <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: 12, marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: dim, letterSpacing: 0.8, marginBottom: 8 }}>
            NOT CONVERGED
          </div>
          {data.not_converged.map(tag => (
            <div key={tag} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
              borderBottom: `1px solid ${brd}`, fontSize: 13,
            }}>
              <span style={{ color: '#f87171' }}>✗</span>
              <span style={{ fontFamily: 'monospace', color: '#fca5a5' }}>{tag}</span>
            </div>
          ))}
        </div>
      )}

      {/* Errors */}
      {data.errors && data.errors.length > 0 && (
        <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: 12, marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: dim, letterSpacing: 0.8, marginBottom: 8 }}>
            ERRORS
          </div>
          {data.errors.map((e, i) => (
            <div key={i} style={{ fontSize: 12, color: '#fca5a5', padding: '3px 0',
              borderBottom: `1px solid ${brd}` }}>
              {e}
            </div>
          ))}
        </div>
      )}

      {/* Fixes applied */}
      {data.fixes_applied && data.fixes_applied.length > 0 && (
        <div style={{ background: card, border: `1px solid ${brd}`, borderRadius: 8, padding: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: dim, letterSpacing: 0.8, marginBottom: 8 }}>
            AUTO-CORRECTIONS APPLIED
          </div>
          {data.fixes_applied.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: '#86efac', padding: '3px 0' }}>
              ✓ {f}
            </div>
          ))}
        </div>
      )}

      {ok && (!data.errors || data.errors.length === 0) && (
        <div style={{ color: dim, fontSize: 12, textAlign: 'center', marginTop: 12 }}>
          All objects solved successfully.
        </div>
      )}
    </div>
  );
}
