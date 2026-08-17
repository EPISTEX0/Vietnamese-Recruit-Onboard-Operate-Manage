'use client';

import React from 'react';
import { useTranslations, useFormatter } from 'next-intl';
import { Send, Loader2, X } from 'lucide-react';
import type { OutboundEmail } from '@/lib/api/gmail';
import EmptyState from './empty-state';
import { fmtDate } from './helpers';

interface OutboundSectionProps {
  items: OutboundEmail[];
  loading: boolean;
  onSend: (id: string) => void;
  onDelete: (id: string) => void;
  sending: boolean;
}

export default function OutboundSection({
  items, loading, onSend, onDelete, sending,
}: OutboundSectionProps) {
  const t = useTranslations('gmail');
  const format = useFormatter();
  const statusBadge = (s: string) => {
    const map: Record<string, string> = {
      pending: 'bg-amber-50 text-amber-700 border-amber-200',
      sending: 'bg-indigo-50 text-indigo-700 border-indigo-200',
      sent: 'bg-emerald-50 text-emerald-700 border-emerald-200',
      failed: 'bg-rose-50 text-rose-700 border-rose-200',
    };
    return map[s] ?? 'bg-slate-50 text-slate-600 border-slate-200';
  };
  return (
    <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm">
      <div className="flex items-center gap-2 mb-3">
        <Send className="w-4 h-4 text-indigo-600" />
        <h2 className="font-bold text-sm text-slate-900">{t('outboundTitle')}</h2>
        <span className="text-[10px] text-slate-400 ml-auto">{t('outboundHint')}</span>
      </div>
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin text-slate-300" />
      ) : items.length === 0 ? (
        <EmptyState title={t('emptyOutboundTitle')} hint={t('emptyOutboundHint')} />
      ) : (
        <div className="space-y-2">
          {items.map((m) => (
            <div key={m.id} className="p-3 bg-slate-50 rounded-lg border border-slate-100 flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs font-semibold text-slate-800 truncate">{m.subject || t('noSubjectOutbound')}</span>
                  <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded-full border ${statusBadge(m.status)}`}>{m.status}</span>
                </div>
                <p className="text-[11px] text-slate-500 truncate">{t('toPrefix', { emails: m.to.join(', ') })}</p>
                {m.error_message && <p className="text-[11px] text-rose-500 truncate mt-0.5">{t('errorPrefix', { msg: m.error_message })}</p>}
                <p className="text-[10px] text-slate-400 mt-0.5">{t('createdDate', { date: fmtDate(m.created_at, format) })}{m.sent_at ? ` · ${t('sentDate', { date: fmtDate(m.sent_at, format) })}` : ''}</p>
              </div>
              {m.status === 'pending' && (
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => { if (confirm(t('sendConfirm'))) onSend(m.id); }}
                    disabled={sending}
                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />} {t('sendReal')}
                  </button>
                  <button onClick={() => onDelete(m.id)} className="p-1.5 rounded-lg text-slate-400 hover:bg-rose-50 hover:text-rose-500"><X className="w-3.5 h-3.5" /></button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
