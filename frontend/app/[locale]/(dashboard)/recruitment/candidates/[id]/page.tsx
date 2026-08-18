'use client';
import { useTranslations, useFormatter } from 'next-intl';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ArrowLeft, CheckCircle2, XCircle, Archive, Link2, Unlink,
  FileText, Calendar, Briefcase, ExternalLink, AlertTriangle, Plus,
} from 'lucide-react';
    import {
      getCandidate, acceptCandidate, rejectCandidate, archiveCandidate,
      assignCandidate, reassignCandidate, unassignCandidate,
      createInterview, completeInterview, cancelInterview, createReplacementInterview,
      getCVPresignedUrl,
      listJobOpenings,
      type CandidateDetail, type JobOpeningListItem, type InterviewResponse,
      type CreateInterviewRequest,
    } from '@/lib/api/recruitment';
    import { getCalendars, type CalendarListResponse } from '@/lib/api/gmail';
    import {
      ErrorBanner, Loading, StatusPill,
      CANDIDATE_STATUS_META, confidencePct,
    } from '@/components/shared-ui';

export default function CandidateDetailPage() {
  const tc = useTranslations('common');
  const t = useTranslations('recruitment');
  const format = useFormatter();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const [rejectReason, setRejectReason] = useState('');
  const [rejectOpen, setRejectOpen] = useState(false);
  const [assJob, setAssJob] = useState('');
  const [actionError, setActionError] = useState<unknown>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [interviewOpen, setInterviewOpen] = useState(false);

  const { data: candidate, isLoading, error } = useQuery<CandidateDetail>({
    queryKey: ['recruitment-candidate', params.id],
    queryFn: () => getCandidate(params.id),
    enabled: !!params.id,
    staleTime: 30 * 1000,
  });

      // Matches the `['google-calendars']` query in interviews/page.tsx (#340): that page gates Interview creation on
      // "đã chọn calendar chưa" and invalidates this same key on selection, so a stale
      // 5-minute answer here could tell a candidate's Interview flow the connection is
      // missing right after it was just made on the other page.
      const { data: calendars } = useQuery<CalendarListResponse>({
        queryKey: ['google-calendars'],
        queryFn: () => getCalendars(),
        staleTime: 60 * 1000,
      });
      const selectedCalendarId = calendars?.selected_calendar_id ?? null;

      const { data: openJobsData } = useQuery({
        queryKey: ['recruitment-job-openings', 'open'],
        queryFn: () => listJobOpenings({ status: ['open'], page_size: 100 }),
        staleTime: 60 * 1000,
      });
      const openJobs: JobOpeningListItem[] = openJobsData?.job_openings ?? [];

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['recruitment-candidate', params.id] });
    qc.invalidateQueries({ queryKey: ['recruitment-candidates'] });
    qc.invalidateQueries({ queryKey: ['recruitment-job-openings'] });
    qc.invalidateQueries({ queryKey: ['recruitment-interviews'] });
    qc.invalidateQueries({ queryKey: ['onboarding'] });
  };
  const wrapErr = (_label: string) => (e: unknown) => setActionError(e);

  const acceptM = useMutation({
    mutationFn: () => acceptCandidate(params.id),
    onSuccess: () => {
      invalidate();
      setActionError(null);
      setActionSuccess(t('acceptedAutoOnboarding'));
    },
    onError: wrapErr(t('errAccept')),
  });
  const rejectM = useMutation({ mutationFn: (reason: string) => rejectCandidate(params.id, { reason }), onSuccess: () => { invalidate(); setRejectOpen(false); setActionError(''); }, onError: wrapErr(t('errReject')) });
  const archiveM = useMutation({ mutationFn: () => archiveCandidate(params.id), onSuccess: () => { invalidate(); setActionError(''); }, onError: wrapErr(t('errArchive')) });
  const assignM = useMutation({
    mutationFn: (jobOpeningId: string) =>
      candidate?.job_opening_id ? reassignCandidate(params.id, jobOpeningId) : assignCandidate(params.id, jobOpeningId),
    onSuccess: () => { invalidate(); setAssignOpen(false); setActionError(''); setAssJob(''); },
    onError: wrapErr(t('errAssign')),
  });
  const unassignM = useMutation({ mutationFn: () => unassignCandidate(params.id), onSuccess: () => { invalidate(); setActionError(''); }, onError: wrapErr(t('errUnassign')) });
  const createIntM = useMutation({
    mutationFn: (data: CreateInterviewRequest) => createInterview(params.id, data),
    onSuccess: () => { invalidate(); setInterviewOpen(false); setActionError(''); },
    onError: wrapErr(t('errCreateInterview')),
  });
      const completeIntM = useMutation({
        mutationFn: (ivId: string) => {
          if (!window.confirm(t('confirmComplete'))) return Promise.reject(new Error('Cancelled'));
          return completeInterview(params.id, ivId);
        },
        onSuccess: () => { invalidate(); setActionError(''); },
        onError: (e: any) => { if (e?.message !== 'Cancelled') setActionError(e); },
      });
      const cancelIntM = useMutation({
        mutationFn: (ivId: string) => {
          if (!window.confirm(t('confirmCancelInterview'))) return Promise.reject(new Error('Cancelled'));
          return cancelInterview(params.id, ivId);
        },
        onSuccess: () => { invalidate(); setActionError(''); },
        onError: (e: any) => { if (e?.message !== 'Cancelled') setActionError(e); },
      });
  const [replaceFor, setReplaceFor] = useState<string | null>(null);
  const replaceIntM = useMutation({
    mutationFn: ({ ivId, data }: { ivId: string; data: CreateInterviewRequest }) => createReplacementInterview(params.id, ivId, data),
    onSuccess: () => { invalidate(); setReplaceFor(null); setActionError(''); },
    onError: wrapErr(t('errReplacement')),
  });

  if (isLoading) return <Loading label={t('loadingCandidate')} />;
  if (error) return <ErrorBanner error={error} />;
  if (!candidate) return <ErrorBanner error={new Error(t('candidateNotFound'))} />;

  const meta = CANDIDATE_STATUS_META[candidate.status] ?? { label: candidate.status, tone: 'slate' as const, labelKey: candidate.status };
  const isTerminal = candidate.status === 'accepted' || candidate.status === 'rejected' || candidate.status === 'archived';
  const interviews: InterviewResponse[] = candidate.interviews ?? [];

  return (
    <div className="space-y-5">
      <button onClick={() => router.push('/recruitment/candidates')} className="flex items-center gap-1 text-xs text-slate-500 hover:text-indigo-600">
        <ArrowLeft className="w-3.5 h-3.5" /> {t('backToList')}
      </button>

      {/* Header */}
      <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-2">
              <StatusPill label={tc(meta.labelKey)} tone={meta.tone} />
              <span className="text-[11px] font-mono text-slate-400">confidence {confidencePct(candidate.confidence_score)}</span>
            </div>
            <h1 className="text-xl font-bold text-slate-900">{candidate.name}</h1>
            <p className="text-sm text-slate-500">{candidate.email} {candidate.phone && `· ${candidate.phone}`}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {!isTerminal && candidate.status !== 'accepted' && (
              <button onClick={() => acceptM.mutate()} disabled={acceptM.isPending} className="px-3 py-2 text-xs font-medium bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg flex items-center gap-1.5 disabled:opacity-50">
                <CheckCircle2 className="w-4 h-4" /> {t('acceptOnboarding')}
              </button>
            )}
            {!isTerminal && candidate.status !== 'rejected' && (
              <button onClick={() => setRejectOpen((v) => !v)} disabled={rejectM.isPending} className="px-3 py-2 text-xs font-medium bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 rounded-lg flex items-center gap-1.5">
                <XCircle className="w-4 h-4" /> {t('reject')}
              </button>
            )}
            {!isTerminal && candidate.status !== 'archived' && (
              <button onClick={() => archiveM.mutate()} disabled={archiveM.isPending} className="px-3 py-2 text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg flex items-center gap-1.5 disabled:opacity-50">
                <Archive className="w-4 h-4" /> {t('archive')}
              </button>
            )}
          </div>
        </div>

        {rejectOpen && (
          <div className="mt-3 p-3 bg-rose-50/50 rounded-xl border border-rose-200 flex items-end gap-2">
            <div className="flex-1">
              <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('rejectReasonRequired')}</label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                rows={2}
                className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg"
                placeholder={t('rejectReasonPlaceholder')}
              />
            </div>
            <button onClick={() => rejectReason.trim() && rejectM.mutate(rejectReason.trim())} disabled={rejectM.isPending || !rejectReason.trim()} className="px-3 py-1.5 text-xs bg-rose-600 text-white rounded-lg disabled:opacity-50">
              {t('confirmReject')}
            </button>
          </div>
        )}

        <div className="mt-3 text-[11px] text-slate-400 font-mono">
          {t('createdAt', { date: format.dateTime(new Date(candidate.created_at), 'full') })} · {t('updatedAt', { date: format.dateTime(new Date(candidate.updated_at), 'full') })}
        </div>
      </div>

      {!!actionError && <ErrorBanner error={actionError} />}

      {actionSuccess && (
        <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-sm text-emerald-700 flex items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{actionSuccess}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Link
              href="/onboarding"
              className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-full transition-all flex items-center gap-1"
            >
              <ExternalLink className="w-3 h-3" /> {t('viewOnboarding')}
            </Link>
            <button
              onClick={() => setActionSuccess(null)}
              className="text-emerald-500 hover:text-emerald-700"
            >
              ×
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left: CV + parsed */}
        <div className="lg:col-span-2 space-y-4">
          {candidate.summary && (
            <Section icon={FileText} title={t('summary')}>
              <p className="text-sm text-slate-600 leading-relaxed">{candidate.summary}</p>
            </Section>
          )}
    {/* Interviews */}
              <Section icon={Calendar} title={t('interviewSchedule')}>
            {interviews.length === 0 ? (
              <p className="text-xs text-slate-400 mb-2">{t('noInterviews')}</p>
            ) : (
              <ul className="space-y-2 mb-2">
                {interviews.map((iv) => (
                  <li key={iv.id} className="p-2.5 bg-slate-50 rounded-lg border border-slate-100">
                    <div className="flex items-center justify-between flex-wrap gap-1">
                      <span className="text-xs font-medium text-slate-800">{iv.round_name}</span>
                      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${iv.status === 'scheduled' ? 'bg-indigo-50 text-indigo-600' : iv.status === 'completed' ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600'}`}>{iv.status}</span>
                    </div>
                    <p className="text-[10px] font-mono text-slate-500 mt-0.5">
                      {format.dateTime(new Date(iv.start_at), 'full', { timeZone: iv.timezone })} ({iv.timezone})
                    </p>
                    {iv.needs_relink && <p className="text-[10px] text-amber-600 mt-0.5">{t('needsRelink')}</p>}
                    {iv.status === 'scheduled' && (
                          <div className="flex gap-1.5 mt-2 flex-wrap">
                            <button onClick={() => completeIntM.mutate(iv.id)} disabled={completeIntM.isPending} className="text-[10px] px-2 py-1 bg-emerald-50 text-emerald-600 rounded hover:bg-emerald-100">{t('complete')}</button>
                            <button onClick={() => cancelIntM.mutate(iv.id)} disabled={cancelIntM.isPending} className="text-[10px] px-2 py-1 bg-rose-50 text-rose-600 rounded hover:bg-rose-100">{t('cancel')}</button>
                            <button onClick={() => { setReplaceFor(iv.id); setInterviewOpen(true); }} className="text-[10px] px-2 py-1 bg-amber-50 text-amber-600 rounded hover:bg-amber-100">{t('reschedule')}</button>
                          </div>
                        )}
                        {iv.status === 'cancelled' && !isTerminal && (
                          <div className="flex gap-1.5 mt-2">
                            <button onClick={() => { setReplaceFor(iv.id); setInterviewOpen(true); }} className="text-[10px] px-2 py-1 bg-indigo-50 text-indigo-600 rounded hover:bg-indigo-100">{t('createReplacement')}</button>
                          </div>
                        )}
                  </li>
                ))}
                            </ul>
                              )}
                              {!isTerminal && (
                                <>
                                  <button onClick={() => { setReplaceFor(null); setInterviewOpen((v) => !v); }} disabled={createIntM.isPending || replaceIntM.isPending} className="text-xs px-3 py-1.5 bg-indigo-600 text-white rounded-lg flex items-center gap-1.5 disabled:opacity-50">
                                    <Plus className="w-3.5 h-3.5" /> {replaceFor ? t('createReplacement') : t('createInterview')}
                                  </button>
                                  {interviewOpen && (
                                    <InterviewForm
                                      onSubmit={(d) => replaceFor ? replaceIntM.mutate({ ivId: replaceFor, data: d }) : createIntM.mutate(d)}
                                      pending={replaceFor ? replaceIntM.isPending : createIntM.isPending}
                                      onCancel={() => { setInterviewOpen(false); setReplaceFor(null); }}
                                      hasCalendar={!!selectedCalendarId}
                                      isReplacement={!!replaceFor}
                                    />
                                  )}
                                </>
                              )}

                                </Section>

              {candidate.skills.length > 0 && (
                <Section icon={Briefcase} title={t('skills')}>
                  <div className="flex flex-wrap gap-1.5">
                    {candidate.skills.map((s) => (
                      <span key={s} className="text-xs bg-emerald-50 text-emerald-700 px-2 py-1 rounded font-mono border border-emerald-100">{s}</span>
                    ))}
                  </div>
                </Section>
              )}
              {candidate.experience.length > 0 && (
                <Section icon={Briefcase} title={t('experience')}>
                  <ul className="space-y-1.5">
                    {candidate.experience.map((e, i) => (
                      <li key={i} className="text-xs">
                        <span className="font-medium text-slate-800">{e.role}</span> · <span className="text-slate-500">{e.company}</span>
                        <span className="text-slate-400 font-mono block">{e.duration}</span>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
              {candidate.education.length > 0 && (
                <Section icon={FileText} title={t('education')}>
                  <ul className="space-y-1.5">
                    {candidate.education.map((e, i) => (
                      <li key={i} className="text-xs">
                        <span className="font-medium text-slate-800">{e.degree}</span> · <span className="text-slate-500">{e.institution}</span>
                        <span className="text-slate-400 font-mono block">{e.year}</span>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {/* CV documents + provenance */}
              <Section icon={FileText} title={t('cvDocuments')}>
                {candidate.cv_documents.length === 0 ? (
                  <p className="text-xs text-slate-400">{t('noCV')}</p>
                ) : (
                  <ul className="space-y-1.5">
                    {candidate.cv_documents.map((doc) => (
                      <li key={doc.id} className="flex items-center justify-between gap-2 p-2 bg-slate-50 rounded-lg border border-slate-100">
                        <div className="min-w-0">
                          <p className="text-xs font-medium text-slate-800 truncate">{doc.original_filename}</p>
                          <p className="text-[10px] font-mono text-slate-400">
                            {doc.processing_status} · {(doc.size_bytes / 1024).toFixed(0)}KB · {doc.mime_type}
                          </p>
                        </div>
                        <button
                          onClick={() => viewCV(candidate.id, doc.id, setActionError)}
                          disabled={!doc.presigned_url}
                          className="text-xs px-2.5 py-1 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 disabled:opacity-40 flex items-center gap-1"
                        >
                          <ExternalLink className="w-3 h-3" /> {t('view')}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {candidate.source_email_message_id && (
                  <p className="text-[10px] font-mono text-slate-400 mt-2">{t('sourceEmail', { id: candidate.source_email_message_id.slice(0, 12) })}</p>
                )}
              </Section>
            </div>

            {/* Right: assignment */}
        <div className="space-y-4">
          <Section icon={Link2} title={t('assignJob')}>
            {candidate.job_opening_id ? (
              <div className="p-3 bg-emerald-50/60 border border-emerald-100 rounded-xl">
                <p className="text-xs font-medium text-emerald-700">{t('assigned')}</p>
                <p className="text-sm text-slate-800">{candidate.job_opening_title || candidate.job_opening_id}</p>
                {!isTerminal && (
                  <div className="flex gap-1.5 mt-2">
                    <button onClick={() => setAssignOpen((v) => !v)} className="text-[10px] px-2 py-1 bg-white border border-slate-200 rounded text-slate-600 hover:bg-slate-50">{t('change')}</button>
                    <button onClick={() => unassignM.mutate()} disabled={unassignM.isPending} className="text-[10px] px-2 py-1 bg-rose-50 text-rose-600 rounded flex items-center gap-1 disabled:opacity-50">
                      <Unlink className="w-3 h-3" /> {t('unassign')}
                    </button>
                  </div>
                )}
              </div>
            ) : (
              !isTerminal ? (
                <>
                  <button onClick={() => setAssignOpen((v) => !v)} className="text-xs px-3 py-1.5 bg-white border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 flex items-center gap-1.5">
                    <Link2 className="w-3.5 h-3.5" /> {t('assignPosition')}
                  </button>
                  {assignOpen && (
                    <div className="mt-2 space-y-2">
                      <select value={assJob} onChange={(e) => setAssJob(e.target.value)} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">
                        <option value="">{t('selectOpenPosition')}</option>
                        {openJobs.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
                      </select>
                      {openJobs.length === 0 && <p className="text-[10px] text-amber-600">{t.rich('noOpenJobs', { code: (c) => <code>{c}</code> })}</p>}
                      <button onClick={() => assJob && assignM.mutate(assJob)} disabled={!assJob || assignM.isPending} className="w-full px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg disabled:opacity-50">
                        {t('confirmAssign')}
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-xs text-slate-400">{t('terminalCannotAssign')}</p>
              )
            )}
          </Section>

          {candidate.rejection_reason && (
            <Section icon={AlertTriangle} title={t('rejectionReason')}>
              <p className="text-xs text-slate-600">{candidate.rejection_reason}</p>
              {candidate.rejected_at && <p className="text-[10px] font-mono text-slate-400 mt-1">{format.dateTime(new Date(candidate.rejected_at), 'full')}</p>}
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({ icon: Icon, title, children }: { icon: React.ElementType; title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-indigo-600" />
        <h3 className="text-sm font-bold text-slate-900">{title}</h3>
      </div>
      {children}
    </div>
  );
}

async function viewCV(candidateId: string, docId: string, setErr: (m: string) => void) {
  try {
    const r = await getCVPresignedUrl(candidateId, docId);
    if (r.presigned_url) window.open(r.presigned_url, '_blank');
  } catch (e) {
    setErr(e instanceof Error ? e.message : 'Failed to load CV');
  }
}

    function InterviewForm({ onSubmit, pending, onCancel, hasCalendar, isReplacement }: {
  onSubmit: (d: CreateInterviewRequest) => void; pending: boolean; onCancel: () => void; hasCalendar: boolean; isReplacement?: boolean }) {
  const t = useTranslations('recruitment');
  const [roundName, setRoundName] = useState(t('round1'));
  // Use datetime-local → ISO. tz default Asia/Ho_Chi_Minh
  const [start, setStart] = useState('');
  const [duration, setDuration] = useState(60);
  const [mode, setMode] = useState<'google_meet' | 'in_person' | 'custom_link'>('google_meet');
  const [notes, setNotes] = useState('');

  const submit = () => {
    if (!start) return;
    const startDate = new Date(start);
    const endDate = new Date(startDate.getTime() + duration * 60000);
    onSubmit({
      round_name: roundName,
      start: startDate.toISOString(),
      end: endDate.toISOString(),
      timezone: 'Asia/Ho_Chi_Minh',
      mode,
      interviewer_ids: [],
      external_participant_emails: [],
      notes: notes || null,
    });
  };

  return (
    <div className="mt-3 p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('round')}</label>
          <input value={roundName} onChange={(e) => setRoundName(e.target.value)} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('startTime')}</label>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('duration')}</label>
          <input type="number" value={duration} onChange={(e) => setDuration(Number(e.target.value) || 60)} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-slate-500 block mb-1">{t('mode')}</label>
          <select value={mode} onChange={(e) => setMode(e.target.value as 'google_meet' | 'in_person' | 'custom_link')} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">
            <option value="google_meet">{t('googleMeet')}</option>
            <option value="in_person">{t('inPerson')}</option>
            <option value="custom_link">{t('customLink')}</option>
          </select>
        </div>
      </div>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t('notesPlaceholder')} rows={2} className="w-full px-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg" />
                <div className="flex items-center justify-between gap-2">
                  {hasCalendar ? (
                    <p className="text-[10px] text-emerald-600">✅ Calendar {t('calendarReady')}</p>
                  ) : (
                    <p className="text-[10px] text-amber-600">⚠ {t('calendarRequired')}</p>
                  )}
                  <div className="flex gap-2">
                                        <button onClick={onCancel} className="px-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg">{t('cancel')}</button>
                                        <button onClick={submit} disabled={pending || !start} className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg disabled:opacity-50">{isReplacement ? t('createReplacement') : t('create')}</button>
                  </div>
                </div>
      </div>
  );
}