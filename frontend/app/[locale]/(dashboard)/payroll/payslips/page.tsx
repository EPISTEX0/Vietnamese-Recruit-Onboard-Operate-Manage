'use client';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useLocale, useTranslations } from 'next-intl';
import {
  FileSpreadsheet, Plus, Search, Eye, Pencil, Send, Trash2, ChevronLeft, ChevronRight, Undo2, CheckSquare, X,
} from 'lucide-react';
import {
  fetchPayslips, fetchPayslip, createPayslip, updatePayslip, publishPayslip, unpublishPayslip, deletePayslip, bulkPublishPayslips,
} from '@/lib/api/admin-payslips';
import type {
  Payslip, PayslipListResponse, CreatePayslipRequest, UpdatePayslipRequest,
} from '@/lib/api/admin-payslips';
import { listEmployees } from '@/lib/api/employees';
import type { EmployeeListResponse } from '@/lib/api/types';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, Select, ButtonPrimary, ButtonGhost, ButtonDanger,
  Badge, ErrorAlert, EmptyState, LoadingRows, Modal, formatVND, formatDateTime,
} from '@/components/shared-ui';

type StatusFilter = '' | 'draft' | 'published';

const blankDraft: CreatePayslipRequest = {
  employee_id: '',
  period_month: '',
  gross_salary: '',
  deductions: '0',
  insurance_employee: '0',
  taxable_income: '0',
  pit_amount: '0',
  net_salary: '',
  pdf_url: '',
};

function toMonthDay(ym: string): string {
  // YYYY-MM → YYYY-MM-01 (BE expects a full date; it normalizes day→1)
  return ym ? `${ym}-01` : '';
}
function toMonth(stat: string): string {
  // period_month date "YYYY-MM-01" → "YYYY-MM"
  if (!stat) return '';
  return stat.slice(0, 7);
}
const empName = (list: EmployeeListResponse | undefined, id: string) =>
  list?.items?.find((e) => e.id === id)?.full_name ?? id.slice(0, 8);

/** Remove Vietnamese diacritics for accent-insensitive search. */
function removeDiacritics(s: string): string {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

const currentYear = new Date().getFullYear();
const YEARS = Array.from({ length: 11 }, (_, i) => currentYear - 5 + i);

function parseMonth(s: string): { year: string; month: string } {
  if (!s || s.length < 7) return { year: '', month: '' };
  return { year: s.slice(0, 4), month: s.slice(5, 7) };
}
function joinMonth(year: string, month: string): string {
  if (!year || !month) return '';
  return `${year}-${month}`;
}

export default function PayslipsPage() {
  const locale = useLocale();
  const t = useTranslations('payroll');
  const qc = useQueryClient();

  const [employeeId, setEmployeeId] = useState('');
  const [status, setStatus] = useState<StatusFilter>('');
  const [periodMonth, setPeriodMonth] = useState('');
  const [page, setPage] = useState(1);
  const [submitted, setSubmitted] = useState({ employeeId, status, periodMonth, page });

  const MONTHS = [
    { value: '01', label: t('month1') },
    { value: '02', label: t('month2') },
    { value: '03', label: t('month3') },
    { value: '04', label: t('month4') },
    { value: '05', label: t('month5') },
    { value: '06', label: t('month6') },
    { value: '07', label: t('month7') },
    { value: '08', label: t('month8') },
    { value: '09', label: t('month9') },
    { value: '10', label: t('month10') },
    { value: '11', label: t('month11') },
    { value: '12', label: t('month12') },
  ];

  const { data: employees } = useQuery<EmployeeListResponse>({
    queryKey: ['employees-list', { all: true }],
    queryFn: () => listEmployees({ page: 1, page_size: 500, is_active: true }),
    staleTime: 60_000,
  });

  const { data, isLoading, error } = useQuery<PayslipListResponse>({
    queryKey: ['admin-payslips', submitted],
    queryFn: () =>
      fetchPayslips({
        page: submitted.page,
        page_size: 20,
        employee_id: submitted.employeeId || undefined,
        status: (submitted.status as 'draft' | 'published' | undefined) || undefined,
        period_month: submitted.periodMonth || undefined,
      }),
    placeholderData: (prev) => prev,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const apply = () => setSubmitted({ employeeId, status, periodMonth, page: 1 });
  const gotoPage = (p: number) => {
    const np = Math.min(Math.max(1, p), totalPages);
    setPage(np);
    setSubmitted({ ...submitted, page: np });
  };

  // Create draft
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<CreatePayslipRequest>(blankDraft);
  const createMut = useMutation({
    mutationFn: (d: CreatePayslipRequest) => createPayslip(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-payslips'] });
      setCreateOpen(false);
      setDraft(blankDraft);
    },
  });
  const submitCreate = () => {
    createMut.mutate({ ...draft, period_month: toMonthDay(draft.period_month) });
  };

  // View / edit
  const [viewId, setViewId] = useState<string | null>(null);
  const { data: viewed } = useQuery<Payslip>({
    queryKey: ['admin-payslip', viewId],
    queryFn: () => fetchPayslip(viewId!),
    enabled: Boolean(viewId),
  });
  const [editMode, setEditMode] = useState(false);
  const [editForm, setEditForm] = useState<UpdatePayslipRequest>({});

  const startEdit = () => {
    if (!viewed) return;
    setEditForm({
      gross_salary: viewed.gross_salary,
      deductions: viewed.deductions,
      insurance_employee: viewed.insurance_employee,
      taxable_income: viewed.taxable_income,
      pit_amount: viewed.pit_amount,
      net_salary: viewed.net_salary,
      pdf_url: viewed.pdf_url ?? '',
    });
    setEditMode(true);
  };

  const editMut = useMutation({
    mutationFn: (d: UpdatePayslipRequest) => updatePayslip(viewId!, d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-payslip', viewId] });
      qc.invalidateQueries({ queryKey: ['admin-payslips'] });
      setEditMode(false);
    },
  });

  const publishMut = useMutation({
    mutationFn: (id: string) => publishPayslip(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-payslip', viewId] });
      qc.invalidateQueries({ queryKey: ['admin-payslips'] });
      setToast({ message: t('publishSuccess'), tone: 'success' });
    },
  });
  const [delConfirm, setDelConfirm] = useState<string | null>(null);
  const [unpublishConfirm, setUnpublishConfirm] = useState<string | null>(null);
  const [empSearch, setEmpSearch] = useState('');
  const [empDropdownOpen, setEmpDropdownOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);

  // Auto-dismiss toast after 3s
  React.useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(t);
    }
  }, [toast]);

  const bulkPubMut = useMutation({
    mutationFn: (ids: string[]) => bulkPublishPayslips(ids),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['admin-payslips'] });
      setSelectedIds(new Set());
      setToast({ message: t('bulkPublishSuccess', { count: data.published_count }), tone: 'success' });
    },
  });

  const unpublishMut = useMutation({
    mutationFn: (id: string) => unpublishPayslip(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-payslip', viewId] });
      qc.invalidateQueries({ queryKey: ['admin-payslips'] });
      setViewId(null);
      setEditMode(false);
      setUnpublishConfirm(null);
      setToast({ message: t('unpublishSuccess'), tone: 'success' });
    },
  });

  const delMut = useMutation({
    mutationFn: (id: string) => deletePayslip(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-payslips'] });
      setViewId(null);
      setDelConfirm(null);
      setToast({ message: t('deleteSuccess'), tone: 'success' });
    },
  });

  const canMutate = (p: Payslip) => p.status === 'draft';

  return (
    <div className="space-y-6">
      <PageHeader
        icon={FileSpreadsheet}
        title={t('title')}
        subtitle={t('subtitle')}
        actions={
          <ButtonPrimary onClick={() => setCreateOpen(true)}>
            <Plus className="w-4 h-4" /> {t('createPayslip')}
          </ButtonPrimary>
        }
      />

      <Card>
        <SectionTitle icon={Search}>{t('filter')}</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
          <Field label={t('employee')}>
            <div className="relative">
              <div className="flex items-center border border-slate-200 rounded-lg bg-white px-2 py-1.5">
                <input
                  type="text"
                  placeholder={t('searchEmployee')}
                  value={empSearch || (employeeId ? empName(employees, employeeId) : '')}
                  onChange={(e) => { setEmpSearch(e.target.value); setEmployeeId(''); setEmpDropdownOpen(true); }}
                  onFocus={() => setEmpDropdownOpen(true)}
                  onBlur={() => setTimeout(() => setEmpDropdownOpen(false), 200)}
                  className="flex-1 text-xs outline-none bg-transparent"
                />
                {employeeId && (
                  <button onClick={() => { setEmployeeId(''); setEmpSearch(''); }} className="text-slate-400 hover:text-slate-600">
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
              {empDropdownOpen && (
                <div className="absolute z-20 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                  <button
                    className="w-full text-left px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50"
                    onMouseDown={() => { setEmployeeId(''); setEmpSearch(''); setEmpDropdownOpen(false); }}
                  >
                    {t('all')}
                  </button>
                  {(employees?.items ?? [])
                    .filter((e) => !empSearch || removeDiacritics(e.full_name.toLowerCase()).includes(removeDiacritics(empSearch.toLowerCase())))
                    .slice(0, 50)
                    .map((e) => (
                      <button
                        key={e.id}
                        className="w-full text-left px-3 py-1.5 text-xs text-slate-700 hover:bg-indigo-50"
                        onMouseDown={() => { setEmployeeId(e.id); setEmpSearch(''); setEmpDropdownOpen(false); }}
                      >
                        {e.full_name}
                      </button>
                    ))}
                </div>
              )}
            </div>
          </Field>
          <Field label={t('status')}>
            <Select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
              <option value="">{t('all')}</option>
              <option value="draft">{t('draft')}</option>
              <option value="published">{t('published')}</option>
            </Select>
          </Field>
              <Field label={t('period')}>
                <div className="flex gap-2">
                  <Select value={parseMonth(periodMonth).year} onChange={(e) => setPeriodMonth(joinMonth(e.target.value, parseMonth(periodMonth).month))}>
                    <option value="">{t('year')}</option>
                    {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
                  </Select>
                  <Select value={parseMonth(periodMonth).month} onChange={(e) => setPeriodMonth(joinMonth(parseMonth(periodMonth).year, e.target.value))}>
                    <option value="">{t('month')}</option>
                    {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </Select>
                </div>
              </Field>
          <div />
          <ButtonPrimary onClick={apply} className="h-[38px]"><Search className="w-4 h-4" /> {t('filter')}</ButtonPrimary>
        </div>
      </Card>

      <Card>
        <SectionTitle icon={FileSpreadsheet}>{t('sectionTitle')}</SectionTitle>
        {selectedIds.size > 0 && (
          <div className="mb-3 flex items-center gap-2 p-2 bg-indigo-50 rounded-lg">
            <span className="text-xs text-indigo-700 font-medium">{t('selectedCount', { count: selectedIds.size })}</span>
            <ButtonPrimary onClick={() => bulkPubMut.mutate([...selectedIds])} disabled={bulkPubMut.isPending} className="!py-1 !px-2 !text-xs">
              <Send className="w-3 h-3" /> {bulkPubMut.isPending ? t('publishingSelected') : t('publishSelected')}
            </ButtonPrimary>
            <ButtonGhost onClick={() => setSelectedIds(new Set())} className="!py-1 !px-2 !text-xs">
              {t('deselect')}
            </ButtonGhost>
          </div>
        )}
        {error ? <ErrorAlert error={error} title={t('loadError')} />
          : isLoading && !data ? <LoadingRows count={6} />
          : !data?.payslips?.length ? <EmptyState filtered={Boolean(submitted.employeeId || submitted.status || submitted.periodMonth)} />
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[10px] font-mono uppercase text-slate-400 border-b border-slate-100">
                    <th className="py-2 px-2 w-8">
                    <input
                      type="checkbox"
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedIds(new Set(data.payslips.filter(p => p.status === 'draft').map(p => p.id)));
                        } else {
                          setSelectedIds(new Set());
                        }
                      }}
                      checked={data && data.payslips.filter(p => p.status === 'draft').length > 0 && data.payslips.filter(p => p.status === 'draft').every(p => selectedIds.has(p.id))}
                      className="rounded border-slate-300"
                    />
                  </th>
                  <th className="py-2 px-2">{t('employee')}</th>
                    <th className="py-2 px-2">{t('period')}</th>
                    <th className="py-2 px-2">{t('grossSalary')}</th>
                    <th className="py-2 px-2">{t('netSalary')}</th>
                    <th className="py-2 px-2">{t('status')}</th>
                    <th className="py-2 px-2">{t('actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.payslips.map((p) => (
                    <tr key={p.id} className="border-b border-slate-50 hover:bg-slate-50">
                      <td className="py-2.5 px-2">
                    {p.status === 'draft' && (
                      <input
                        type="checkbox"
                        checked={selectedIds.has(p.id)}
                        onChange={() => {
                          const next = new Set(selectedIds);
                          if (next.has(p.id)) next.delete(p.id);
                          else next.add(p.id);
                          setSelectedIds(next);
                        }}
                        className="rounded border-slate-300"
                      />
                    )}
                  </td>
                  <td className="py-2.5 px-2 text-xs font-semibold text-slate-800">{empName(employees, p.employee_id)}</td>
                      <td className="py-2.5 px-2 font-mono text-xs text-slate-500">{toMonth(p.period_month)}</td>
                      <td className="py-2.5 px-2 text-xs text-slate-600">{formatVND(p.gross_salary)}</td>
                      <td className="py-2.5 px-2 text-xs font-semibold text-slate-800">{formatVND(p.net_salary)}</td>
                      <td className="py-2.5 px-2"><Badge tone={p.status === 'published' ? 'emerald' : 'amber'}>{p.status === 'published' ? t('published') : t('draft')}</Badge></td>
                          <td className="py-2.5 px-2">
                            <div className="flex items-center gap-0.5">
                              <button onClick={() => { setViewId(p.id); setEditMode(false); }} className="p-1.5 rounded-lg hover:bg-slate-200 text-slate-500 hover:text-indigo-600 transition-all" title={t('view')}>
                                <Eye className="w-4 h-4" />
                              </button>
                              {p.status === 'draft' && (
                                <>
                                  <button onClick={() => { setViewId(p.id); setEditMode(true); }} className="p-1.5 rounded-lg hover:bg-amber-100 text-slate-500 hover:text-amber-600 transition-all" title={t('editTooltip')}>
                                    <Pencil className="w-4 h-4" />
                                  </button>
                                      <button onClick={() => publishMut.mutate(p.id)} disabled={publishMut.isPending} className="p-1.5 rounded-lg hover:bg-emerald-100 text-slate-500 hover:text-emerald-600 transition-all disabled:opacity-50" title={t('publishTooltip')}>
                                        <Send className="w-4 h-4" />
                                      </button>
                                  <button onClick={() => setDelConfirm(p.id)} className="p-1.5 rounded-lg hover:bg-rose-100 text-slate-500 hover:text-rose-600 transition-all" title={t('deleteTooltip')}>
                                    <Trash2 className="w-4 h-4" />
                                  </button>
                                </>
                              )}
                            </div>
                          </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

        {data && data.total > data.page_size && (
          <div className="flex items-center justify-between pt-4 border-t border-slate-100 mt-4">
            <span className="text-xs text-slate-500">{t('payslipCount', { total: data.total })} · {t('pageOf', { page: submitted.page, total: totalPages })}</span>
            <div className="flex items-center gap-2">
              <button onClick={() => gotoPage(submitted.page - 1)} disabled={submitted.page <= 1} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all"><ChevronLeft className="w-4 h-4" /></button>
              <button onClick={() => gotoPage(submitted.page + 1)} disabled={submitted.page >= totalPages} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all"><ChevronRight className="w-4 h-4" /></button>
            </div>
          </div>
        )}
      </Card>

      {/* Create draft modal */}
      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title={t('createPayslip')}>
        <div className="space-y-3">
          <Field label={t('employeeRequired')}>
            <div className="relative">
              <div className="flex items-center border border-slate-200 rounded-lg bg-white px-2 py-1.5">
                <input
                  type="text"
                  placeholder={t('searchEmployee')}
                  value={draft.employee_id ? empName(employees, draft.employee_id) : ''}
                  onChange={(e) => { setDraft({ ...draft, employee_id: '' }); }}
                  onFocus={() => setEmpDropdownOpen(true)}
                  className="flex-1 text-xs outline-none bg-transparent"
                />
                {draft.employee_id && (
                  <button onClick={() => setDraft({ ...draft, employee_id: '' })} className="text-slate-400 hover:text-slate-600">
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
              {empDropdownOpen && (
                <div className="absolute z-20 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                  {(employees?.items ?? [])
                    .slice(0, 50)
                    .map((e) => (
                      <button
                        key={e.id}
                        className="w-full text-left px-3 py-1.5 text-xs text-slate-700 hover:bg-indigo-50"
                        onMouseDown={() => { setDraft({ ...draft, employee_id: e.id }); setEmpDropdownOpen(false); }}
                      >
                        {e.full_name}
                      </button>
                    ))}
                </div>
              )}
            </div>
          </Field>
              <Field label={t('periodRequired')}>
                <div className="flex gap-2">
                  <Select value={parseMonth(draft.period_month).year} onChange={(e) => setDraft({ ...draft, period_month: joinMonth(e.target.value, parseMonth(draft.period_month).month) })}>
                    <option value="">{t('year')}</option>
                    {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
                  </Select>
                  <Select value={parseMonth(draft.period_month).month} onChange={(e) => setDraft({ ...draft, period_month: joinMonth(parseMonth(draft.period_month).year, e.target.value) })}>
                    <option value="">{t('month')}</option>
                    {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </Select>
                </div>
              </Field>
          <div className="grid grid-cols-2 gap-3">
                <Field label={t('grossSalary')} hint={t('hintGross')}><TextInput type="number" placeholder="20000000" value={draft.gross_salary} onChange={(e) => setDraft({ ...draft, gross_salary: e.target.value })} /></Field>
                <Field label={t('netSalary')} hint={t('hintNet')}><TextInput type="number" placeholder="18000000" value={draft.net_salary} onChange={(e) => setDraft({ ...draft, net_salary: e.target.value })} /></Field>
                <Field label={t('deductions')} hint={t('hintDeductions')}><TextInput value={draft.deductions} onChange={(e) => setDraft({ ...draft, deductions: e.target.value })} /></Field>
                <Field label={t('insurance')} hint={t('hintInsurance')}><TextInput value={draft.insurance_employee} onChange={(e) => setDraft({ ...draft, insurance_employee: e.target.value })} /></Field>
                <Field label={t('taxableIncome')} hint={t('hintTaxableIncome')}><TextInput value={draft.taxable_income} onChange={(e) => setDraft({ ...draft, taxable_income: e.target.value })} /></Field>
                <Field label={t('pitAmount')} hint={t('hintPIT')}><TextInput value={draft.pit_amount} onChange={(e) => setDraft({ ...draft, pit_amount: e.target.value })} /></Field>
          </div>
          <Field label={t('pdfOptional')}><TextInput value={draft.pdf_url} onChange={(e) => setDraft({ ...draft, pdf_url: e.target.value })} /></Field>
                                {(() => {
                                  const g = parseFloat(draft.gross_salary) || 0;
                                  const d = parseFloat(draft.deductions ?? '0') || 0;
                                  const ins = parseFloat(draft.insurance_employee ?? '0') || 0;
                                  const pit = parseFloat(draft.pit_amount ?? '0') || 0;
                                  const calcNet = g - d - ins - pit;
                                  const manualNet = parseFloat(draft.net_salary) || 0;
                                  const mismatch = g > 0 && manualNet > 0 && calcNet !== manualNet;
                                  if (mismatch) {
                                    return <p className="text-xs text-red-600 font-medium mt-1">⚠ {t('netMismatch', { net: calcNet.toLocaleString("vi-VN"), manual: manualNet.toLocaleString("vi-VN") })}</p>;
                                  }
                                  if (g > 0) {
                                    return <p className="text-xs text-emerald-600 mt-1">✓ {t('netMatch', { net: calcNet.toLocaleString("vi-VN") })}</p>;
                                  }
                                  return null;
                                })()}
          {createMut.isError && <ErrorAlert error={createMut.error} />}
          <div className="flex justify-end gap-2">
            <ButtonGhost onClick={() => setCreateOpen(false)}>{t('cancel')}</ButtonGhost>
                <ButtonPrimary onClick={submitCreate} disabled={createMut.isPending || !draft.employee_id || !draft.period_month || !draft.gross_salary || !draft.net_salary || (() => { const g = parseFloat(draft.gross_salary) || 0; const d = parseFloat(draft.deductions ?? '0') || 0; const ins = parseFloat(draft.insurance_employee ?? '0') || 0; const pit = parseFloat(draft.pit_amount ?? '0') || 0; const calcNet = g - d - ins - pit; const manualNet = parseFloat(draft.net_salary) || 0; return g > 0 && manualNet > 0 && calcNet !== manualNet; })()}>
              {createMut.isPending ? t('createDrafting') : t('createDraft')}
            </ButtonPrimary>
          </div>
        </div>
      </Modal>

      {/* View / edit / publish / delete modal */}
      <Modal open={!!viewId} onClose={() => { setViewId(null); setEditMode(false); }} title={editMode ? t('editPayslip') : t('viewDetail')}>
        {viewed && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={viewed.status === 'published' ? 'emerald' : 'amber'}>{viewed.status === 'published' ? t('published') : t('draft')}</Badge>
              <span className="text-xs font-semibold text-slate-800">{empName(employees, viewed.employee_id)}</span>
              <span className="font-mono text-[10px] text-slate-400">{t('period')} {toMonth(viewed.period_month)}</span>
              {viewed.published_at && <span className="text-[10px] text-slate-400">{t('publishedAt')} {formatDateTime(viewed.published_at)}</span>}
            </div>

            {!editMode ? (
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Row label={t('grossSalary')} value={formatVND(viewed.gross_salary)} />
                <Row label={t('netSalary')} value={formatVND(viewed.net_salary)} />
                <Row label={t('deductions')} value={formatVND(viewed.deductions)} />
                <Row label={t('insurance')} value={formatVND(viewed.insurance_employee)} />
                <Row label={t('taxableIncome')} value={formatVND(viewed.taxable_income)} />
                <Row label={t('pitAmount')} value={formatVND(viewed.pit_amount)} />
                {viewed.pdf_url && <div className="col-span-2"><Row label={t('pdfUrl')} value={viewed.pdf_url} /></div>}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                    <Field label={t('grossSalary')} hint={t('hintGross')}><TextInput value={editForm.gross_salary ?? ''} onChange={(e) => setEditForm({ ...editForm, gross_salary: e.target.value })} /></Field>
                    <Field label={t('netSalary')} hint={t('hintNet')}><TextInput value={editForm.net_salary ?? ''} onChange={(e) => setEditForm({ ...editForm, net_salary: e.target.value })} /></Field>
                    <Field label={t('deductions')} hint={t('hintDeductions')}><TextInput value={editForm.deductions ?? ''} onChange={(e) => setEditForm({ ...editForm, deductions: e.target.value })} /></Field>
                    <Field label={t('insurance')} hint={t('hintInsurance')}><TextInput value={editForm.insurance_employee ?? ''} onChange={(e) => setEditForm({ ...editForm, insurance_employee: e.target.value })} /></Field>
                    <Field label={t('taxableIncome')} hint={t('hintTaxableIncome')}><TextInput value={editForm.taxable_income ?? ''} onChange={(e) => setEditForm({ ...editForm, taxable_income: e.target.value })} /></Field>
                    <Field label={t('pitAmount')} hint={t('hintPIT')}><TextInput value={editForm.pit_amount ?? ''} onChange={(e) => setEditForm({ ...editForm, pit_amount: e.target.value })} /></Field>
                    <Field label={t('pdfClear')}><TextInput value={editForm.pdf_url ?? ''} onChange={(e) => setEditForm({ ...editForm, pdf_url: e.target.value })} /></Field>
                      {(() => {
                        const g = parseFloat(editForm.gross_salary ?? '0') || 0;
                        const d = parseFloat(editForm.deductions ?? '0') || 0;
                        const ins = parseFloat(editForm.insurance_employee ?? '0') || 0;
                        const pit = parseFloat(editForm.pit_amount ?? '0') || 0;
                        const calcNet = g - d - ins - pit;
                        const manualNet = parseFloat(editForm.net_salary ?? '0') || 0;
                        const mismatch = g > 0 && manualNet > 0 && calcNet !== manualNet;
                        if (mismatch) {
                          return <p className="text-xs text-red-600 font-medium mt-1 col-span-2">⚠ {t('netMismatch', { net: calcNet.toLocaleString("vi-VN"), manual: manualNet.toLocaleString("vi-VN") })}</p>;
                        }
                        if (g > 0) {
                          return <p className="text-xs text-emerald-600 mt-1 col-span-2">✓ {t('netMatch', { net: calcNet.toLocaleString("vi-VN") })}</p>;
                        }
                        return null;
                      })()}
              </div>
            )}

            {editMode && editMut.isError && <ErrorAlert error={editMut.error} />}
            {publishMut.isError && <ErrorAlert error={publishMut.error} />}
            {bulkPubMut.isError && <ErrorAlert error={bulkPubMut.error} />}
            {unpublishMut.isError && <ErrorAlert error={unpublishMut.error} />}
            {delMut.isError && <ErrorAlert error={delMut.error} />}

            <div className="flex flex-wrap justify-between gap-2 pt-2 border-t border-slate-100">
              <div className="flex gap-2">
                {canMutate(viewed) ? (
                  <ButtonDanger onClick={() => setDelConfirm(viewed.id)} disabled={delMut.isPending}>
                    <Trash2 className="w-4 h-4" /> {t('deleteDraft')}
                  </ButtonDanger>
                ) : (
                  <ButtonGhost onClick={() => setUnpublishConfirm(viewed.id)} disabled={unpublishMut.isPending}>
                    <Undo2 className="w-4 h-4" /> {unpublishMut.isPending ? t('publishing') : t('unpublish')}
                  </ButtonGhost>
                )}
              </div>
              <div className="flex gap-2">
                {editMode ? (
                  <>
                    <ButtonGhost onClick={() => setEditMode(false)}>{t('editModeCancel')}</ButtonGhost>
                        <ButtonPrimary onClick={() => editMut.mutate(editForm)} disabled={editMut.isPending || (() => { const g = parseFloat(editForm.gross_salary ?? '0') || 0; const d = parseFloat(editForm.deductions ?? '0') || 0; const ins = parseFloat(editForm.insurance_employee ?? '0') || 0; const pit = parseFloat(editForm.pit_amount ?? '0') || 0; const calcNet = g - d - ins - pit; const manualNet = parseFloat(editForm.net_salary ?? '0') || 0; return g > 0 && manualNet > 0 && calcNet !== manualNet; })()}>
                      <Pencil className="w-4 h-4" /> {editMut.isPending ? t('savingDraft') : t('saveDraft')}
                    </ButtonPrimary>
                  </>
                ) : (
                  <>
                    <ButtonGhost onClick={() => { setViewId(null); setEditMode(false); }}>{t('close')}</ButtonGhost>
                    <ButtonGhost onClick={startEdit} disabled={!canMutate(viewed)}>
                      <Pencil className="w-4 h-4" /> {t('editTooltip')}
                    </ButtonGhost>
                    <ButtonPrimary onClick={() => publishMut.mutate(viewed.id)} disabled={publishMut.isPending || !canMutate(viewed)}>
                      <Send className="w-4 h-4" /> {publishMut.isPending ? t('publishing') : t('publish')}
                    </ButtonPrimary>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* Unpublish confirmation modal */}
          <Modal open={!!unpublishConfirm} onClose={() => setUnpublishConfirm(null)} title={t('unpublishTitle')}>
            <div className="space-y-4">
              <p className="text-sm text-slate-600">
                {t('unpublishConfirmText')}
              </p>
              {unpublishMut.isError && <ErrorAlert error={unpublishMut.error} />}
              <div className="flex justify-end gap-2">
                <ButtonGhost onClick={() => setUnpublishConfirm(null)}>{t('cancel')}</ButtonGhost>
                <ButtonPrimary onClick={() => unpublishMut.mutate(unpublishConfirm!)} disabled={unpublishMut.isPending}>
                  <Undo2 className="w-4 h-4" /> {unpublishMut.isPending ? t('confirmingUnpublish') : t('confirmUnpublish')}
                </ButtonPrimary>
              </div>
            </div>
          </Modal>

          {/* Delete confirmation modal */}
      <Modal open={!!delConfirm} onClose={() => setDelConfirm(null)} title={t('deleteTitle')}>
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            {t('deleteConfirmText')}
          </p>
          {delMut.isError && <ErrorAlert error={delMut.error} />}
          <div className="flex justify-end gap-2">
            <ButtonGhost onClick={() => setDelConfirm(null)}>{t('cancel')}</ButtonGhost>
            <ButtonDanger onClick={() => delMut.mutate(delConfirm!)} disabled={delMut.isPending}>
              <Trash2 className="w-4 h-4" /> {delMut.isPending ? t('confirmingDelete') : t('confirmDelete')}
            </ButtonDanger>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-2 bg-slate-50 rounded-lg border border-slate-100">
      <p className="text-[10px] font-mono text-slate-400 uppercase">{label}</p>
      <p className="text-xs font-semibold text-slate-800">{value}</p>
    </div>
  );
}
