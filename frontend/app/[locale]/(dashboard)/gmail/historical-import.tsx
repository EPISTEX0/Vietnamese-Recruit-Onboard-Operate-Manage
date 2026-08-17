'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useFormatter } from 'next-intl';
import { History, Loader2, Tag, Play, Ban, Clock, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';
import * as gmailApi from '@/lib/api/gmail';
import { useToast } from './toast';
import { fmtDate, apiErrorText } from './helpers';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function HistoricalImportPanel() {
  const t = useTranslations('gmail');
  const format = useFormatter();
  const { push } = useToast();
  const qc = useQueryClient();
  const [days, setDays] = useState(7);

  // --- mutations ---
  const previewMut = useMutation({
    mutationFn: (d: number) => gmailApi.previewImport(d),
    onError: (e) => push({ kind: 'error', text: t('previewFail', { error: apiErrorText(e) }) }),
  });

  const startMut = useMutation({
    mutationFn: (d: number) => gmailApi.startImport(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['gmail-import-status'] });
      push({ kind: 'success', text: t('importStarted') });
    },
    onError: (e) => push({ kind: 'error', text: t('importStartFail', { error: apiErrorText(e) }) }),
  });

  const cancelMut = useMutation({
    mutationFn: () => gmailApi.cancelImport(),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['gmail-import-status'] });
      if (res.status === 'cancelled') {
        push({ kind: 'info', text: t('importCancelled') });
      } else {
        push({ kind: 'info', text: t('noRunningJob') });
      }
    },
    onError: (e) => push({ kind: 'error', text: t('cancelFail', { error: apiErrorText(e) }) }),
  });

  // --- status query ---
  const status = useQuery({
    queryKey: ['gmail-import-status'],
    queryFn: gmailApi.getImportStatus,
    staleTime: 15_000,
  });

  const running = status.data?.status === 'running' || status.data?.status === 'pending';
  const done   = status.data?.status === 'completed';
  const failed = status.data?.status === 'failed';

  // --- poll while running ---
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => qc.invalidateQueries({ queryKey: ['gmail-import-status'] }), 4000);
    return () => clearInterval(t);
  }, [running, qc]);

  // --- auto-refresh messages on completion ---
  const prevDoneRef = React.useRef(false);
  useEffect(() => {
    if (done && !prevDoneRef.current) {
      qc.invalidateQueries({ queryKey: ['gmail-messages'] });
      push({ kind: 'success', text: t('importCompleted') });
    }
    prevDoneRef.current = done;
  }, [done, qc, push, t]);

  // --- handlers ---
  const handlePreview = useCallback(() => previewMut.mutate(days), [days, previewMut]);
  const handleStart   = useCallback(() => startMut.mutate(days),   [days, startMut]);
  const handleCancel  = useCallback(() => cancelMut.mutate(),      [cancelMut]);

  // --- derived data ---
  const hasStatus  = status.data && status.data.status !== 'none';
  const total      = status.data?.total_count ?? 0;
  const processed  = status.data?.processed_count ?? 0;
  const progressPct = total > 0 ? Math.round((processed / total) * 100) : 0;

  const preview = previewMut.data;

  return (
    <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm">
      {/* ── Title ── */}
      <div className="flex items-center gap-2 mb-3">
        <History className="w-4 h-4 text-indigo-600" />
        <h2 className="font-bold text-sm text-slate-900">{t('importTitle')}</h2>
        {hasStatus && (
          <span className={`ml-auto text-[10px] font-medium px-2 py-0.5 rounded-full border ${trangThaiMau(t, status.data?.status)}`}>
            {trangThaiNhan(t, status.data?.status)}
          </span>
        )}
      </div>

      {/* ── Controls ── */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500">{t('timeRange')}</span>
        {[7, 30].map((d) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            disabled={running || startMut.isPending}
            className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
              days === d
                ? 'bg-indigo-600 text-white border-indigo-600'
                : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50 disabled:opacity-40'
            }`}
          >
            {t('daysLabel', { days: d })}
          </button>
        ))}

        {/* Preview */}
        <button
          onClick={handlePreview}
          disabled={running || previewMut.isPending}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 transition-colors"
        >
          {previewMut.isPending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Tag className="w-3.5 h-3.5" />
          )}
          {t('previewBtn')}
        </button>

        {/* Start */}
        <button
          onClick={handleStart}
          disabled={running || startMut.isPending}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {startMut.isPending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
          {t('startBtn')}
        </button>

        {/* Cancel (only when running) */}
        {running && (
          <button
            onClick={handleCancel}
            disabled={cancelMut.isPending}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-rose-50 text-rose-600 border border-rose-200 hover:bg-rose-100 disabled:opacity-40 transition-colors"
          >
            {cancelMut.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Ban className="w-3.5 h-3.5" />
            )}
            {t('cancelBtn')}
          </button>
        )}
      </div>

      {/* ── Preview result ── */}
      {preview && (
        <div className="mt-3 px-3 py-2.5 bg-indigo-50/50 rounded-lg border border-indigo-100 text-xs text-slate-600 leading-relaxed">
          <Tag className="w-3 h-3 text-indigo-500 inline align-[-2px] mr-1.5" />
          {t('previewResult', {
            count: preview.estimated_count,
            imported: preview.already_imported_count,
            days: preview.days,
          })}
        </div>
      )}

      {/* ── Status section ── */}
      {hasStatus && (
        <div className="mt-3 p-3 bg-slate-50 rounded-lg border border-slate-100 text-xs space-y-2">
          {/* Line 1: status + progress */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Status icon */}
            {running && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" />}
            {done && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />}
            {failed && <XCircle className="w-3.5 h-3.5 text-rose-500" />}
            {!running && !done && !failed && (
              <AlertTriangle className="w-3.5 h-3.5 text-slate-400" />
            )}

            <span className="text-slate-500">
              {status.data.days != null && t('daysLabel', { days: status.data.days })}
            </span>

            {/* Numeric progress */}
            <span className="font-mono font-semibold text-slate-800">
              {processed}/{total}
            </span>

            {/* Percentage */}
            {total > 0 && (
              <span className="text-[10px] text-slate-400">
                ({progressPct}%)
              </span>
            )}

            {/* Start time */}
            {status.data.started_at && (
              <span className="inline-flex items-center gap-1 text-slate-400 ml-auto">
                <Clock className="w-3 h-3" />
                {fmtDate(status.data.started_at, format)}
              </span>
            )}
          </div>

          {/* Progress bar (only while running) */}
          {running && total > 0 && (
            <div className="w-full h-1.5 bg-slate-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all duration-700 ease-out"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          )}

          {/* Line 2: stats */}
          <div className="flex items-center gap-3 text-slate-400 flex-wrap">
            <span>
              {t('applications', { count: status.data.job_application_count })}
            </span>
            <span>
              {t('errors')}:{' '}
              <b className={status.data.errors > 0 ? 'text-rose-600' : 'text-slate-600'}>
                {status.data.errors}
              </b>
            </span>
            {status.data.completed_at && (
              <span className="inline-flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" />
                {t('completedAt', { time: fmtDate(status.data.completed_at, format) })}
              </span>
            )}
          </div>

          {/* Error line (if any) */}
          {status.data.error_message && (
            <p className="text-rose-500 bg-rose-50 rounded-md px-2 py-1 text-[11px] leading-relaxed">
              {status.data.error_message}
            </p>
          )}
        </div>
      )}

      {/* ── Loading skeleton for status ── */}
      {status.isLoading && (
        <div className="mt-3 p-3 bg-slate-50 rounded-lg border border-slate-100 animate-pulse">
          <div className="flex items-center gap-2">
            <div className="w-3.5 h-3.5 bg-slate-200 rounded-full" />
            <div className="h-3 w-32 bg-slate-200 rounded" />
          </div>
          <div className="mt-2 flex items-center gap-3">
            <div className="h-3 w-24 bg-slate-200 rounded" />
            <div className="h-3 w-16 bg-slate-200 rounded" />
          </div>
        </div>
      )}

      {/* ── Status query error ── */}
      {status.isError && (
        <div className="mt-3 p-3 bg-rose-50 rounded-lg border border-rose-100 text-xs text-rose-600">
          {t('statusLoadError', { error: apiErrorText(status.error) })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers for status labels
// ---------------------------------------------------------------------------
function trangThaiNhan(t: (key: string) => string, status: string | undefined | null): string {
  const map: Record<string, string> = {
    running: t('statusRunning'),
    pending: t('statusPending'),
    completed: t('statusCompleted'),
    cancelled: t('statusCancelled'),
    failed: t('statusFailed'),
  };
  if (!status) return t('statusNone');
  return map[status] ?? status;
}

function trangThaiMau(t: (key: string) => string, status: string | undefined | null): string {
  if (!status) return 'bg-slate-50 text-slate-400 border-slate-100';
  const map: Record<string, string> = {
    running: 'bg-amber-50 text-amber-700 border-amber-200',
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    cancelled: 'bg-slate-100 text-slate-500 border-slate-200',
    failed: 'bg-rose-50 text-rose-700 border-rose-200',
  };
  return map[status] ?? 'bg-slate-50 text-slate-500 border-slate-100';
}
