'use client';

import React from 'react';
import { useTranslations, useFormatter } from 'next-intl';
import { ChevronLeft, Loader2, RefreshCw, Paperclip, Sparkles, Send, FileText, Inbox } from 'lucide-react';
import DOMPurify from 'dompurify';
import type { EmailMessage } from '@/lib/api/types';
import { apiErrorText } from './helpers';
import { fmtDate } from './helpers';
import EmptyState from './empty-state';

interface AttachmentData {
  attachments: { attachment_id: string; filename: string; size_bytes: number }[];
}

interface ProcessResult {
  message?: string;
  processed_count: number;
}

interface MessageDetailProps {
  selected: EmailMessage | null;
  onDeselect: () => void;
  bodyQuery: {
    isLoading: boolean;
    error: unknown;
    data?: { plain_text?: string | null; html?: string | null } | null;
    refetch: () => void;
  };
  attachmentsMut: {
    isPending: boolean;
    mutate: (id: string) => void;
    data?: AttachmentData | null;
    reset: () => void;
  };
  processAttachmentsMut: {
    isPending: boolean;
    mutate: (id: string) => void;
    data?: ProcessResult | null;
  };
  onReply: () => void;
}

export default function MessageDetail({
  selected,
  onDeselect,
  bodyQuery,
  attachmentsMut,
  processAttachmentsMut,
  onReply,
}: MessageDetailProps) {
  const t = useTranslations('gmail');
  const format = useFormatter();

  if (!selected) {
    return (
      <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <EmptyState title={t('selectPrompt')} hint={t('selectHint')} icon={<Inbox className="w-6 h-6 text-slate-300" />} />
      </div>
    );
  }

  return (
    <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="flex flex-col h-full">
        <div className="px-4 py-3 border-b border-slate-100">
          <div className="flex items-center gap-2 mb-1">
            <button onClick={onDeselect} className="text-slate-400 hover:text-slate-600 lg:hidden"><ChevronLeft className="w-4 h-4" /></button>
            <h3 className="text-sm font-semibold text-slate-900 flex-1 truncate">{selected.subject || t('noSubject')}</h3>
            {selected.category && <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600">{selected.category}</span>}
          </div>
          <p className="text-[11px] text-slate-500">{t('from', { name: selected.sender_name, email: selected.sender_email })} · {fmtDate(selected.received_at, format)}</p>
        </div>
        <div className="p-4 text-sm text-slate-700 whitespace-pre-wrap max-h-[40vh] overflow-y-auto">
          {bodyQuery.isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : bodyQuery.error ? (
            <div className="flex flex-col items-center gap-2 text-xs text-rose-500">
              <span>{t('contentLoadError', { error: apiErrorText(bodyQuery.error) })}</span>
              <button onClick={() => bodyQuery.refetch()} className="inline-flex items-center gap-1 px-2 py-1 rounded border border-rose-200 hover:bg-rose-50 text-rose-600">
                <RefreshCw className="w-3 h-3" /> {t('retryBtn')}
              </button>
            </div>
          ) : bodyQuery.data?.plain_text || bodyQuery.data?.html ? (
            bodyQuery.data.html ? (
              <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(bodyQuery.data.html) }} />
            ) : bodyQuery.data.plain_text
          ) : t('noContent')}
        </div>
        <div className="px-4 py-3 border-t border-slate-100 flex flex-wrap items-center gap-2">
          <button
            onClick={() => attachmentsMut.mutate(selected.id)}
            disabled={attachmentsMut.isPending}
            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50"
          >
            {attachmentsMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Paperclip className="w-3.5 h-3.5" />} {t('getAttachments')}
          </button>
          <button
            onClick={() => processAttachmentsMut.mutate(selected.id)}
            disabled={processAttachmentsMut.isPending}
            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            <Sparkles className="w-3.5 h-3.5" /> {t('processCv')}
          </button>
          <button
            onClick={onReply}
            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 ml-auto"
          >
            <Send className="w-3.5 h-3.5" /> {t('replyBtn')}
          </button>
        </div>
        {attachmentsMut.data && (
          <div className="px-4 pb-3 space-y-1">
            <p className="text-[10px] font-mono uppercase text-slate-400">{t('attachments', { count: attachmentsMut.data.attachments.length })}</p>
            {attachmentsMut.data.attachments.map((a) => (
              <div key={a.attachment_id} className="text-xs text-slate-600 flex items-center gap-2">
                <FileText className="w-3.5 h-3.5 text-slate-400" />
                <span className="truncate">{a.filename}</span>
                <span className="text-[10px] text-slate-400">{(a.size_bytes / 1024).toFixed(1)}KB</span>
              </div>
            ))}
          </div>
        )}
        {processAttachmentsMut.data && (
          <div className="px-4 pb-3 text-xs text-emerald-600">{processAttachmentsMut.data.message || t('cvProcessedCount', { count: processAttachmentsMut.data.processed_count })}</div>
        )}
      </div>
    </div>
  );
}
