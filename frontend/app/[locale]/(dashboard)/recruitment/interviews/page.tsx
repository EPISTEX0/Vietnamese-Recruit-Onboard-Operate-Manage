'use client';
import { useTranslations } from 'next-intl';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@/i18n/navigation';
import {
  Calendar, AlertTriangle, CheckCircle2, XCircle, Link2Off, CalendarClock, ShieldAlert,
} from 'lucide-react';
import {
  listCandidates, listCalendarConflicts, resolveCalendarConflict,
  type CalendarConflict, type CandidateListItem,
  type CreateInterviewRequest, type InterviewResponse,
} from '@/lib/api/recruitment';
import { getCalendars, selectCalendar, type CalendarListResponse } from '@/lib/api/gmail';
import { ErrorBanner, Loading, EmptyState, StatusPill, CONFLICT_STATUS_META, formatAuditDetails } from '@/components/shared-ui';

export default function InterviewsPage() {
  const tc = useTranslations('common');
  const t = useTranslations('recruitment');
  const router = useRouter();
  const qc = useQueryClient();
  const [actionError, setActionError] = useState<unknown>(null);
  const [resolveTarget, setResolveTarget] = useState<string | null>(null);
  const [resolveChoice, setResolveChoice] = useState<'keep_google' | 'overwrite_vroom'>('keep_google');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'reviewing' | 'interview_scheduled'>('all');

  // Calendar status (GH #214: creating Interview requires selected Calendar)
  const { data: calendars, isLoading: calLoading, isError: calError, error: _calErrorData } = useQuery<CalendarListResponse>({
    queryKey: ['google-calendars'],
    queryFn: getCalendars,
    staleTime: 60 * 1000,
    placeholderData: (prev) => prev,
    retry: 1,
  });
  const selectedCalendarId = calendars?.selected_calendar_id ?? null;
  const googleConnected = (calendars?.calendars?.length ?? 0) > 0;

  // Candidates in interview_scheduled or reviewing to pick from
  const { data: candidatesData } = useQuery({
    queryKey: ['recruitment-candidates', { status: ['reviewing', 'interview_scheduled'], page_size: 100 }],
    queryFn: () => listCandidates({ status: ['reviewing', 'interview_scheduled'], page_size: 100 }),
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });
  const candidates: CandidateListItem[] = candidatesData?.candidates ?? [];
  const filtered = candidates.filter((c) => {
    const q = search.toLowerCase().trim();
    const matchSearch = !q || c.name.toLowerCase().includes(q) || (c.email ?? '').toLowerCase().includes(q) || (c.job_opening_title ?? '').toLowerCase().includes(q);
    const matchStatus = statusFilter === 'all' || c.status === statusFilter;
    return matchSearch && matchStatus;
  });

  // Calendar conflicts (status unresolved)
  const { data: conflictsData, isLoading: confLoading } = useQuery({
    queryKey: ['recruitment-conflicts'],
    queryFn: () => listCalendarConflicts({ status: 'unresolved' }),
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });
  const conflicts: CalendarConflict[] = conflictsData?.conflicts ?? [];

  const selectCalM = useMutation({
    mutationFn: (id: string) => selectCalendar(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['google-calendars'] });  setActionError(null); },
    onError: (e: unknown) => setActionError(e),
  });
  const resolveM = useMutation({
    mutationFn: ({ id, choice }: { id: string; choice: 'keep_google' | 'overwrite_vroom' }) => resolveCalendarConflict(id, { choice }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['recruitment-conflicts'] }); setResolveTarget(null); setActionError(''); },
    onError: (e: unknown) => setActionError(e),
  });

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-indigo-600">
          <Calendar className="w-5 h-5" />
          <h1 className="text-xl font-bold text-slate-900">{t('interviewsTitle')}</h1>
        </div>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        {t('interviewsDesc')}
      </p>

      {!!actionError && <ErrorBanner error={actionError} />}

      {/* Calendar preconditions */}
      <div className={`p-4 rounded-2xl border shadow-sm shadow-slate-100 ${selectedCalendarId ? 'bg-emerald-50/40 border-emerald-200' : 'bg-amber-50/60 border-amber-200'}`}>
        <div className="flex items-center gap-2 mb-2">
          {selectedCalendarId ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <ShieldAlert className="w-4 h-4 text-amber-600" />}
          <h2 className="text-sm font-bold text-slate-900">{t('interviewPrerequisites')}</h2>
        </div>
            {calError ? (
              <div className="text-xs text-rose-600 space-y-1">
                <p className="flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> {t('calendarCheckError')}</p>
                <p className="text-slate-400">{t('calendarCheckErrorHint')}</p>
                <button onClick={() => router.push('/gmail')} className="mt-1 text-indigo-600 hover:text-indigo-700 font-medium">→ {t('openGmailConnection')}</button>
              </div>
            ) : calLoading ? (
              <p className="text-xs text-slate-500">{t('checkingCalendar')}</p>
            ) : !googleConnected ? (
              <div className="text-xs text-slate-600 space-y-1">
                <p>{t('calendarNotConnected')}</p>
                <button onClick={() => router.push('/gmail')} className="mt-1 text-indigo-600 hover:text-indigo-700 font-medium">→ {t('openGmailConnection')}</button>
              </div>
        ) : selectedCalendarId ? (
          <p className="text-xs text-emerald-700">{t('calendarSelected')} <code className="font-mono">{calendars?.calendars?.find((c) => c.id === selectedCalendarId)?.summary ?? selectedCalendarId}</code></p>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-amber-700">{t('calendarPickRequired')}</p>
            <div className="flex flex-wrap gap-2">
              {calendars?.calendars?.map((c) => (
                <button key={c.id} onClick={() => selectCalM.mutate(c.id)} disabled={selectCalM.isPending} className="text-xs px-3 py-1.5 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 disabled:opacity-50">
                  {c.summary}{c.primary ? ' (' + t('primary') + ')' : ''}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Conflicts (410/412) — HR must choose */}
      <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-rose-600" />
          <h2 className="text-sm font-bold text-slate-900">{t('calendarConflicts')}</h2>
          {conflicts.length > 0 && <span className="text-[10px] font-mono bg-rose-50 text-rose-700 px-2 py-0.5 rounded-full">{conflicts.length}</span>}
        </div>
        {confLoading ? (
          <Loading label={t('loadingConflicts')} />
        ) : conflicts.length === 0 ? (
          <p className="text-xs text-slate-400 py-2">{t('noConflicts')}</p>
        ) : (
          <ul className="space-y-2">
            {conflicts.map((c) => (
              <li key={c.id} className="p-3 bg-rose-50/40 border border-rose-200 rounded-xl">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div className="min-w-0">
                    <p className="text-xs text-slate-500">{t('conflictItem', { id: c.id.slice(0, 8), ivId: c.interview_id.slice(0, 8), candId: c.candidate_id.slice(0, 8) })}</p>
                    <div className="flex gap-1.5 mt-1">
                      {(() => { const meta = CONFLICT_STATUS_META[c.status] ?? { label: c.status, tone: "rose" as const, labelKey: c.status }; return <StatusPill status={c.status} label={tc(meta.labelKey)} tone={meta.tone} />; })()}
                      {c.conflict_details && typeof c.conflict_details === 'object' && (
                        <span className="text-[10px] text-slate-400">{formatAuditDetails(c.conflict_details).slice(0, 80)}</span>
                      )}
                    </div>
                  </div>
                  {resolveTarget === c.id ? (
                    <div className="flex items-end gap-2">
                      <select value={resolveChoice} onChange={(e) => setResolveChoice(e.target.value as 'keep_google' | 'overwrite_vroom')} className="text-xs px-2 py-1 bg-white border border-slate-200 rounded">
                        <option value="keep_google">{t('keepGoogle')}</option>
                        <option value="overwrite_vroom">{t('overwriteVroom')}</option>
                      </select>
                      <button onClick={() => resolveM.mutate({ id: c.id, choice: resolveChoice })} disabled={resolveM.isPending} className="text-[11px] px-2.5 py-1 bg-indigo-600 text-white rounded disabled:opacity-50">{t('confirm')}</button>
                      <button onClick={() => setResolveTarget(null)} className="text-[11px] px-2.5 py-1 bg-white border border-slate-200 rounded">{t('cancel')}</button>
                    </div>
                  ) : (
                    <button onClick={() => setResolveTarget(c.id)} className="text-[11px] px-2.5 py-1 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100">{t('resolve')}</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Candidates to interview */}
      <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
        <div className="flex items-center gap-2 mb-3">
          <CalendarClock className="w-4 h-4 text-indigo-600" />
                    <h2 className="text-sm font-bold text-slate-900">{t('candidatesToSchedule')}</h2>
                        <span className="text-[10px] font-mono text-slate-400">{filtered.length}/{candidates.length}</span>
                      </div>
                      <div className="flex gap-2 mb-3">
                        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('searchCandidates')} className="flex-1 px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
                        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as 'all' | 'reviewing' | 'interview_scheduled')} className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">
                          <option value="all">{t('all')}</option>
                          <option value="reviewing">{t('reviewing')}</option>
                          <option value="interview_scheduled">{t('scheduled')}</option>
                        </select>
                      </div>
                      {candidates.length === 0 ? (
                        <EmptyState filtered={false} />
                      ) : filtered.length === 0 ? (
                        <p className="text-xs text-slate-400 py-4 text-center">{search || statusFilter !== 'all' ? t('noCandidatesFiltered') : t('noCandidates')}</p>
                      ) : (
          <ul className="divide-y divide-slate-100">
            {filtered.map((cand) => (
              <li key={cand.id} className="py-2.5 flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800 truncate">{cand.name}</p>
                  <p className="text-xs text-slate-500 truncate">{cand.email}{cand.job_opening_title ? ` · ${cand.job_opening_title}` : ''}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <StatusPill status={cand.status} label={cand.status === 'interview_scheduled' ? t('scheduled') : t('reviewing')} tone={cand.status === 'interview_scheduled' ? 'violet' : 'indigo'} />
                  <button onClick={() => router.push(`/recruitment/candidates/${cand.id}`)} className="text-xs px-2.5 py-1 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50" disabled={!selectedCalendarId}>
                    {selectedCalendarId ? t('openProfile') : t('needCalendar')}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {!selectedCalendarId && googleConnected && (
          <p className="text-[11px] text-amber-600 mt-2">⚠ {t('interviewLocked')}</p>
        )}
      </div>

      <div className="p-3 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-500">
        {t('interviewLifecycle')}
      </div>
    </div>
  );
}