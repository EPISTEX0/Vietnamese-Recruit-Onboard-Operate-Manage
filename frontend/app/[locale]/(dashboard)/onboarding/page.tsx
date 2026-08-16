'use client';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckSquare, ChevronDown, ChevronRight, Circle, CheckCircle2, UserCheck, AlertTriangle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import {
  getOnboardingCounts, listOnboardingProcesses, getOnboardingProcess,
  updateTaskStatus,
  type OnboardingProcess, type OnboardingCounts, type OnboardingTaskStatus, type ProcessFilter,
} from '@/lib/api/onboarding';
import { ErrorBanner, Loading, EmptyState, StatusPill } from '@/components/shared-ui';

const FILTERS: { key: ProcessFilter; label: string }[] = [
  { key: 'all', label: 'all' },
  { key: 'in_progress', label: 'inProgressLabel' },
  { key: 'complete', label: 'completeLabel' },
];

export default function OnboardingPage() {
  const qc = useQueryClient();
  const t = useTranslations('onboarding');
  const [filter, setFilter] = useState<ProcessFilter>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<unknown>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['onboarding'] });
    qc.invalidateQueries({ queryKey: ['recruitment-candidates'] });
  };

  const { data: counts } = useQuery<OnboardingCounts>({ queryKey: ['onboarding', 'counts'], queryFn: getOnboardingCounts, staleTime: 30 * 1000 });
  const { data, isLoading, error } = useQuery({
    queryKey: ['onboarding', 'list', filter],
    queryFn: () => listOnboardingProcesses(filter),
    staleTime: 30 * 1000,
  });

  const tasksM = useMutation({
    mutationFn: ({ taskId, status }: { taskId: string; status: OnboardingTaskStatus }) => updateTaskStatus(taskId, status),
    onSuccess: () => { invalidate(); setActionError(null); },
    onError: (e: unknown) => setActionError(e),
  });

  const processes = data?.items ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-indigo-600">
        <CheckSquare className="w-5 h-5" />
        <h1 className="text-xl font-bold text-slate-900">{t('title')}</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        {t.rich('subtitle', { em: (c) => <em>{c}</em>, code: (c) => <code>{c}</code>, strong: (c) => <strong>{c}</strong> })}
      </p>

      
      {counts && (
        <div className="grid grid-cols-3 gap-2">
          <Count label={t('total')} value={counts.total} />
          <Count label={t('inProgress')} value={counts.in_progress} tone="indigo" />
          <Count label={t('complete')} value={counts.complete} tone="emerald" />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)} className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${filter === f.key ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
            {t(f.label)} {counts && f.key !== 'all' && <span className="opacity-70 font-mono">{f.key === 'in_progress' ? counts.in_progress : counts.complete}</span>}
          </button>
        ))}
      </div>

      {!!actionError && <ErrorBanner error={actionError} />}

      {isLoading ? (
        <Loading label={t('loading')} />
      ) : error ? (
        <ErrorBanner error={error} />
      ) : processes.length === 0 ? (
        <EmptyState filtered={filter !== 'all'} onReset={() => setFilter('all')} />
      ) : (
        <div className="space-y-2">
          {processes.map((proc) => (
            <ProcessRow
              key={proc.id}
              proc={proc}
              t={t}
              expanded={!!expanded[proc.id]}
              onToggle={() => setExpanded((p) => ({ ...p, [proc.id]: !p[proc.id] }))}
              onToggleTask={(taskId, status) => tasksM.mutate({ taskId, status })}
              pending={tasksM.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function Count({ label, value, tone = 'slate' }: { label: string; value: number; tone?: 'slate' | 'indigo' | 'emerald' }) {
  const tones: Record<string, string> = { slate: 'text-slate-800', indigo: 'text-indigo-600', emerald: 'text-emerald-600' };
  return (
    <div className="p-3 bg-white rounded-xl border border-slate-200">
      <div className={`text-2xl font-bold ${tones[tone]}`}>{value}</div>
      <p className="text-[10px] font-mono uppercase text-slate-400">{label}</p>
    </div>
  );
}

function ProcessRow({
  proc, t, expanded, onToggle, onToggleTask, pending,
}: {
  proc: OnboardingProcess;
  t: ((key: string, values?: Record<string, string | number | Date>) => string) & { rich: (key: string, values?: Record<string, string | number | Date | ((chunks: React.ReactNode) => React.ReactNode)>) => React.ReactNode };
  expanded: boolean;
  onToggle: () => void;
  onToggleTask: (taskId: string, status: OnboardingTaskStatus) => void;
  pending: boolean;
}) {
  const isComplete = proc.status === 'complete';
  const missing = proc.missing_setup_fields ?? [];
  const tasks = proc.tasks ?? [];
  const allMandatory = tasks.length > 0;
  const canActivate = allMandatory && tasks.every((t) => t.status === 'done') && isComplete;

  return (
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100 overflow-hidden">
      <button onClick={onToggle} className="w-full flex items-start gap-3 p-4 text-left">
        <div className="mt-0.5 text-slate-400">{expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <StatusPill label={isComplete ? t('processComplete') : t('processActive')} tone={isComplete ? 'emerald' : 'indigo'} />
            <span className="text-[11px] font-mono text-slate-400">{t('taskProgress', { done: proc.completed_count, total: proc.total_count })}</span>
            {proc.employee_code && <span className="text-[10px] font-mono text-slate-400">{proc.employee_code}</span>}
          </div>
          <p className="font-semibold text-sm text-slate-900 truncate">{proc.employee_full_name}</p>
          <p className="text-xs text-slate-500 truncate">{proc.employee_email}{proc.job_opening ? ` · ${proc.job_opening}` : ''}</p>
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-slate-100 pt-3 space-y-3">
          {missing.length > 0 && (
            <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{t.rich('missingFields', { fields: missing.join(', '), code: (c) => <code>{c}</code> })}</span>
            </div>
          )}

          {tasks.length === 0 ? (
            <p className="text-xs text-slate-400">{t('noTasks')}</p>
          ) : (
            <ul className="space-y-1.5">
              {tasks.sort((a, b) => a.order_index - b.order_index).map((task) => (
                <li key={task.id} className="flex items-center gap-2.5">
                  <button
                    onClick={() => onToggleTask(task.id, task.status === 'done' ? 'pending' : 'done')}
                    disabled={pending || isComplete}
                    className={`shrink-0 ${task.status === 'done' ? 'text-emerald-600' : 'text-slate-300 hover:text-indigo-500'} disabled:opacity-50`}
                  >
                    {task.status === 'done' ? <CheckCircle2 className="w-5 h-5" /> : <Circle className="w-5 h-5" />}
                  </button>
                  <span className={`text-sm ${task.status === 'done' ? 'line-through text-slate-400' : 'text-slate-700'}`}>{task.name}</span>
                  {task.completed_by_name && <span className="text-[10px] font-mono text-slate-400 ml-auto">{task.completed_by_name} · {task.completed_at && new Date(task.completed_at).toLocaleString('vi-VN')}</span>}
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
            <UserCheck className={`w-4 h-4 ${canActivate ? 'text-emerald-600' : 'text-slate-300'}`} />
            <span className="text-xs text-slate-500">
              {canActivate
                ? t('processDone')
                : isComplete
                  ? t('employeeActive')
                  : t('activationHint')}
            </span>
          </div>
          {proc.accepted_at && <p className="text-[10px] font-mono text-slate-400">{t('acceptedAt', { date: new Date(proc.accepted_at).toLocaleString('vi-VN') })}</p>}
        </div>
      )}
    </div>
  );
}
