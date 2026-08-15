'use client';

/** Runtime health — per-service status of the deployment's infrastructure. */

import React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useLocale } from 'next-intl';
import { Activity, Loader2, RefreshCw } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type { RuntimeHealthResponse } from '@/lib/api/admin';
import { PageHeader, formatRuntimeDetail, formatLatency } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function SystemHealthPage() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const locale = useLocale();
  const ts = useTranslations('system');
  const { data, isLoading, error, dataUpdatedAt } = useQuery<RuntimeHealthResponse>({
    queryKey: ['runtime-health'], queryFn: admin.getRuntimeHealth, staleTime: 30_000,
  });
  return (
    <div className="space-y-5">
      <PageHeader icon={Activity} title={t('systemHealth')} subtitle={t('healthDesc')} actions={
        <button onClick={() => qc.invalidateQueries({ queryKey: ['runtime-health'] })} aria-label={t('refreshStatus')} title={t('refreshStatus')} className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors"><RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} /></button>
      } />
      <SectionCard>
        {isLoading ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
          error ? <ErrorBox text={apiErrorText(error)} onRetry={() => qc.invalidateQueries({ queryKey: ['runtime-health'] })} /> :
          data ? (
            <div className="space-y-4">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] text-slate-600">{t('overview')}:</span>
                <span className={`text-[12px] font-semibold px-2.5 py-0.5 rounded-full ${data.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : data.status === 'degraded' ? 'bg-amber-50 text-amber-700' : 'bg-rose-50 text-rose-700'}`}>
                  {data.status === 'healthy' ? t('healthy') : data.status === 'degraded' ? t('degraded') : t('error')}
                </span>
                {dataUpdatedAt ? (
                  <span className="text-[10px] text-slate-400">
                    · {t('updatedAt')} {formatRuntimeDetail(`last beat: ${dataUpdatedAt / 1000}`, locale)}
                  </span>
                ) : null}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {data.services.map((s) => (
                  <div key={s.name} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl" title={`${ts(s.name)}: ${s.status === 'healthy' ? t('healthy') : s.status === 'unhealthy' ? t('error') : t('degraded')}`}>
                    <span>{s.status === 'healthy' ? '🟢' : s.status === 'unhealthy' ? '🔴' : '🟡'}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] font-medium text-slate-700">{ts(s.name)}</p>
                      {formatRuntimeDetail(s.detail, locale) && <p className="text-[11px] text-slate-400 truncate">{formatRuntimeDetail(s.detail, locale)}</p>}
                    </div>
                    {s.latency_ms !== null && <span className="text-[11px] text-slate-400">{formatLatency(s.latency_ms, locale)}</span>}
                  </div>
                ))}
              </div>
            </div>
          ) : <Empty text={t('noData')} />}
      </SectionCard>
    </div>
  );
}
