'use client';

/** Runtime health — per-service status of the deployment's infrastructure. */

import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useLocale } from 'next-intl';
import { Activity, Loader2, RefreshCw, RotateCcw } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type {
  RuntimeHealthResponse,
  ClassificationRolloutTelemetryResponse,
  OrganizationAIConfiguration,
} from '@/lib/api/admin';
import { PageHeader, formatRuntimeDetail, formatLatency } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

const ROLLOUT_MODE_LABEL_KEY: Record<OrganizationAIConfiguration['rollout_mode'], string> = {
  stable: 'rolloutModeStable',
  shadow: 'rolloutModeShadow',
  canary: 'rolloutModeCanary',
  full: 'rolloutModeFull',
};

/** One telemetry figure — label, headline value, optional note underneath. */
function MetricCard({ label, sub, value, note, muted }: {
  label: string; sub?: string; value: React.ReactNode; note?: string; muted?: boolean;
}) {
  return (
    <div className="p-3 bg-slate-50 rounded-xl">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-[12px] text-slate-500">{label}</span>
        {sub && <span className="text-[11px] text-slate-400">({sub})</span>}
      </div>
      <div className={muted ? 'text-[13px] text-slate-400 mt-1' : 'text-2xl font-bold text-slate-900'}>{value}</div>
      {note && <p className="text-[11px] text-slate-400 mt-1">{note}</p>}
    </div>
  );
}

/**
 * Telemetry chỉ-đọc + nút rollback cho classification rollout (#422, ADR-0005).
 *
 * Every recall figure here is a `*_recall_proxy` — measured from recent
 * durable rollout events, not the real recall measured on an eval set that
 * ADR-0005's 98% guardrail is defined against. Labeling it plain "Recall"
 * next to that threshold would read as the real number; every recall value
 * below therefore carries the "(proxy)" label and `sample_size` beside it.
 *
 * Version/canary configuration stays API-only on purpose — `mode`,
 * `classifier_version`, `policy_version`, `canary_percentage` are ML-ops
 * language, the wrong audience for an HR-software crisis panel. This panel
 * shows current mode as read-only context (so rollback isn't a blind
 * action) but never lets it be edited here.
 */
function ClassificationRolloutPanel() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const [rollbackMessage, setRollbackMessage] = React.useState<{ text: string; isError: boolean } | null>(null);

  const telemetry = useQuery<ClassificationRolloutTelemetryResponse>({
    queryKey: ['classification-rollout-telemetry'],
    queryFn: () => admin.getClassificationRolloutTelemetry(),
    staleTime: 30_000,
  });
  const config = useQuery<OrganizationAIConfiguration>({
    queryKey: ['ai-config'],
    queryFn: admin.getOrganizationAIConfiguration,
    staleTime: 30_000,
  });

  const rollbackMut = useMutation({
    mutationFn: admin.rollbackClassificationRollout,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-config'] });
      qc.invalidateQueries({ queryKey: ['classification-rollout-telemetry'] });
      setRollbackMessage({ text: t('rolloutRollbackSuccess'), isError: false });
    },
    onError: (e) => setRollbackMessage({ text: apiErrorText(e), isError: true }),
  });

  const handleRollback = () => {
    if (!window.confirm(t('rolloutRollbackConfirm'))) return;
    setRollbackMessage(null);
    rollbackMut.mutate();
  };

  const retry = () => {
    qc.invalidateQueries({ queryKey: ['classification-rollout-telemetry'] });
    qc.invalidateQueries({ queryKey: ['ai-config'] });
  };

  return (
    <SectionCard icon={<RotateCcw className="w-5 h-5 text-indigo-600" />} title={t('rolloutTitle')} desc={t('rolloutDesc')}>
      {/* Error before data — see `Empty` in ../_components/console-ui. */}
      {telemetry.isError || config.isError ? (
        <ErrorBox text={apiErrorText(telemetry.error ?? config.error)} onRetry={retry} />
      ) : telemetry.isPending || config.isPending ? (
        <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" />
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 flex-wrap text-[13px]">
            <span className="text-slate-500">{t('rolloutCurrentMode')}:</span>
            <span className="font-semibold text-slate-900">{t(ROLLOUT_MODE_LABEL_KEY[config.data!.rollout_mode])}</span>
            <span className="text-slate-400">· {t('rolloutStableVersion')}: {config.data!.stable_classifier_version}</span>
            {config.data!.candidate_classifier_version && (
              <span className="text-slate-400">· {t('rolloutCandidateVersion')}: {config.data!.candidate_classifier_version}</span>
            )}
          </div>
          <p className="text-[11px] text-slate-400">{t('rolloutWindow')}</p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <MetricCard
              label={t('rolloutRecallProxy')}
              sub={`${t('rolloutSampleSize')}: ${telemetry.data!.sample_size}`}
              value={formatPercent(telemetry.data!.job_application_recall_proxy)}
              note={t('rolloutRecallProxyNote')}
            />
            <MetricCard
              label={t('rolloutStableRecallProxy')}
              value={formatPercent(telemetry.data!.stable_recall_proxy)}
            />
            <MetricCard
              label={t('rolloutNoCvRecallProxy')}
              value={telemetry.data!.no_cv_recall_proxy === null
                ? t('rolloutNoCvRecallProxyNoData')
                : formatPercent(telemetry.data!.no_cv_recall_proxy)}
              muted={telemetry.data!.no_cv_recall_proxy === null}
            />
            <MetricCard
              label={t('rolloutProviderErrorRate')}
              value={formatPercent(telemetry.data!.provider_error_rate)}
            />
            <MetricCard
              label={t('rolloutP95Latency')}
              value={`${telemetry.data!.p95_latency_ms} ms`}
            />
            <MetricCard
              label={t('rolloutReviewRate')}
              value={formatPercent(telemetry.data!.review_rate)}
            />
          </div>

          <div className="pt-2 border-t border-slate-100 space-y-2">
            <button
              onClick={handleRollback}
              disabled={rollbackMut.isPending}
              className="inline-flex items-center gap-2 h-10 px-5 text-sm font-semibold rounded-xl bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50 transition-all shadow-sm shadow-rose-200"
            >
              {rollbackMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
              {rollbackMut.isPending ? t('rolloutRollingBack') : t('rolloutRollback')}
            </button>
            {rollbackMessage && (
              <p className={`text-[12px] ${rollbackMessage.isError ? 'text-rose-600' : 'text-emerald-600'}`}>
                {rollbackMessage.text}
              </p>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

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
      <ClassificationRolloutPanel />
    </div>
  );
}
