'use client';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useLocale, useTranslations } from 'next-intl';
import { FileText, Check, X, Search, CheckCheck, XCircle } from 'lucide-react';
import {
  fetchSubmittedRequests, approveRequest, rejectRequest,
} from '@/lib/api/employee-requests';
import type {
  AdminReviewQueueResponse, AdminEmployeeRequestItem, ReviewQueueFilters,
} from '@/lib/api/employee-requests';
import { listEmployees } from '@/lib/api/employees';
import type { EmployeeListResponse } from '@/lib/api/types';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, TextArea, Select, ButtonPrimary, ButtonGhost, ButtonDanger,
  Badge, ErrorAlert, EmptyState, LoadingRows, Modal, useFormatDate, useFormatDateTime,
} from '@/components/shared-ui';

export default function RequestsReviewPage() {
  const t = useTranslations('requests');
      const locale = useLocale();
  const formatDate = useFormatDate();
  const formatDateTime = useFormatDateTime();
  const qc = useQueryClient();

  const [requestType, setRequestType] = useState<'' | 'leave' | 'overtime'>('');
  const [statusFilter, setStatusFilter] = useState<'' | 'submitted' | 'approved' | 'rejected' | 'cancelled'>('submitted');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [employeeId, setEmployeeId] = useState('');
  const [submitted, setSubmitted] = useState({ requestType, statusFilter, dateFrom, dateTo, employeeId });

  // Bulk selection (C-3)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const { data: employees } = useQuery<EmployeeListResponse>({
    queryKey: ['employees-list', { all: true }],
    queryFn: () => listEmployees({ page: 1, page_size: 200, is_active: true }),
    staleTime: 60_000,
  });

  const filters: ReviewQueueFilters = {
    request_type: submitted.requestType || undefined,
    status: submitted.statusFilter || undefined,
    date_from: submitted.dateFrom || undefined,
    date_to: submitted.dateTo || undefined,
    employee_id: submitted.employeeId || undefined,
  };

  const { data, isLoading, error } = useQuery<AdminReviewQueueResponse>({
    queryKey: ['admin-requests', submitted],
    queryFn: () => fetchSubmittedRequests(filters),
    placeholderData: (prev) => prev,
  });

  const apply = () => {
    setSubmitted({ requestType, statusFilter, dateFrom, dateTo, employeeId });
    setSelectedIds(new Set());
  };
  const hasFilters =
    Boolean(submitted.requestType || submitted.dateFrom || submitted.dateTo || submitted.employeeId) ||
    submitted.statusFilter !== 'submitted';

  // --- Individual Approve / Reject ---

  const [rejectTarget, setRejectTarget] = useState<AdminEmployeeRequestItem | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  // m-2: Approve confirmation
  const [approveTarget, setApproveTarget] = useState<AdminEmployeeRequestItem | null>(null);

  const approveMut = useMutation({
    mutationFn: (id: string) => approveRequest(id, null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-requests'] });
      setApproveTarget(null);
    },
  });
  const rejectMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => rejectRequest(id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-requests'] });
      setRejectTarget(null);
      setRejectReason('');
    },
  });

  // --- Bulk actions (C-3) ---

  const [bulkRejectOpen, setBulkRejectOpen] = useState(false);
  const [bulkRejectReason, setBulkRejectReason] = useState('');

  const submittedItems = (data?.requests ?? []).filter(r => r.status === 'submitted');

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    const allSubmittedIds = submittedItems.map(r => r.id);
    if (allSubmittedIds.every(id => selectedIds.has(id))) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(allSubmittedIds));
    }
  };

  const bulkApproveMut = useMutation({
    mutationFn: async (ids: string[]) => {
      for (const id of ids) await approveRequest(id, null);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-requests'] });
      setSelectedIds(new Set());
    },
  });

  const bulkRejectMut = useMutation({
    mutationFn: async ({ ids, reason }: { ids: string[]; reason: string }) => {
      for (const id of ids) await rejectRequest(id, reason);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-requests'] });
      setSelectedIds(new Set());
      setBulkRejectOpen(false);
      setBulkRejectReason('');
    },
  });

  const renderDetail = (r: AdminEmployeeRequestItem) => {
    if (r.request_type === 'leave') {
      return `${r.leave_type ?? '—'} · ${formatDate(r.start_date)} → ${formatDate(r.end_date)}`;
    }
    return `${formatDate(r.work_date)} · ${r.start_time ?? '—'}→${r.end_time ?? '—'}${r.duration_minutes ? ` (${r.duration_minutes} phút)` : ''}`;
  };

  const selectedCount = selectedIds.size;
  const allSelected = submittedItems.length > 0 && submittedItems.every(r => selectedIds.has(r.id));

  return (
    <div className="space-y-6">
      <PageHeader
        icon={FileText}
        title={t('title')}
        subtitle={t('subtitle')}
      />

      <Card>
        <SectionTitle icon={Search}>{t('filterSection')}</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 items-end">
          <Field label={t('requestType')}>
            <Select value={requestType} onChange={(e) => setRequestType(e.target.value as '' | 'leave' | 'overtime')}>
              <option value="">{t('all')}</option>
              <option value="leave">{t('leave')}</option>
              <option value="overtime">{t('overtime')}</option>
            </Select>
          </Field>
          <Field label={t('status')}>
            <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as '' | 'submitted' | 'approved' | 'rejected' | 'cancelled')}>
              <option value="submitted">{t('pending')}</option>
              <option value="approved">{t('approved')}</option>
              <option value="rejected">{t('rejected')}</option>
              <option value="cancelled">{t('cancelled')}</option>
              <option value="">{t('all')}</option>
            </Select>
          </Field>
          <Field label={t('fromDate')}><TextInput type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></Field>
          <Field label={t('toDate')}><TextInput type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></Field>
          <Field label={t('employee')}>
            <Select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}>
              <option value="">{t('all')}</option>
              {employees?.items?.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
            </Select>
          </Field>
          <ButtonPrimary onClick={apply} className="h-[38px]"><Search className="w-4 h-4" /> {t('filter')}</ButtonPrimary>
        </div>
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-4">
          <SectionTitle icon={FileText}>{t('queue')}</SectionTitle>
          {/* Bulk actions bar (C-3) */}
          {submittedItems.length > 0 && (
            <div className="flex items-center gap-2">
              <button
                onClick={toggleAll}
                className="text-xs text-indigo-600 font-semibold hover:underline"
              >
                {allSelected ? t('deselectAll') : t('selectAll')}
              </button>
              {selectedCount > 0 && (
                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-slate-500">{t('selected', { count: selectedCount })}</span>
                  <ButtonPrimary
                    onClick={() => bulkApproveMut.mutate([...selectedIds])}
                    disabled={bulkApproveMut.isPending || bulkRejectMut.isPending}
                    className="!px-2.5 !text-[11px]"
                  >
                    <CheckCheck className="w-3.5 h-3.5" /> {t('approve')}
                  </ButtonPrimary>
                  <ButtonDanger
                    onClick={() => setBulkRejectOpen(true)}
                    disabled={bulkApproveMut.isPending || bulkRejectMut.isPending}
                    className="!px-2.5 !text-[11px]"
                  >
                    <XCircle className="w-3.5 h-3.5" /> {t('reject')}
                  </ButtonDanger>
                </div>
              )}
            </div>
          )}
        </div>

        {error ? <ErrorAlert error={error} title={t('loadError')} />
          : isLoading && !data ? <LoadingRows count={6} />
          : !data?.requests?.length ? <EmptyState filtered={hasFilters} />
          : (
            <div className="space-y-2">
              {data.requests.map((r) => {
                const isSelected = selectedIds.has(r.id);
                return (
                  <div key={r.id} className={`p-3 rounded-lg border transition-colors ${isSelected ? 'bg-indigo-50 border-indigo-200' : 'bg-slate-50 border-slate-100'}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-2">
                        {/* Checkbox (C-3) */}
                        {r.status === 'submitted' && (
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleSelect(r.id)}
                            className="mt-1.5 w-4 h-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                            aria-label={t('selected', { count: 1 })}
                          />
                        )}
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <Badge tone={r.request_type === 'leave' ? 'sky' : 'indigo'}>{r.request_type === 'leave' ? t('leave') : t('overtime')}</Badge>
                            <Badge tone={statusToneOf(r.status)}>{statusLabel(t, r.status)}</Badge>
                            <span className="text-xs font-semibold text-slate-800">{r.employee_name}</span>
                            <span className="text-[10px] font-mono text-slate-400">{formatDateTime(r.submitted_at)}</span>
                          </div>
                          <p className="text-xs text-slate-600">{renderDetail(r)}</p>
                          {r.reason && <p className="text-xs text-slate-500 mt-1 italic">&ldquo;{r.reason}&rdquo;</p>}
                          {r.review_reason && (
                            <p className="text-[11px] text-slate-500 mt-1">
                              {t('decisionReason')}: {r.review_reason} · {formatDateTime(r.reviewed_at)}
                            </p>
                          )}
                        </div>
                      </div>
                      {r.status === 'submitted' && (
                        <div className="flex gap-2 shrink-0">
                          <ButtonPrimary
                            onClick={() => setApproveTarget(r)}
                            disabled={approveMut.isPending || rejectMut.isPending}
                            className="!px-2.5"
                          >
                            <Check className="w-4 h-4" /> {t('approve')}
                          </ButtonPrimary>
                          <ButtonDanger onClick={() => { setRejectTarget(r); setRejectReason(''); }} className="!px-2.5">
                            <X className="w-4 h-4" /> {t('reject')}
                          </ButtonDanger>
                        </div>
                      )}
                    </div>
                    {approveMut.isError && approveMut.variables === r.id && <div className="mt-2"><ErrorAlert error={approveMut.error} /></div>}
                  </div>
                );
              })}
            </div>
          )}
      </Card>

      {/* Approve confirmation modal (m-2) */}
      <Modal open={!!approveTarget} onClose={() => setApproveTarget(null)} title={t('approveConfirmTitle')}>
        {approveTarget && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              {t.rich('approveConfirm', { type: approveTarget.request_type === 'leave' ? t('leave') : t('overtime'), name: approveTarget.employee_name, strong: (c) => <strong>{c}</strong> })}
            </p>
            <p className="text-xs text-slate-400">{renderDetail(approveTarget)}</p>
            {approveMut.isError && <ErrorAlert error={approveMut.error} />}
            <div className="flex justify-end gap-2">
              <ButtonGhost onClick={() => setApproveTarget(null)}>{t('filter')}</ButtonGhost>
              <ButtonPrimary
                onClick={() => approveTarget && approveMut.mutate(approveTarget.id)}
                disabled={approveMut.isPending}
              >
                {approveMut.isPending ? t('processing') : t('approveConfirmAction')}
              </ButtonPrimary>
            </div>
          </div>
        )}
      </Modal>

      {/* Reject modal with TextArea (M-7) */}
      <Modal open={!!rejectTarget} onClose={() => setRejectTarget(null)} title={t('rejectTitle')}>
        {rejectTarget && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              {rejectTarget.employee_name} · {renderDetail(rejectTarget)}
            </p>
            <Field label={t('rejectReason')}>
              <TextArea
                rows={3}
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder={t('rejectReasonPlaceholder')}
                aria-label={t('rejectReason')}
              />
            </Field>
            {rejectMut.isError && <ErrorAlert error={rejectMut.error} />}
            <div className="flex justify-end gap-2">
              <ButtonGhost onClick={() => setRejectTarget(null)}>{t('filter')}</ButtonGhost>
              <ButtonDanger onClick={() => rejectTarget && rejectMut.mutate({ id: rejectTarget.id, reason: rejectReason })} disabled={rejectMut.isPending || !rejectReason.trim()}>
                {rejectMut.isPending ? t('processing') : t('rejectConfirm')}
              </ButtonDanger>
            </div>
          </div>
        )}
      </Modal>

      {/* Bulk reject modal (C-3) */}
      <Modal open={bulkRejectOpen} onClose={() => setBulkRejectOpen(false)} title={t('bulkRejectTitle', { count: selectedCount })}>
        <div className="space-y-3">
          <p className="text-xs text-slate-500">
            {t.rich('bulkRejectDesc', { count: selectedCount, strong: (c) => <strong>{c}</strong> })}
          </p>
          <Field label={t('bulkRejectReason')}>
            <TextArea
              rows={3}
              value={bulkRejectReason}
              onChange={(e) => setBulkRejectReason(e.target.value)}
              placeholder={t('bulkRejectReasonPlaceholder')}
              aria-label={t('bulkRejectReason')}
            />
          </Field>
          {bulkRejectMut.isError && <ErrorAlert error={bulkRejectMut.error} />}
          <div className="flex justify-end gap-2">
            <ButtonGhost onClick={() => setBulkRejectOpen(false)}>{t('filter')}</ButtonGhost>
            <ButtonDanger
              onClick={() => bulkRejectMut.mutate({ ids: [...selectedIds], reason: bulkRejectReason })}
              disabled={bulkRejectMut.isPending || !bulkRejectReason.trim()}
            >
              {bulkRejectMut.isPending ? t('processing') : t('bulkRejectConfirm', { count: selectedCount })}
            </ButtonDanger>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function statusToneOf(s: string) {
  switch (s) {
    case 'approved': return 'emerald' as const;
    case 'rejected': return 'rose' as const;
    case 'cancelled': return 'slate' as const;
    default: return 'amber' as const;
  }
}
function statusLabel(t: (key: string) => string, s: string) {
  switch (s) {
    case 'submitted': return t('pending');
    case 'approved': return t('approved');
    case 'rejected': return t('rejected');
    case 'cancelled': return t('cancelled');
    default: return s;
  }
}
