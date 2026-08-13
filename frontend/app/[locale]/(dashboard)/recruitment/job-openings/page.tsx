'use client';
import { useTranslations } from 'next-intl';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Briefcase, Plus, X, Pencil, Eye, EyeOff } from 'lucide-react';
import {
  listJobOpenings, getJobOpeningMetrics,
  createJobOpening, updateJobOpening, openJobOpening, closeJobOpening, cancelJobOpening,
  type JobOpeningListItem, type JobOpeningMetrics, type JobOpeningCreateInput, type JobOpeningUpdateInput,
} from '@/lib/api/recruitment';
import { listPositions } from '@/lib/api/positions';
import type { Position } from '@/lib/api/types';

import { ErrorBanner, Loading, EmptyState, StatusPill, JOB_STATUS_META } from '@/components/shared-ui';

type StatusFilter = 'all' | 'draft' | 'open' | 'closed' | 'cancelled';

export default function JobOpeningsPage() {
  const tc = useTranslations('common');
  const t = useTranslations('recruitment');
  const qc = useQueryClient();
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['recruitment-job-openings'] });
    qc.invalidateQueries({ queryKey: ['recruitment-candidates'] });
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ['recruitment-job-openings', 'list', filter, search],
    queryFn: () =>
      listJobOpenings({
        search: search.trim() || undefined,
        status: filter === 'all' ? undefined : [filter],
        page_size: 100,
      }),
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });
  const { data: metrics } = useQuery<JobOpeningMetrics>({ queryKey: ['recruitment-job-openings', 'metrics'], queryFn: getJobOpeningMetrics, staleTime: 30 * 1000, placeholderData: (prev) => prev });
  const { data: positions } = useQuery<Position[]>({ queryKey: ['positions'], queryFn: listPositions, staleTime: 5 * 60 * 1000, placeholderData: (prev) => prev });

  const createM = useMutation({
    mutationFn: (d: JobOpeningCreateInput) => createJobOpening(d),
    onSuccess: () => { invalidate(); setCreateOpen(false); setActionError(null); },
    onError: (e: unknown) => setActionError(e),
  });
  const openM = useMutation({ mutationFn: openJobOpening, onSuccess: () => { invalidate(); setActionError(''); }, onError: (e: unknown) => setActionError(e) });
  const closeM = useMutation({ mutationFn: closeJobOpening, onSuccess: () => { invalidate(); setActionError(''); }, onError: (e: unknown) => setActionError(e) });
  const cancelM = useMutation({ mutationFn: cancelJobOpening, onSuccess: () => { invalidate(); setActionError(''); }, onError: (e: unknown) => setActionError(e) });
  const updateM = useMutation({
    mutationFn: ({ id, data }: { id: string; data: JobOpeningUpdateInput }) => updateJobOpening(id, data),
    onSuccess: () => { invalidate(); setEditingId(null); setActionError(''); },
    onError: (e: unknown) => setActionError(e),
  });

  const jobs = data?.job_openings ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-indigo-600">
          <Briefcase className="w-5 h-5" />
          <h1 className="text-xl font-bold text-slate-900">{t('jobOpeningsTitle')}</h1>
        </div>
        <button onClick={() => setCreateOpen(true)} className="px-3 py-2 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg flex items-center gap-1.5">
          <Plus className="w-4 h-4" /> {t('createJobOpening')}
        </button>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        {t('jobOpeningsDesc')}
      </p>

      {/* Metrics summary */}
      {metrics && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          <MetricCard label={t('total')} value={metrics.total_job_openings} />
          <MetricCard label={t('draft')} value={metrics.draft_count} tone="slate" />
          <MetricCard label={t('open')} value={metrics.open_count} tone="emerald" />
          <MetricCard label={t('closed')} value={metrics.closed_count} tone="indigo" />
          <MetricCard label={t('cancelled')} value={metrics.cancelled_count} tone="rose" />
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {(['all', 'draft', 'open', 'closed', 'cancelled'] as StatusFilter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${filter === f ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
            {JOB_STATUS_META[f]?.labelKey ? tc(JOB_STATUS_META[f].labelKey) : t('all')}
          </button>
        ))}
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('searchJobOpenings')} className="ml-auto px-3 py-1 text-xs bg-white border border-slate-200 rounded-lg w-48" />
      </div>

      {!!actionError && <ErrorBanner error={actionError} />}

      {createOpen && (
        <JobForm
          positions={positions ?? []}
          pending={createM.isPending}
          onCancel={() => setCreateOpen(false)}
          onSubmit={(d) => createM.mutate(d)}
        />
      )}

      {isLoading ? (
        <Loading label={t('loadingJobOpenings')} />
      ) : error ? (
        <ErrorBanner error={error} />
      ) : jobs.length === 0 ? (
        <EmptyState filtered={filter !== 'all' || search.trim() !== ''} onReset={() => { setFilter('all'); setSearch(''); }} />
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => {
            const meta = JOB_STATUS_META[job.status] ?? { label: job.status, tone: 'slate' as const, labelKey: job.status };
            const isEditing = editingId === job.id;
            return (
              <div key={job.id} className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
                {isEditing ? (
                  <JobForm
                    positions={positions ?? []}
                    pending={updateM.isPending}
                    onCancel={() => setEditingId(null)}
                    onSubmit={(d) => updateM.mutate({ id: job.id, data: { title: d.title, description: d.description, target_headcount: d.target_headcount } })}
                    initial={{ title: job.title, description: '', target_headcount: job.target_headcount, position_id: job.position_id, status: job.status }}
                    editMode
                  />
                ) : (
                  <>
                    <div className="flex items-start justify-between gap-3 flex-wrap">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <StatusPill status={job.status} label={tc(meta.labelKey)} tone={meta.tone} />
                          <span className="text-[11px] font-mono text-slate-400">{job.position_name}</span>
                        </div>
                        <p className="font-semibold text-sm text-slate-900">{job.title}</p>
                      </div>
                      {/* Headcount */}
                      <div className="text-right">
                        <p className="text-xs font-mono text-slate-500">
                          <span className="text-emerald-600 font-bold">{job.accepted_count}</span>
                          <span className="text-slate-400">{t('ofHeadcount', { count: job.target_headcount })}</span>
                        </p>
                        <p className="text-[10px] text-slate-400">{t('candidatesInReview', { count: job.total_candidates })}</p>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-slate-100">
                      {job.status === 'draft' && (
                        <button onClick={() => openM.mutate(job.id)} disabled={openM.isPending} className="text-[11px] px-2.5 py-1 bg-emerald-50 text-emerald-600 rounded-lg hover:bg-emerald-100 disabled:opacity-50">{t('openRecruitment')}</button>
                      )}
                      {job.status === 'open' && (
                        <button onClick={() => closeM.mutate(job.id)} disabled={closeM.isPending} className="text-[11px] px-2.5 py-1 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 disabled:opacity-50">{t('close')}</button>
                      )}
                      {(job.status === 'draft' || job.status === 'open') && (
                        <button onClick={() => cancelM.mutate(job.id)} disabled={cancelM.isPending} className="text-[11px] px-2.5 py-1 bg-rose-50 text-rose-600 rounded-lg hover:bg-rose-100 disabled:opacity-50">{t('cancel')}</button>
                      )}
                      {job.status !== 'cancelled' && (
                        <button onClick={() => setEditingId(job.id)} className="text-[11px] px-2.5 py-1 bg-slate-100 text-slate-600 rounded-lg hover:bg-slate-200 flex items-center gap-1">
                          <Pencil className="w-3 h-3" /> {t('edit')}
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, tone = 'slate' }: { label: string; value: number; tone?: 'slate' | 'emerald' | 'indigo' | 'rose' }) {
  const t = useTranslations('recruitment');
  const tones: Record<string, string> = {
    slate: 'text-slate-700', emerald: 'text-emerald-600', indigo: 'text-indigo-600', rose: 'text-rose-600',
  };
  return (
    <div className="p-3 bg-white rounded-xl border border-slate-200">
      <div className={`text-xl font-bold ${tones[tone]}`}>{value}</div>
      <p className="text-[10px] font-mono uppercase text-slate-400">{label}</p>
    </div>
  );
}

function JobForm({
  positions, pending, onCancel, onSubmit, initial, editMode = false,
}: {
  positions: Position[];
  pending: boolean;
  onCancel: () => void;
  onSubmit: (d: JobOpeningCreateInput) => void;
  initial?: { title: string; description: string; target_headcount: number; position_id: string; status: string };
  editMode?: boolean;
}) {
  const t = useTranslations('recruitment');
  const [title, setTitle] = useState(initial?.title ?? '');
  const [positionId, setPositionId] = useState(initial?.position_id ?? '');
  const [headcount, setHeadcount] = useState(initial?.target_headcount ?? 1);
  const [description, setDescription] = useState(initial?.description ?? '');
  const [status, setStatus] = useState<'draft' | 'open'>(initial?.status === 'open' ? 'open' : 'draft');

  const submit = () => {
    if (!title.trim() || !positionId || headcount < 1) return;
    onSubmit({ title: title.trim(), position_id: positionId, target_headcount: headcount, description, status });
  };

  return (
    <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-slate-900">{editMode ? t('editJobTitle') : t('createJobTitle')}</h3>
        <button onClick={onCancel} className="text-slate-400 hover:text-slate-600"><X className="w-4 h-4" /></button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('title')}</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('titlePlaceholder')} className="w-full px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg" />
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('position')}</label>
          <select value={positionId} onChange={(e) => setPositionId(e.target.value)} className="w-full px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg">
            <option value="">{t('selectPosition')}</option>
            {positions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {positions.length === 0 && <p className="text-[10px] text-amber-600 mt-1">{t('noPositions')}</p>}
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('targetHeadcount')}</label>
          <input type="number" min={1} value={headcount} onChange={(e) => setHeadcount(Math.max(1, Number(e.target.value) || 1))} className="w-full px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg" />
        </div>
        {!editMode && (
          <div>
            <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('initialStatus')}</label>
            <select value={status} onChange={(e) => setStatus(e.target.value as 'draft' | 'open')} className="w-full px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg">
              <option value="draft">{t('draft')}</option>
              <option value="open">{t('openNow')}</option>
            </select>
          </div>
        )}
      </div>
      <div>
        <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('description')}</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="w-full px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg" />
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel} className="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg">{t('cancel')}</button>
        <button onClick={submit} disabled={pending || !title.trim() || !positionId} className="px-3 py-2 text-xs bg-indigo-600 text-white rounded-lg disabled:opacity-50">
          {pending ? t('saving') : editMode ? t('save') : t('createJobOpening')}
        </button>
      </div>
    </div>
  );
}