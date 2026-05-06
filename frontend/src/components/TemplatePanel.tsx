import React, { useState, useEffect } from 'react';
import { QUICK_TEMPLATES } from '../types';
import { api } from '../utils/api';

interface Props {
  dark:   boolean;
  onUse:  (prompt: string) => void;
}

export default function TemplatePanel({ dark, onUse }: Props) {
  const [templates, setTemplates] = useState<{ name: string; description?: string }[]>([]);
  const brd  = dark ? '#334155' : '#e2e8f0';
  const card = dark ? '#0f172a' : '#f1f5f9';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';
  const hdr  = dark ? '#1e293b' : '#fff';

  useEffect(() => {
    api.listTemplates().then((d: any) => {
      if (Array.isArray(d?.templates)) setTemplates(d.templates);
    }).catch(() => {});
  }, []);

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: dark ? '#0f172a' : '#f8fafc' }}>
      {/* Quick-start prompts */}
      <div style={{ padding: '10px 12px 4px', fontSize: 10, fontWeight: 700, color: dim, letterSpacing: 0.8 }}>
        QUICK-START
      </div>
      {QUICK_TEMPLATES.map(t => (
        <div
          key={t.key}
          onClick={() => onUse(t.prompt)}
          style={{
            margin: '0 8px 6px', background: hdr, border: `1px solid ${brd}`,
            borderRadius: 8, padding: '8px 10px', cursor: 'pointer',
            transition: 'border-color .15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.borderColor = '#0ea5e9')}
          onMouseLeave={e => (e.currentTarget.style.borderColor = brd)}
        >
          <div style={{ fontWeight: 600, color: txt, fontSize: 12, marginBottom: 2 }}>
            ⚡ {t.label}
          </div>
          <div style={{ color: dim, fontSize: 11, lineHeight: 1.4 }}>
            {t.prompt.length > 80 ? t.prompt.slice(0, 80) + '…' : t.prompt}
          </div>
        </div>
      ))}

      {/* Server-side templates (if any) */}
      {templates.length > 0 && (
        <>
          <div style={{ padding: '10px 12px 4px', fontSize: 10, fontWeight: 700, color: dim, letterSpacing: 0.8 }}>
            SAVED TEMPLATES
          </div>
          {templates.map((t, i) => (
            <div
              key={i}
              onClick={() => {
                api.createFromTemplate(t.name).then((r: any) => {
                  if (r?.success) onUse(`Template '${t.name}' loaded.`);
                }).catch(() => {});
              }}
              style={{
                margin: '0 8px 6px', background: hdr, border: `1px solid ${brd}`,
                borderRadius: 8, padding: '8px 10px', cursor: 'pointer',
              }}
            >
              <div style={{ fontWeight: 600, color: txt, fontSize: 12 }}>📋 {t.name}</div>
              {t.description && <div style={{ color: dim, fontSize: 11 }}>{t.description}</div>}
            </div>
          ))}
        </>
      )}

      {/* Example research queries */}
      <div style={{ padding: '10px 12px 4px', fontSize: 10, fontWeight: 700, color: dim, letterSpacing: 0.8 }}>
        RESEARCH &amp; ANALYSIS
      </div>
      {[
        { label: 'Parametric study',   prompt: 'Run a parametric study varying the feed temperature from 20°C to 100°C in 10 steps and plot the outlet enthalpy.' },
        { label: 'Research report',    prompt: 'Run a parametric study and generate a 6-section academic PDF research report with abstract, introduction, methodology, results, discussion, and conclusion.' },
        { label: 'Economic analysis',  prompt: 'Estimate the capital cost, operating cost, and NPV for the current flowsheet.' },
        { label: 'Safety check',       prompt: 'Check for any silent failure modes in the current simulation.' },
      ].map(t => (
        <div
          key={t.label}
          onClick={() => onUse(t.prompt)}
          style={{
            margin: '0 8px 6px', background: card, border: `1px solid ${brd}`,
            borderRadius: 8, padding: '7px 10px', cursor: 'pointer',
          }}
          onMouseEnter={e => (e.currentTarget.style.borderColor = '#7c3aed')}
          onMouseLeave={e => (e.currentTarget.style.borderColor = brd)}
        >
          <div style={{ fontWeight: 600, color: '#c084fc', fontSize: 12 }}>🔬 {t.label}</div>
          <div style={{ color: dim, fontSize: 11 }}>{t.prompt.slice(0, 70)}…</div>
        </div>
      ))}
    </div>
  );
}
