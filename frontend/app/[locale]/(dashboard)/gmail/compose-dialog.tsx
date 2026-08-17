'use client';

import React, { useEffect, useState } from 'react';
import { useTranslations, useFormatter } from 'next-intl';
import { motion } from 'motion/react';
import { X, Loader2, PenSquare } from 'lucide-react';
import type { EmailMessage } from '@/lib/api/types';

interface ComposeDialogProps {
  open: boolean;
  onClose: () => void;
  replyTo: EmailMessage | null;
  replyBodyText?: string | null;
  onSend: (data: { to: string[]; cc?: string[]; subject: string; body_text?: string; body_html?: string; reply_to_message_id?: string }) => void;
  sending: boolean;
}

export default function ComposeDialog({
  open, onClose, replyTo, replyBodyText, onSend, sending,
}: ComposeDialogProps) {
  const t = useTranslations('gmail');
  const format = useFormatter();
  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState('');
  const [bodyText, setBodyText] = useState('');

  useEffect(() => {
    if (open) {
      setTo(replyTo ? replyTo.sender_email : '');
      setCc('');
      setSubject(replyTo ? `Re: ${replyTo.subject || ''}` : '');
      if (replyTo) {
        const date = replyTo.received_at ? format.dateTime(new Date(replyTo.received_at), 'full') : '';
        const header = `Vào ${date}, ${replyTo.sender_name || replyTo.sender_email} đã viết:\n`;
        const sourceText = replyBodyText || replyTo.snippet || '';
        const quoted = sourceText
          .split('\n')
          .map((line) => `> ${line}`)
          .join('\n');
        setBodyText(header + quoted + '\n\n');
      } else {
        setBodyText('');
      }
    }
  }, [open, replyTo, replyBodyText, format]);

  if (!open) return null;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const toArr = to.split(',').map((s) => s.trim()).filter(Boolean);
    if (toArr.length === 0 || !subject.trim()) return;
    onSend({
      to: toArr,
      cc: cc ? cc.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
      subject: subject.trim(),
      body_text: bodyText,
      reply_to_message_id: replyTo?.gmail_message_id,
    });
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <motion.form
        onSubmit={submit}
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-lg bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden"
      >
        <div className="px-4 py-3 bg-slate-900 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">{t('composeTitle')}</h3>
          <button type="button" onClick={onClose} className="text-slate-300 hover:text-white"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 space-y-3">
          <Field label={t('to')}>
            <input value={to} onChange={(e) => setTo(e.target.value)} required className="w-full text-xs border border-slate-200 rounded-lg px-2.5 py-2 focus:outline-none focus:border-indigo-500" placeholder={t('toPlaceholder')} />
          </Field>
          <Field label={t('cc')}>
            <input value={cc} onChange={(e) => setCc(e.target.value)} className="w-full text-xs border border-slate-200 rounded-lg px-2.5 py-2 focus:outline-none focus:border-indigo-500" placeholder={t('ccPlaceholder')} />
          </Field>
          <Field label={t('subject')}>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} required className="w-full text-xs border border-slate-200 rounded-lg px-2.5 py-2 focus:outline-none focus:border-indigo-500" />
          </Field>
          <Field label={t('body')}>
            <textarea value={bodyText} onChange={(e) => setBodyText(e.target.value)} rows={6} className="w-full text-xs border border-slate-200 rounded-lg px-2.5 py-2 focus:outline-none focus:border-indigo-500 resize-none" />
          </Field>
          <p className="text-[10px] text-slate-400">{t('composeHint')}</p>
        </div>
        <div className="px-4 py-3 bg-slate-50 border-t border-slate-100 flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-white">{t('cancelCompose')}</button>
          <button type="submit" disabled={sending} className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
            {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PenSquare className="w-3.5 h-3.5" />} {t('createDraft')}
          </button>
        </div>
      </motion.form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[10px] font-mono uppercase text-slate-400">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}
