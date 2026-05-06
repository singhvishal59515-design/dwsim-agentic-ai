import React from 'react';
import { ReportCard } from '../types';
import { api } from '../utils/api';

interface Props {
  reports: ReportCard[];
  dark:    boolean;
}

export default function ReportsTab({ reports, dark }: Props) {
  const bg   = dark ? '#0f172a' : '#f8fafc';
  const card = dark ? '#1e293b' : '#fff';
  const brd  = dark ? '#334155' : '#e2e8f0';
  const dim  = dark ? '#64748b' : '#94a3b8';
  const txt  = dark ? '#e2e8f0' : '#1e293b';

  if (reports.length === 0) {
    return (
      <div style={{ padding: 24, color: dim, fontSize: 13, textAlign: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>📄</div>
        No reports generated yet.<br />
        <span style={{ color: dim, fontSize: 12 }}>
          Ask the agent to "generate a research report" after a parametric study.
        </span>
      </div>
    );
  }

  return (
    <div style={{ padding: 12, background: bg, height: '100%', overflowY: 'auto' }}>
      {reports.map((r, i) => (
        <div key={i} style={{
          background: card, border: `1px solid ${brd}`, borderRadius: 10,
          padding: 14, marginBottom: 10,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 700, color: txt, fontSize: 13, marginBottom: 4 }}>
                📄 {r.title}
              </div>
              <div style={{ fontSize: 11, color: dim }}>{r.timestamp}</div>
              {(r.data_points || r.plot_count) ? (
                <div style={{ fontSize: 11, color: dim, marginTop: 4 }}>
                  {r.data_points ? `${r.data_points} data points` : ''}
                  {r.data_points && r.plot_count ? ' · ' : ''}
                  {r.plot_count ? `${r.plot_count} plot(s)` : ''}
                </div>
              ) : null}
              {r.sections && r.sections.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                  {r.sections.map(s => (
                    <span key={s} style={{
                      background: '#0ea5e933', color: '#38bdf8',
                      borderRadius: 4, padding: '1px 6px', fontSize: 10,
                    }}>{s}</span>
                  ))}
                </div>
              )}
            </div>
            {r.pdf_path && (
              <a
                href={api.downloadReport(r.pdf_path)}
                download
                target="_blank"
                rel="noreferrer"
                style={{ textDecoration: 'none', flexShrink: 0, marginLeft: 10 }}
              >
                <button style={{
                  background: '#0ea5e9', color: '#fff', border: 'none',
                  borderRadius: 6, padding: '6px 12px', cursor: 'pointer', fontSize: 12,
                  fontWeight: 600,
                }}>
                  ⬇ PDF
                </button>
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
