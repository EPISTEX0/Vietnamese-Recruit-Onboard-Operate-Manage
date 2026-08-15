'use client';

/** Audit log — who changed what, filterable by action type and date range. */

import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useLocale } from 'next-intl';
import { FileText, Loader2, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type { AuditLog } from '@/lib/api/admin';
import { PageHeader, AUDIT_ACTION_GROUPS, formatAuditDetails } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function AuditLogPage() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const locale = useLocale();
  const ta = useTranslations('audit');
  const [page, setPage] = useState(1);
  const [actionType, setActionType] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [datePreset, setDatePreset] = useState<string>('all');

  const applyDatePreset = (preset: string) => {
    setDatePreset(preset);
    const today = new Date();
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    if (preset === 'today') {
      setStartDate(fmt(today));
      setEndDate(fmt(today));
    } else if (preset === '7days') {
      const d = new Date(today); d.setDate(d.getDate() - 7);
      setStartDate(fmt(d));
      setEndDate(fmt(today));
    } else if (preset === '30days') {
      const d = new Date(today); d.setDate(d.getDate() - 30);
      setStartDate(fmt(d));
      setEndDate(fmt(today));
    } else {
      setStartDate('');
      setEndDate('');
    }
    setPage(1);
  };

  const params: admin.AuditLogQueryParams = { page, page_size: 15 };
  if (actionType) params.action_type = actionType;
  if (startDate) params.start_date = startDate;
  if (endDate) params.end_date = endDate;

  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ['audit-logs', params], queryFn: () => admin.getAuditLogs(params), staleTime: 30_000 });
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const datePresets = [
    { key: 'all', label: t('all') },
    { key: 'today', label: t('today') },
    { key: '7days', label: t('last7Days') },
    { key: '30days', label: t('last30Days') },
  ];

  return (
    <div className="space-y-5">
      <PageHeader icon={FileText} title={t('auditLog')} subtitle={t('auditLogDesc')} />
      <SectionCard>
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <select value={actionType} onChange={(e) => { setActionType(e.target.value); setPage(1); }} className="h-9 pl-3 pr-8 text-[13px] border border-slate-200 rounded-lg bg-slate-50 focus:bg-white focus:border-indigo-400 outline-none transition-all cursor-pointer max-w-[200px]">
            <option value="">{t('allActions')}</option>
            {AUDIT_ACTION_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.items.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <div className="flex items-center h-9 border border-slate-200 rounded-lg bg-slate-50 overflow-hidden">
            {datePresets.map((p) => (
              <button
                key={p.key}
                onClick={() => applyDatePreset(p.key)}
                className={`h-full px-2.5 text-[12px] font-medium transition-colors border-r border-slate-200 last:border-r-0 ${datePreset === p.key ? 'bg-indigo-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
              >
                {p.label}
              </button>
            ))}
          </div>
          {(startDate || endDate) && (
            <span className="text-[11px] text-slate-400">
              {startDate && `${t('from')} ${new Date(startDate).toLocaleDateString('vi-VN')}`}
              {startDate && endDate && ' → '}
              {endDate && `${t('to')} ${new Date(endDate).toLocaleDateString('vi-VN')}`}
            </span>
          )}
          <button onClick={() => qc.invalidateQueries({ queryKey: ['audit-logs'] })} className="h-9 px-3 text-[13px] font-medium rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors flex items-center gap-1.5 ml-auto"><RefreshCw className="w-3.5 h-3.5" /> {t('refresh')}</button>
        </div>
        {/* Error before data — see `Empty` in ../_components/console-ui. The
            filtered variant below makes it worse here: a failed request under an
            active filter reads as "no records match", i.e. as an answer. */}
        {isError ? <ErrorBox text={apiErrorText(error)} onRetry={() => { void refetch(); }} /> :
          isPending ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
          (data?.items?.length ?? 0) === 0 ? <Empty text={actionType || startDate || endDate ? t('noFilterResults') : t('noActivityYet')} /> :
          <div className="space-y-2">
            {data!.items.map((log: AuditLog) => (
              <div key={log.id} className="p-3 bg-slate-50 rounded-xl flex items-start gap-3">
                <div className="w-8 h-8 rounded-lg bg-indigo-100 text-indigo-600 flex items-center justify-center text-[11px] font-bold shrink-0">{log.admin_email?.[0]?.toUpperCase() ?? '?'}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[13px] font-medium text-slate-700">{log.admin_email}</span>
                    <span className="text-[10px] bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded font-medium">{ta(log.action_type)}</span>
                  </div>
                  <p className="text-[12px] text-slate-500">{formatAuditDetails(log.details, locale)}</p>
                </div>
                <span className="text-[11px] text-slate-400 shrink-0">{new Date(log.created_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}</span>
              </div>
            ))}
            <div className="flex items-center justify-between pt-3">
              <span className="text-[12px] text-slate-500">{t('pageOf', { page, total: totalPages })} · {data!.total} {t('records')}</span>
              <div className="flex gap-1">
                <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className="p-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-30 transition-colors"><ChevronLeft className="w-4 h-4" /></button>
                <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-30 transition-colors"><ChevronRight className="w-4 h-4" /></button>
              </div>
            </div>
          </div>}
      </SectionCard>
    </div>
  );
}
