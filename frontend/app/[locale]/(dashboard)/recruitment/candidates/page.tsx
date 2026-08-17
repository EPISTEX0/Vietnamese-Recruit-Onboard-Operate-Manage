'use client';
import { useTranslations } from 'next-intl';

import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from '@/i18n/navigation';
import { UserCheck, Search, ChevronLeft, ChevronRight } from 'lucide-react';
import {
  listCandidates, type CandidateStatus, type CandidateListParams,
} from '@/lib/api/recruitment';
import {
  ErrorBanner, Loading, EmptyState, StatusPill,
  CANDIDATE_STATUS_META, confidencePct,
} from '@/components/shared-ui';

const STATUSES: CandidateStatus[] = ['new', 'reviewing', 'interview_scheduled', 'accepted', 'rejected', 'archived'];
const PAGE_SIZE = 12;

export default function CandidatesPage() {
  const tc = useTranslations('common');
  const t = useTranslations('recruitment');
  const router = useRouter();
  const [search, setSearch] = useState('');
  const [activeStatuses, setActiveStatuses] = useState<CandidateStatus[]>([]);
  const [page, setPage] = useState(1);

  const params: CandidateListParams = {
    page,
    page_size: PAGE_SIZE,
    search: search.trim() || undefined,
    status: activeStatuses.length ? activeStatuses : undefined,
  };

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ['recruitment-candidates', params],
    queryFn: () => listCandidates(params),
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });

  const toggleStatus = (s: CandidateStatus) => {
    setActiveStatuses((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
    setPage(1);
  };

  const candidates = data?.candidates ?? [];
  const total = data?.total_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = search.trim() !== '' || activeStatuses.length > 0;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-indigo-600">
        <UserCheck className="w-5 h-5" />
        <h1 className="text-xl font-bold text-slate-900">{t('candidatesTitle')}</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        {t('candidatesDesc')}
      </p>

      {/* Filters */}
      <div className="flex flex-col gap-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            placeholder={t('searchCandidates')}
            className="w-full pl-9 pr-3 py-2 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:border-indigo-500"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {STATUSES.map((s) => {
            const meta = CANDIDATE_STATUS_META[s];
            const active = activeStatuses.includes(s);
            return (
              <button
                key={s}
                onClick={() => toggleStatus(s)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all border ${active ? 'bg-indigo-600 border-indigo-600 text-white' : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'}`}
              >
                {tc(meta.labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      {isLoading ? (
        <Loading label={t('loadingCandidates')} />
      ) : error ? (
        <ErrorBanner error={error} />
      ) : candidates.length === 0 ? (
        <EmptyState filtered={filtered} onReset={() => { setSearch(''); setActiveStatuses([]); }} />
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {candidates.map((c) => {
              const meta = CANDIDATE_STATUS_META[c.status] ?? { label: c.status, tone: 'slate' as const, labelKey: c.status };
              return (
                <button
                  key={c.id}
                  onClick={() => router.push(`/recruitment/candidates/${c.id}`)}
                  className="text-left p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100 hover:border-indigo-200 hover:shadow-indigo-100 transition-all"
                >
                  <div className="flex items-center justify-between mb-2">
                    <StatusPill label={tc(meta.labelKey)} tone={meta.tone} />
                    <span className="text-[10px] font-mono text-slate-400">{confidencePct(c.confidence_score)}</span>
                  </div>
                  <p className="font-semibold text-sm text-slate-900 truncate">{c.name}</p>
                  <p className="text-xs text-slate-500 truncate">{c.email}</p>
                  {c.phone && <p className="text-xs text-slate-400 truncate">{c.phone}</p>}
                  {c.job_opening_title && (
                    <p className="text-[11px] text-indigo-600 mt-2 truncate">📍 {c.job_opening_title}</p>
                  )}
                  {c.skills.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {c.skills.slice(0, 3).map((sk) => (
                        <span key={sk} className="text-[10px] bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded font-mono">{sk}</span>
                      ))}
                      {c.skills.length > 3 && <span className="text-[10px] text-slate-400">+{c.skills.length - 3}</span>}
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">
              {isFetching ? t('updating') : t('pageInfo', { page, total: totalPages, count: total })}
            </span>
            <div className="flex items-center gap-2">
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 disabled:opacity-40">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 disabled:opacity-40">
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}