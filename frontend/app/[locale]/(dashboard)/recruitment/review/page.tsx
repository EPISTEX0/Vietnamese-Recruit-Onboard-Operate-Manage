'use client';
import { useTranslations } from 'next-intl';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FileSearch, Pencil, RefreshCw, Ban, AlertTriangle, CheckCircle2 } from 'lucide-react';
import {
  listReviewQueue, submitCorrection, retryParse, dismissReview,
  type CVReviewItem, type ParsedCVInput, type ProcessingStatus,
} from '@/lib/api/recruitment';
import { ErrorBanner, Loading, EmptyState, StatusPill, confidencePct, MIME_TYPE_LABELS } from '@/components/shared-ui';

const PROC_STATUS_META: Record<ProcessingStatus, { label: string; tone: 'amber' | 'emerald' | 'rose' | 'indigo' | 'slate' }> = {
  pending: { label: 'Chờ', tone: 'slate' },
  ocr_processing: { label: 'Đang OCR', tone: 'indigo' },
  llm_parsing: { label: 'Đang parse AI', tone: 'indigo' },
  completed: { label: 'Hoàn tất', tone: 'emerald' },
  needs_review: { label: 'Cần review', tone: 'amber' },
  failed: { label: 'Thất bại', tone: 'rose' },
  skipped: { label: 'Bỏ qua', tone: 'slate' },
  dismissed: { label: 'Đã bỏ', tone: 'slate' },
  upload_failed: { label: 'Upload lỗi', tone: 'rose' },
  permanently_failed: { label: 'Lỗi vĩnh viễn', tone: 'rose' },
};

export default function ReviewPage() {
  const t = useTranslations('recruitment');
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ['recruitment-review'] });
  const { data, isLoading, error } = useQuery({
    queryKey: ['recruitment-review', page],
    queryFn: () => listReviewQueue({ page, page_size: 12 }),
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });

  const submitM = useMutation({
    mutationFn: ({ id, data }: { id: string; data: ParsedCVInput }) => submitCorrection(id, data),
    onSuccess: () => { invalidate(); setEditing(null);  setActionError(null); },
    onError: (e: unknown) => setActionError(e),
  });
  const retryM = useMutation({ mutationFn: retryParse, onSuccess: () => { invalidate(); setActionError(''); }, onError: (e: unknown) => setActionError(e) });
  const dismissM = useMutation({ mutationFn: dismissReview, onSuccess: () => { invalidate(); setActionError(''); }, onError: (e: unknown) => setActionError(e) });

  const items = data?.items ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-indigo-600">
        <FileSearch className="w-5 h-5" />
        <h1 className="text-xl font-bold text-slate-900">{t('reviewTitle')}</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        {t('reviewDesc')}
      </p>

      {!!actionError && <ErrorBanner error={actionError} />}

      {isLoading ? (
        <Loading label={t('loadingReview')} />
      ) : error ? (
        <ErrorBanner error={error} />
      ) : items.length === 0 ? (
        <EmptyState filtered={page > 1} onReset={() => setPage(1)} />
      ) : (
        <>
          <div className="space-y-3">
            {items.map((item) => (
              <ReviewCard
                key={item.id}
                item={item}
                editing={editing === item.id}
                onToggleEdit={() => setEditing((p) => (p === item.id ? null : item.id))}
                onCorrect={(d) => submitM.mutate({ id: item.id, data: d })}
                onRetry={() => retryM.mutate(item.id)}
                onDismiss={() => dismissM.mutate(item.id)}
                pending={submitM.isPending || retryM.isPending || dismissM.isPending}
              />
            ))}
          </div>
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>{t('pageInfo', { page, total: data?.total ?? 0, count: data?.total ?? 0 })}</span>
            <div className="flex gap-2">
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className="px-2 py-1 bg-white border border-slate-200 rounded disabled:opacity-40">{t('prev')}</button>
              <button onClick={() => setPage((p) => p + 1)} disabled={items.length < 12} className="px-2 py-1 bg-white border border-slate-200 rounded disabled:opacity-40">{t('next')}</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ReviewCard({
      item, editing, onToggleEdit, onCorrect, onRetry, onDismiss, pending,
    }: {
      item: CVReviewItem;
      editing: boolean;
      onToggleEdit: () => void;
      onCorrect: (d: ParsedCVInput) => void;
      onRetry: () => void;
      onDismiss: () => void;
      pending: boolean;
    }) {
  const t = useTranslations('recruitment');
  const meta = PROC_STATUS_META[item.processing_status] ?? { label: item.processing_status, tone: 'slate' as const };
  const p = item.parsed_cv_data ?? {};
  const [name, setName] = useState(p.name ?? '');
  const [email, setEmail] = useState(p.email ?? '');
  const [phone, setPhone] = useState(p.phone ?? '');
  const [skills, setSkills] = useState((p.skills ?? []).join(', '));
  const [summary, setSummary] = useState(p.summary ?? '');

  const submit = () => {
    onCorrect({
      name: name.trim(),
      email: email.trim(),
      phone: phone.trim(),
      skills: skills.split(',').map((s) => s.trim()).filter(Boolean),
      experience: p.experience ?? [],
      education: p.education ?? [],
      summary: summary.trim(),
    });
  };

  return (
    <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <StatusPill status={item.processing_status} label={meta.label} tone={meta.tone} />
            {item.confidence_score != null && (
              <span className="text-[10px] font-mono bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">confidence {confidencePct(item.confidence_score)}</span>
            )}
            {item.retry_count > 0 && <span className="text-[10px] font-mono text-slate-400">retry {item.retry_count}</span>}
          </div>
          <p className="font-semibold text-sm text-slate-900 truncate">{item.original_filename}</p>
          <p className="text-[11px] text-slate-400">#{item.id.slice(0, 8)} · {(item.size_bytes / 1024).toFixed(0)} KB · {MIME_TYPE_LABELS[item.mime_type] ?? item.mime_type}</p>
        </div>
      </div>

      {item.processing_error && (
        <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-600 flex items-start gap-2 mb-2">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{t('parseError', { error: item.processing_error })}</span>
        </div>
      )}

      {/* Provenance: parsed data preview */}
      {!editing && item.parsed_cv_data && (
        <div className="text-xs space-y-1 text-slate-600">
          {item.parsed_cv_data.name && <p><span className="font-mono text-slate-400">{t('fieldName')}</span> {item.parsed_cv_data.name}</p>}
          {item.parsed_cv_data.email && <p><span className="font-mono text-slate-400">{t('fieldEmail')}</span> {item.parsed_cv_data.email}</p>}
          {item.parsed_cv_data.skills && item.parsed_cv_data.skills.length > 0 && (
            <div className="flex flex-wrap gap-1"><span className="font-mono text-slate-400 mr-1">skills:</span>{item.parsed_cv_data.skills.map((s) => <span key={s} className="bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded font-mono">{s}</span>)}</div>
          )}
          {item.validation_errors && item.validation_errors.length > 0 && (
            <ul className="text-amber-600 mt-1">{item.validation_errors.map((v, i) => <li key={i}>• {v.field}: {v.message}</li>)}</ul>
          )}
        </div>
      )}
      {!item.parsed_cv_data && !editing && <p className="text-xs text-slate-400">{t('noParseData')}</p>}

      {/* Correction form */}
      {editing && (
        <div className="mt-2 p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-2">
          <p className="text-[10px] font-mono uppercase text-slate-500">{t('correctionHeader')}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('namePlaceholder')} className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t('emailPlaceholder')} className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg px-2.5" />
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder={t('phonePlaceholder')} className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
            <input value={skills} onChange={(e) => setSkills(e.target.value)} placeholder={t('skillsPlaceholder')} className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
          </div>
          <textarea value={summary} onChange={(e) => setSummary(e.target.value)} placeholder={t('summaryPlaceholder')} rows={2} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
          <div className="flex justify-end gap-2">
            <button onClick={onToggleEdit} className="px-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">{t('cancel')}</button>
            <button onClick={submit} disabled={pending} className="px-3 py-1.5 text-xs bg-emerald-600 text-white rounded-lg disabled:opacity-50 flex items-center gap-1.5"><CheckCircle2 className="w-3.5 h-3.5" /> {t('submitCorrection')}</button>
          </div>
        </div>
      )}

      {/* Actions */}
      {!editing && (
        <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-slate-100">
          <button onClick={onToggleEdit} className="text-[11px] px-2.5 py-1 bg-slate-100 text-slate-600 rounded-lg hover:bg-slate-200 flex items-center gap-1"><Pencil className="w-3 h-3" /> {t('correction')}</button>
          {(item.processing_status === 'failed' || item.processing_status === 'permanently_failed' || item.processing_status === 'needs_review') && (
            <button onClick={onRetry} disabled={pending} className="text-[11px] px-2.5 py-1 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 flex items-center gap-1 disabled:opacity-50"><RefreshCw className="w-3 h-3" /> {t('retryParse')}</button>
          )}
          <button onClick={onDismiss} disabled={pending} className="text-[11px] px-2.5 py-1 bg-rose-50 text-rose-600 rounded-lg hover:bg-rose-100 flex items-center gap-1 disabled:opacity-50"><Ban className="w-3 h-3" /> {t('dismissAction')}</button>
        </div>
      )}
    </div>
  );
}