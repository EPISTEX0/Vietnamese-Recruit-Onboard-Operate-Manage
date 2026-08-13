'use client';
import { useLocale, useTranslations } from 'next-intl';

import React, { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FileText, Plus, X, CheckCircle } from 'lucide-react';
import {
  fetchMyRequests, createLeave, createOvertime, cancelLeave, cancelOvertime, fetchLeaveBalance,
} from '@/lib/api/employee-requests';
import type {
  EmployeeRequestListResponse, EmployeeRequestListItem, CreateLeaveData, CreateOvertimeData, LeaveBalance,
} from '@/lib/api/employee-requests';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, TextArea, Select, ButtonPrimary, ButtonGhost,
  Badge, ErrorAlert, EmptyState, Modal, formatDate, formatDateTime,
} from '@/components/shared-ui';

function getLeaveTypes(t: (key: string) => string): { value: CreateLeaveData['leave_type']; label: string }[] {
  return [
    { value: 'annual', label: t('leaveAnnual') },
    { value: 'sick', label: t('leaveSick') },
    { value: 'unpaid', label: t('leaveUnpaid') },
    { value: 'other', label: t('leaveOther') },

  ];
}
type FormTab = 'leave' | 'overtime';

/** Client-side overtime duration calculator (mirrors backend derive_duration). */
function calcDuration(startTime: string, endTime: string): number | null {
  if (!startTime || !endTime) return null;
  const [sh, sm] = startTime.split(':').map(Number);
  const [eh, em] = endTime.split(':').map(Number);
  if (isNaN(sh) || isNaN(sm) || isNaN(eh) || isNaN(em)) return null;
  let mins = (eh * 60 + em) - (sh * 60 + sm);
  if (mins <= 0) mins += 24 * 60; // overnight
  return mins;
}

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h > 0 && m > 0) return `${h}h ${m}m`;
  if (h > 0) return `${h}h`;
  return `${m}m`;
}

/** Simple toast that auto-dismisses after 3s. */
function SuccessToast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000);
    return () => clearTimeout(t);
  }, [onDone]);
  return (
    <div className="fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-3 bg-emerald-50 border border-emerald-200 rounded-xl text-emerald-700 shadow-lg animate-in fade-in slide-in-from-top-2">
      <CheckCircle className="w-4 h-4 shrink-0" />
      <span className="text-sm font-semibold">{message}</span>
    </div>
  );
}

export default function EmployeeRequestsPage() {
  const t = useTranslations('employee');
  const locale = useLocale();
  const qc = useQueryClient();

  const [tab, setTab] = useState<FormTab>('leave');
  const [leaveForm, setLeaveForm] = useState<CreateLeaveData>({ leave_type: 'annual', start_date: '', end_date: '', reason: '' });
  const [otForm, setOtForm] = useState<CreateOvertimeData>({ work_date: '', start_time: '', end_time: '', reason: '', project_or_task: '' });

  const [cancelTarget, setCancelTarget] = useState<EmployeeRequestListItem | null>(null);
  const [cancelReason, setCancelReason] = useState('');
  const [toast, setToast] = useState<string | null>(null);

  const listRef = useRef<HTMLDivElement>(null);

  // Queries
  const { data, isLoading, error } = useQuery<EmployeeRequestListResponse>({
    queryKey: ['my-requests'],
    queryFn: fetchMyRequests,
  });

  const { data: balance } = useQuery<LeaveBalance>({
    queryKey: ['my-leave-balance'],
    queryFn: fetchLeaveBalance,
    staleTime: 60_000,
  });

  // --- Client-side validation helpers ---

  const leaveDateError =
    leaveForm.start_date && leaveForm.end_date && leaveForm.end_date < leaveForm.start_date
      ? t('dateValidationError')
      : null;

  const otTimeError =
    otForm.start_time && otForm.end_time && otForm.end_time <= otForm.start_time
      ? t('timeValidationError')
      : null;

  const otDuration = calcDuration(otForm.start_time, otForm.end_time);

  // --- Mutations ---

  const leaveMut = useMutation({
    mutationFn: (d: CreateLeaveData) => createLeave(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-requests'] });
      qc.invalidateQueries({ queryKey: ['my-leave-balance'] });
      setLeaveForm({ leave_type: 'annual', start_date: '', end_date: '', reason: '' });
      setToast(t('leaveSubmitted'));
    },
  });
  const otMut = useMutation({
    mutationFn: (d: CreateOvertimeData) => createOvertime(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-requests'] });
      setOtForm({ work_date: '', start_time: '', end_time: '', reason: '', project_or_task: '' });
      setToast(t('overtimeSubmitted'));
    },
  });
  const cancelMut = useMutation<unknown, unknown, { request: EmployeeRequestListItem; reason: string }>({
    mutationFn: async ({ request, reason }) => {
      const r = reason.trim() || null;
      if (request.request_type === 'leave') {
        await cancelLeave(request.id, r);
      } else {
        await cancelOvertime(request.id, r);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-requests'] });
      qc.invalidateQueries({ queryKey: ['my-leave-balance'] });
      setCancelTarget(null);
      setCancelReason('');
      setToast(t('requestCancelled'));
    },
  });

  // --- Handlers ---

  const switchTab = (t: FormTab) => {
    setTab(t);
    // M-4: Clear mutation errors on tab switch
    leaveMut.reset();
    otMut.reset();
  };

  const submitLeave = () => leaveMut.mutate(leaveForm);
  const submitOt = () => otMut.mutate({ ...otForm, project_or_task: otForm.project_or_task || undefined });

  const leaveDisabled =
    leaveMut.isPending || !leaveForm.start_date || !leaveForm.end_date || !leaveForm.reason || !!leaveDateError;
  const otDisabled =
    otMut.isPending || !otForm.work_date || !otForm.start_time || !otForm.end_time || !otForm.reason || !!otTimeError;

  return (
    <div className="space-y-6">
      <PageHeader icon={FileText} title={t('myRequests')} subtitle={t('requestsDesc')} />

      {/* Success toast */}
      {toast && <SuccessToast message={toast} onDone={() => setToast(null)} />}

      {/* Leave balance card (C-1) */}
      {balance && (
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-bold text-slate-900">{t('annualLeaveDays')}</p>
              <p className="text-xs text-slate-500">
                {t('usedDays', { days: balance.approved_days_used })}
                {balance.pending_days > 0 && t('pendingDays', { days: balance.pending_days })}
              </p>
            </div>
            <div className="text-right">
              <span className={`text-2xl font-bold ${balance.remaining_days > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                {balance.remaining_days}
              </span>
              <span className="text-sm text-slate-500">{t('ofDays', { days: balance.annual_entitlement_days })}</span>
            </div>
          </div>
          {/* Progress bar */}
          <div className="mt-2 w-full bg-slate-100 rounded-full h-2">
            <div
              className="bg-indigo-500 h-2 rounded-full transition-all"
              style={{ width: `${Math.min(100, (balance.approved_days_used / balance.annual_entitlement_days) * 100)}%` }}
            />
          </div>
        </Card>
      )}

      {/* Create form */}
      <Card>
        <div className="flex gap-2 mb-4">
          {(['leave', 'overtime'] as FormTab[]).map((tabName) => (
            <button key={tabName} onClick={() => switchTab(tabName)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${tab === tabName ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
              {tabName === 'leave' ? t('leaveTab') : t('overtimeTab')}
            </button>
          ))}
        </div>

        {tab === 'leave' ? (
          <div className="space-y-3">
            <SectionTitle icon={Plus}>{t('createLeave')}</SectionTitle>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Field label={t('leaveType')}>
                <Select value={leaveForm.leave_type} onChange={(e) => setLeaveForm({ ...leaveForm, leave_type: e.target.value as CreateLeaveData['leave_type'] })}>
                  {getLeaveTypes(t).map((lt) => <option key={lt.value} value={lt.value}>{lt.label}</option>)}
                </Select>
              </Field>
              <Field label={t('fromDate')}>
                <TextInput aria-label={t('fromDateLabel')} type="date" value={leaveForm.start_date} onChange={(e) => setLeaveForm({ ...leaveForm, start_date: e.target.value })} />
              </Field>
              <Field label={t('toDate')}>
                <TextInput aria-label={t('toDateLabel')} type="date" value={leaveForm.end_date} onChange={(e) => setLeaveForm({ ...leaveForm, end_date: e.target.value })} />
              </Field>
            </div>
            {/* Client-side date validation error (M-2) */}
            {leaveDateError && <p className="text-xs text-rose-600 font-medium">{leaveDateError}</p>}
            <Field label={t('reason')}><TextArea rows={2} aria-label={t('leaveReasonAria')} placeholder={t('leaveReasonPlaceholder')} value={leaveForm.reason} onChange={(e) => setLeaveForm({ ...leaveForm, reason: e.target.value })} /></Field>
            {leaveMut.isError && <ErrorAlert error={leaveMut.error} />}
            <div className="flex justify-end">
              <ButtonPrimary aria-label={t('submitLeaveLabel')} onClick={submitLeave} disabled={leaveDisabled}>
                {leaveMut.isPending ? t('sending') : t('submitRequest')}
              </ButtonPrimary>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <SectionTitle icon={Plus}>{t('createOvertime')}</SectionTitle>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
              <Field label={t('workDate')}><TextInput type="date" aria-label={t('workDateLabel')} value={otForm.work_date} onChange={(e) => setOtForm({ ...otForm, work_date: e.target.value })} /></Field>
              <Field label={t('startTime')}><TextInput type="time" aria-label={t('startTimeLabel')} value={otForm.start_time} onChange={(e) => setOtForm({ ...otForm, start_time: e.target.value })} /></Field>
              <Field label={t('endTime')}><TextInput type="time" aria-label={t('endTimeLabel')} value={otForm.end_time} onChange={(e) => setOtForm({ ...otForm, end_time: e.target.value })} /></Field>
              <Field label={t('projectTask')}><TextInput value={otForm.project_or_task ?? ''} onChange={(e) => setOtForm({ ...otForm, project_or_task: e.target.value })} /></Field>
            </div>
            {/* Client-side time validation + real-time duration (M-2, M-3) */}
            {otTimeError && <p className="text-xs text-rose-600 font-medium">{otTimeError}</p>}
            {!otTimeError && otDuration != null && (
              <p className="text-xs text-indigo-600 font-medium">{t('totalDuration', { duration: formatDuration(otDuration) })}</p>
            )}
            <Field label={t('reason')}><TextArea rows={2} aria-label={t('overtimeReasonAria')} placeholder={t('overtimeReasonPlaceholder')} value={otForm.reason} onChange={(e) => setOtForm({ ...otForm, reason: e.target.value })} /></Field>
            {otMut.isError && <ErrorAlert error={otMut.error} />}
            <div className="flex justify-end">
              <ButtonPrimary aria-label={t('submitOvertimeLabel')} onClick={submitOt} disabled={otDisabled}>
                {otMut.isPending ? t('sending') : t('submitRequest')}
              </ButtonPrimary>
            </div>
          </div>
        )}
      </Card>

      {/* My requests list */}
      <Card>
        <div ref={listRef}>
          <SectionTitle icon={FileText}>{t('myRequests')}</SectionTitle>
          {error ? <ErrorAlert error={error} title={t('loadRequestsError')} />
            : isLoading && !data ? <p className="text-sm text-slate-400">{t('loading')}</p>
            : !data?.requests?.length ? <EmptyState filtered={false} emptyData={t('noRequests')} />
            : (
              <div className="space-y-2">
                {data.requests.map((r) => (
                  <div key={r.id} className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <Badge tone={r.request_type === 'leave' ? 'sky' : 'indigo'}>{r.request_type === 'leave' ? t('leaveLabel') : t('overtimeLabel')}</Badge>
                          <Badge tone={toneFor(r.status)}>{t(statusKey(r.status))}</Badge>
                          <span className="text-[10px] font-mono text-slate-400">{formatDateTime(r.submitted_at)}</span>
                        </div>
                        <p className="text-xs text-slate-600">
                          {r.request_type === 'leave'
                            ? `${r.leave_type ?? '—'}: ${formatDate(r.start_date)} → ${formatDate(r.end_date)}`
                            : `${formatDate(r.work_date)} ${r.start_time ?? ''}→${r.end_time ?? ''}${r.duration_minutes ? ` (${t('minutes', { count: r.duration_minutes })})` : ''}`}
                        </p>
                        {r.reason && <p className="text-xs text-slate-500 mt-1 italic">&ldquo;{r.reason}&rdquo;</p>}
                        {r.review_reason && <p className="text-[11px] text-slate-500 mt-1">{t('hrReview', { reason: r.review_reason, time: formatDateTime(r.reviewed_at) })}</p>}
                        {r.cancellation_reason && <p className="text-[11px] text-slate-400 mt-1">{t('cancelReason', { reason: r.cancellation_reason })}</p>}
                      </div>
                      {r.status === 'submitted' && (
                        <ButtonGhost onClick={() => { setCancelTarget(r); setCancelReason(''); }} className="!px-2.5">
                          <X className="w-4 h-4" /> {t('cancel')}
                        </ButtonGhost>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
        </div>
      </Card>

      {/* Cancel modal with optional reason (M-5) */}
      <Modal open={!!cancelTarget} onClose={() => setCancelTarget(null)} title={t('cancelRequest')}>
        {cancelTarget && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">{t('cancelConfirmDesc', { type: cancelTarget.request_type === 'leave' ? t('leaveLabel') : t('overtimeLabel') })}</p>
            <Field label={t('cancelReasonLabel')} hint={t('cancelReasonHint')}>
              <TextInput
                aria-label={t('cancelReasonAria')}
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                placeholder={t('cancelReasonPlaceholder')}
              />
            </Field>
            {cancelMut.isError && <ErrorAlert error={cancelMut.error} />}
            <div className="flex justify-end gap-2">
              <ButtonGhost onClick={() => setCancelTarget(null)}>{t('later')}</ButtonGhost>
              <ButtonPrimary
                onClick={() => cancelTarget && cancelMut.mutate({ request: cancelTarget, reason: cancelReason })}
                disabled={cancelMut.isPending}
              >
                {cancelMut.isPending ? t('cancelling') : t('confirmCancel')}
              </ButtonPrimary>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function toneFor(s: string) {
  switch (s) {
    case 'approved': return 'emerald' as const;
    case 'rejected': return 'rose' as const;
    case 'cancelled': return 'slate' as const;
    default: return 'amber' as const;
  }
}
    function statusKey(s: string) {
      switch (s) {
        case 'submitted': return 'pending';
        case 'approved': return 'approved';
        case 'rejected': return 'rejected';
        case 'cancelled': return 'cancelled';
        default: return s;
    }
}
