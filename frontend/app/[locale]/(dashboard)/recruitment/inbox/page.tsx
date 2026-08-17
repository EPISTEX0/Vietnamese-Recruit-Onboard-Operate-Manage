'use client';
import { useTranslations, useFormatter } from 'next-intl';

import React, { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Inbox, Mail, AlertTriangle, CheckCircle2, XCircle,
  Split, Link2, Pencil, Ban, ChevronDown, ChevronRight, UserPlus, Paperclip,
} from 'lucide-react';
import {
  listInbox, correctInboxIntent, dismissInboxItem,
  splitInboxItem, proposeInboxLink,
  listJobOpenings, promoteJobApplication,
  type InboxItem, type InboxStatus, type JobApplicationInboxResult,
  type SplitApplicantInput, type ApplicationSource,
  type JobOpeningListItem,
} from '@/lib/api/recruitment';
import {
  ErrorBanner, Loading, EmptyState, StatusPill,
  INBOX_STATUS_META, confidencePct,
} from '@/components/shared-ui';

const INTENTS = ['job_application', 'partner', 'event', 'internal', 'other'] as const;
export default function InboxPage() {
  const t = useTranslations('recruitment');
  const INTENT_LABELS: Record<string, string> = {
    job_application: 'Ứng tuyển',
    partner: 'Đối tác',
    event: 'Sự kiện',
    internal: 'Nội bộ',
    other: 'Khác',
  };
  const GROUPS: { status: InboxStatus | 'all'; label: string; icon: React.ElementType }[] = [
    { status: 'needs_classification', label: t('needsClassification'), icon: AlertTriangle },
    { status: 'needs_information', label: t('needsInformation'), icon: Pencil },
    { status: 'ready_for_review', label: t('readyForReview'), icon: CheckCircle2 },
    { status: 'resolved', label: t('resolved'), icon: CheckCircle2 },
  ];
  const qc = useQueryClient();
  const [activeGroup, setActiveGroup] = useState<InboxStatus | 'all'>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [splitting, setSplitting] = useState<string | null>(null);
  const [linking, setLinking] = useState<string | null>(null);
  const [splitApps, setSplitApps] = useState<Record<string, JobApplicationInboxResult[]>>({});
  const [actionError, setActionError] = useState<unknown>(null);

  const inboxKey = ['recruitment-inbox', activeGroup];
  const { data, isLoading, error } = useQuery({
    queryKey: inboxKey,
    queryFn: () => listInbox(activeGroup === 'all' ? {} : { inbox_status: activeGroup }),
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });

  // Open job openings for split assign + promote + link target
  const { data: jobOpeningsData } = useQuery({
    queryKey: ['recruitment-job-openings', 'open'],
    queryFn: () => listJobOpenings({ status: ['open'], page_size: 100 }),
    staleTime: 60 * 1000,
    placeholderData: (prev) => prev,
  });
  const openJobs: JobOpeningListItem[] = jobOpeningsData?.job_openings ?? [];

  const items = data?.items ?? [];

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['recruitment-inbox'] });
    qc.invalidateQueries({ queryKey: ['recruitment-candidates'] });
  };

  const correctM = useMutation({
    mutationFn: ({ id, intent }: { id: string; intent: string }) => correctInboxIntent(id, intent),
    onSuccess: () => { invalidate();  setActionError(null); },
    onError: (e: unknown) => setActionError(e),
  });
  const dismissM = useMutation({
    mutationFn: (id: string) => dismissInboxItem(id),
    onSuccess: () => { invalidate(); setActionError(''); },
    onError: (e: unknown) => setActionError(e),
  });
  const splitM = useMutation({
    mutationFn: ({ id, source, applicants }: { id: string; source: ApplicationSource; applicants: SplitApplicantInput[] }) =>
      splitInboxItem(id, { source, applicants }),
    onSuccess: (res, vars) => {
      setSplitApps((prev) => ({ ...prev, [vars.id]: res.applications }));
      invalidate();
      setActionError('');
    },
    onError: (e: unknown) => setActionError(e),
  });
  const promoteM = useMutation({
    mutationFn: ({ id, name, email, jobOpeningId }: { id: string; name: string; email: string; jobOpeningId?: string | null }) =>
      promoteJobApplication(id, { applicant_name: name, applicant_email: email, job_opening_id: jobOpeningId ?? null }),
    onSuccess: () => {
      invalidate();
      setActionError('');
    },
    onError: (e: unknown) => setActionError(e),
  });
  const linkM = useMutation({
    mutationFn: ({ id, target }: { id: string; target: string }) => proposeInboxLink(id, target),
    onSuccess: () => { setActionError(''); invalidate(); setLinking(null); },
    onError: (e: unknown) => setActionError(e),
  });

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length };
    for (const it of items) c[it.inbox_status] = (c[it.inbox_status] ?? 0) + 1;
    return c;
  }, [items]);

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-indigo-600">
        <Inbox className="w-5 h-5" />
        <h1 className="text-xl font-bold text-slate-900">{t('inboxTitle')}</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-3">
        {t('inboxDesc')}
      </p>

      {/* Group filter tabs */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setActiveGroup('all')}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${activeGroup === 'all' ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}
        >
          {t('all')} <span className="ml-1 font-mono opacity-70">{counts.all ?? 0}</span>
        </button>
        {GROUPS.map((g) => {
          const meta = INBOX_STATUS_META[g.status];
          const active = activeGroup === g.status;
          return (
            <button
              key={g.status}
              onClick={() => setActiveGroup(g.status)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${active ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}
            >
              <g.icon className="w-3.5 h-3.5" />
              {g.label}
              <span className="ml-1 font-mono opacity-70">{counts[g.status] ?? 0}</span>
            </button>
          );
        })}
      </div>

      {!!actionError && <ErrorBanner error={actionError} />}

      {isLoading ? (
        <Loading label={t('loadingInbox')} />
      ) : error ? (
        <ErrorBanner error={error} />
      ) : items.length === 0 ? (
        <EmptyState filtered={activeGroup !== 'all'} onReset={() => setActiveGroup('all')} />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <InboxCard
              key={item.id}
              item={item}
              open={!!expanded[item.id]}
              onToggle={() => setExpanded((p) => ({ ...p, [item.id]: !p[item.id] }))}
              onCorrect={(intent) => correctM.mutate({ id: item.id, intent })}
              onDismiss={() => dismissM.mutate(item.id)}
              onSplit={(source, applicants) => splitM.mutate({ id: item.id, source, applicants })}
              splitting={splitting === item.id}
              onStartSplit={() => setSplitting(item.id)}
              onCancelSplit={() => setSplitting(null)}
              splitApps={splitApps[item.id] ?? null}
              openJobs={openJobs}
              onLink={(target) => linkM.mutate({ id: item.id, target })}
              linking={linking === item.id}
              onStartLink={() => setLinking(item.id)}
              onCancelLink={() => setLinking(null)}
              onPromote={(jid, name, email, joid) => promoteM.mutate({ id: jid, name, email, jobOpeningId: joid })}
              pending={correctM.isPending || dismissM.isPending || splitM.isPending || promoteM.isPending || linkM.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface CardProps {
  item: InboxItem;
  open: boolean;
  onToggle: () => void;
  onCorrect: (intent: string) => void;
  onDismiss: () => void;
  onSplit: (source: ApplicationSource, applicants: SplitApplicantInput[]) => void;
  splitting: boolean;
  onStartSplit: () => void;
  onCancelSplit: () => void;
  splitApps: JobApplicationInboxResult[] | null;
  openJobs: JobOpeningListItem[];
  onLink: (targetJobApplicationId: string) => void;
  linking: boolean;
  onStartLink: () => void;
  onCancelLink: () => void;
  onPromote: (jobAppId: string, name: string, email: string, jobOpeningId?: string) => void;
  pending: boolean;
}

function InboxCard(props: CardProps) {
  const t = useTranslations('recruitment');
  const tc = useTranslations('common');
  const format = useFormatter();
  const { item, open, onToggle } = props;
  const meta = INBOX_STATUS_META[item.inbox_status] ?? { label: item.inbox_status, tone: 'slate' as const, labelKey: item.inbox_status };
  const [intentOpen, setIntentOpen] = useState(false);
  const [splitForm, setSplitForm] = useState<{ source: ApplicationSource; applicants: { name: string; email: string; job_opening_id: string }[] }>({
    source: 'direct',
    applicants: [{ name: '', email: '', job_opening_id: '' }],
  });
  const [linkTarget, setLinkTarget] = useState('');
  const [promoteJobOpening, setPromoteJobOpening] = useState<Record<string, string>>({});

  const doSplit = () => {
    const apps = splitForm.applicants.filter((a) => a.name.trim());
    if (apps.length === 0) return;
    props.onSplit(splitForm.source, apps.map((a) => ({
      name: a.name.trim(),
      email: a.email.trim() || undefined,
      job_opening_id: a.job_opening_id || undefined,
    })));
  };

  return (
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
      <button onClick={onToggle} className="w-full flex items-start gap-3 p-4 text-left">
        <div className="mt-0.5 text-slate-400">
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <StatusPill label={tc(meta.labelKey)} tone={meta.tone} />
            {item.prediction_intent && (
              <span className="text-[10px] font-mono bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                AI: {t('intent_' + (item.prediction_intent || 'unknown'))}
                {item.confidence_calibrated != null && ` · ${confidencePct(item.confidence_calibrated)}`}
              </span>
            )}
            {item.corrected_intent && (
              <span className="text-[10px] font-mono bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded border border-emerald-200">
                {t('correctedTo')} {t('intent_' + (item.corrected_intent || 'unknown'))}
              </span>
            )}
            {item.dismissed && <span className="text-[10px] font-mono bg-rose-50 text-rose-600 px-1.5 py-0.5 rounded">{t('dismissed')}</span>}
            {item.has_attachments && <Paperclip className="w-3.5 h-3.5 text-slate-400" />}
          </div>
          <p className="font-semibold text-sm text-slate-900 truncate">{item.subject || t('noSubject')}</p>
          <p className="text-xs text-slate-500 mt-0.5">{item.sender_name} &lt;{item.sender_email}&gt;</p>
          <p className="text-xs text-slate-400 mt-1 line-clamp-1">{item.snippet}</p>
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-slate-100 pt-3 space-y-3">
          {/* AI evidence + source hints */}
          {item.processing_error && (
            <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-600 flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{t('aiProcessingError', { retry: item.retry_count, exhausted: item.is_retry_exhausted ? t('retryExhausted') : '', error: item.processing_error })}</span>
            </div>
          )}
          {item.evidence && item.evidence.length > 0 && (
            <div className="text-xs">
              <span className="font-mono uppercase text-slate-400 text-[10px] tracking-wider">{t('aiSignals')}</span>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {item.evidence.map((e, i) => (
                  <span key={i} className="text-[11px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded border border-indigo-100 font-mono">{e.signal}</span>
                ))}
              </div>
            </div>
          )}
          {item.correction_history && item.correction_history.length > 0 && (
            <div className="text-xs text-slate-500">
              <span className="font-mono uppercase text-slate-400 text-[10px] tracking-wider">{t('correctionHistory')}</span>
              <ul className="mt-1 space-y-0.5">
                {item.correction_history.map((h, i) => (
                  <li key={i} className="font-mono text-[11px] text-slate-500">
                    {h.previous_intent ?? '—'} → <span className="text-emerald-600">{h.corrected_intent}</span> · {format.dateTime(new Date(h.corrected_at), 'full')}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Actions */}
          <div className="flex flex-wrap gap-2 pt-1">
            {/* Correct intent */}
            <div className="relative">
              <button
                onClick={() => setIntentOpen((v) => !v)}
                disabled={props.pending}
                className="px-3 py-1.5 text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg flex items-center gap-1.5 disabled:opacity-50"
              >
                <Pencil className="w-3.5 h-3.5" /> {t('correctClassification')}
              </button>
              {intentOpen && (
                <div className="absolute z-10 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg p-1 w-44">
                  {INTENTS.map((it) => (
                    <button
                      key={it}
                      onClick={() => { props.onCorrect(it); setIntentOpen(false); }}
                      className="w-full text-left px-2.5 py-1.5 text-xs rounded hover:bg-indigo-50 hover:text-indigo-700"
                    >
                      {t('intent_' + it)} <span className="font-mono text-[10px] text-slate-400">{it}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Dismiss */}
            <button
              onClick={props.onDismiss}
              disabled={props.pending}
              className="px-3 py-1.5 text-xs font-medium bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 rounded-lg flex items-center gap-1.5 disabled:opacity-50"
            >
              <Ban className="w-3.5 h-3.5" /> {t('dismissAction')}
            </button>

            {/* Split */}
            <button
              onClick={props.onStartSplit}
              disabled={props.pending}
              className="px-3 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg flex items-center gap-1.5 disabled:opacity-50"
            >
              <Split className="w-3.5 h-3.5" /> {t('splitToJobApp')}
            </button>

            {/* Link proposal */}
            <button
              onClick={props.onStartLink}
              disabled={props.pending}
              className="px-3 py-1.5 text-xs font-medium bg-violet-50 hover:bg-violet-100 text-violet-700 border border-violet-200 rounded-lg flex items-center gap-1.5 disabled:opacity-50"
            >
              <Link2 className="w-3.5 h-3.5" /> {t('proposeLink')}
            </button>
          </div>

          {/* Split form */}
          {props.splitting && (
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono uppercase text-slate-500">{t('source')}</span>
                <select
                  value={splitForm.source}
                  onChange={(e) => setSplitForm((f) => ({ ...f, source: e.target.value as ApplicationSource }))}
                  className="text-xs bg-white border border-slate-200 rounded px-2 py-1"
                >
                  <option value="direct">{t('sourceDirect')}</option>
                  <option value="employee_referral">{t('sourceReferral')}</option>
                  <option value="agency">{t('sourceAgency')}</option>
                </select>
              </div>
              {splitForm.applicants.map((a, i) => (
                <div key={i} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_1fr_auto] gap-2">
                  <input
                    placeholder={t('applicantNamePlaceholder')}
                    value={a.name}
                    onChange={(e) => setSplitForm((f) => {
                      const apps = [...f.applicants]; apps[i] = { ...apps[i], name: e.target.value }; return { ...f, applicants: apps };
                    })}
                    className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg"
                  />
                  <input
                    placeholder={t('emailPlaceholder')}
                    value={a.email}
                    onChange={(e) => setSplitForm((f) => {
                      const apps = [...f.applicants]; apps[i] = { ...apps[i], email: e.target.value }; return { ...f, applicants: apps };
                    })}
                    className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg"
                  />
                  <select
                    value={a.job_opening_id}
                    onChange={(e) => setSplitForm((f) => {
                      const apps = [...f.applicants]; apps[i] = { ...apps[i], job_opening_id: e.target.value }; return { ...f, applicants: apps };
                    })}
                    className="px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg"
                  >
                    <option value="">{t('noJobAssigned')}</option>
                    {props.openJobs.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
                  </select>
                  <button
                    onClick={() => setSplitForm((f) => ({ ...f, applicants: f.applicants.filter((_, idx) => idx !== i) }))}
                    disabled={splitForm.applicants.length <= 1}
                    className="p-1.5 text-rose-400 hover:bg-rose-50 rounded-lg disabled:opacity-30"
                  >
                    <XCircle className="w-4 h-4" />
                  </button>
                </div>
              ))}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setSplitForm((f) => ({ ...f, applicants: [...f.applicants, { name: '', email: '', job_opening_id: '' }] }))}
                  className="text-xs text-indigo-600 hover:text-indigo-700 font-medium flex items-center gap-1"
                >
                  <UserPlus className="w-3.5 h-3.5" /> {t('addApplicant')}
                </button>
                <div className="flex-1" />
                <button onClick={props.onCancelSplit} className="px-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">{t('cancel')}</button>
                <button onClick={doSplit} disabled={props.pending} className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg disabled:opacity-50">
                  {t('splitCount', { count: splitForm.applicants.length })}
                </button>
              </div>
            </div>
          )}

          {/* Link form */}
          {props.linking && (
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 flex items-end gap-2">
              <div className="flex-1">
                <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('linkTargetLabel')}</label>
                <input
                  value={linkTarget}
                  onChange={(e) => setLinkTarget(e.target.value)}
                  placeholder="uuid..."
                  className="w-full px-2.5 py-1.5 text-xs font-mono bg-white border border-slate-200 rounded-lg"
                />
              </div>
              <button onClick={props.onCancelLink} className="px-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">{t('cancel')}</button>
              <button
                onClick={() => linkTarget && props.onLink(linkTarget)}
                disabled={props.pending || !linkTarget}
                className="px-3 py-1.5 text-xs bg-violet-600 text-white rounded-lg disabled:opacity-50"
              >
                {t('propose')}
              </button>
            </div>
          )}

          {/* Split results → promote */}
          {props.splitApps && props.splitApps.length > 0 && (
            <div className="space-y-2">
              <span className="text-[10px] font-mono uppercase text-slate-400 tracking-wider">
                {t('jobAppsCreated', { count: props.splitApps.length })}
              </span>
              {props.splitApps.map((ja) => (
                <div key={ja.id} className="p-3 bg-indigo-50/60 border border-indigo-100 rounded-xl">
                  <div className="flex items-center gap-2 mb-2">
                    <Mail className="w-3.5 h-3.5 text-indigo-500" />
                    <span className="text-xs font-medium text-slate-800">
                      {ja.applicant_name ?? t('unnamed')} {ja.applicant_email ? `· ${ja.applicant_email}` : ''}
                    </span>
                    <code className="text-[10px] font-mono text-slate-400 truncate">{ja.id}</code>
                  </div>
                  <div className="flex items-end gap-2">
                    <div className="flex-1">
                      <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('assignJobOpening')}</label>
                      <select
                        value={promoteJobOpening[ja.id] ?? ''}
                        onChange={(e) => setPromoteJobOpening((p) => ({ ...p, [ja.id]: e.target.value }))}
                        className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg"
                      >
                        <option value="">{t('noAssign')}</option>
                        {props.openJobs.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
                      </select>
                    </div>
                    <button
                      onClick={() => props.onPromote(ja.id, ja.applicant_name ?? t('unnamed'), ja.applicant_email ?? '', promoteJobOpening[ja.id] || undefined)}
                      disabled={props.pending || !ja.applicant_name}
                      className="px-3 py-1.5 text-xs bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg disabled:opacity-50 flex items-center gap-1.5"
                    >
                      <UserPlus className="w-3.5 h-3.5" /> {t('promoteToCandidate')}
                    </button>
                  </div>
                  {!ja.applicant_name && (
                    <p className="text-[10px] text-amber-600 mt-1">{t('promoteNameRequired')}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}