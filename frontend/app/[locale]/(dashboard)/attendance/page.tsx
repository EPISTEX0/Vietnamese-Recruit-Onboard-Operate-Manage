    'use client';

    import React, { useState } from 'react';
    import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
    import { useTranslations } from 'next-intl';
    import {
      Clock, Network, Plus, Trash2, Save, Pencil, ChevronLeft, ChevronRight,
    } from 'lucide-react';
    import {
      listAttendanceRecords, correctAttendanceRecord,
      getNetworkAllowlist, updateNetworkAllowlist, addNetworkToAllowlist, removeNetworkFromAllowlist,
    } from '@/lib/api/attendance';
    import type {
      AttendanceListResponse, AttendanceRecord, CorrectionData,
      NetworkAllowlistResponse,
    } from '@/lib/api/attendance';
    import { listEmployees } from '@/lib/api/employees';
    import type { EmployeeListResponse } from '@/lib/api/types';
    import {
      PageHeader, Card, SectionTitle, Field, TextInput, Select, ButtonPrimary, ButtonGhost, ButtonDanger,
      Badge, ErrorAlert, EmptyState, LoadingRows, Modal, useFormatDateTime,
    } from '@/components/shared-ui';
    import { toast } from 'sonner';

    type Tab = 'records' | 'network';

    function todayISO() {
      return new Date().toISOString().slice(0, 10);
    }
    function firstOfMonthISO() {
      const d = new Date();
      return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
    }
    /** Convert an ISO datetime to a datetime-local input value (no timezone shift). */
    function toLocalInput(iso: string | null): string {
      if (!iso) return '';
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return '';
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    /** Convert a datetime-local value to ISO string (or null if cleared). */
    function fromLocalInput(value: string): string | null {
      if (!value) return null;
      const d = new Date(value);
      if (Number.isNaN(d.getTime())) return null;
      return d.toISOString();
    }

    export default function AttendancePage() {
      const t = useTranslations('attendance');
      const qc = useQueryClient();
      const [tab, setTab] = useState<Tab>('records');

      return (
        <div className="space-y-6">
          <PageHeader
            icon={Clock}
            title={t('title')}
            subtitle={t('subtitle')}
          />

          <div className="flex gap-2">
            <button
              onClick={() => setTab('records')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                tab === 'records' ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'
              }`}
            >
              <Clock className="w-4 h-4 inline mr-1.5" /> {t('recordsTab')}
            </button>
            <button
              onClick={() => setTab('network')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                tab === 'network' ? 'bg-indigo-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'
              }`}
            >
              <Network className="w-4 h-4 inline mr-1.5" /> {t('networkTab')}
            </button>
          </div>

          {tab === 'records' ? <RecordsTab /> : <NetworkTab qc={qc} />}
        </div>
      );
    }

    // ---------------------------------------------------------------------------
    // Records tab
    // ---------------------------------------------------------------------------

    function RecordsTab() {
      const t = useTranslations('attendance');
      const tc = useTranslations('common');
      const qc = useQueryClient();
      const formatDateTime = useFormatDateTime();
      const [start, setStart] = useState(firstOfMonthISO());
      const [end, setEnd] = useState(todayISO());
      const [employeeId, setEmployeeId] = useState('');
      const [status, setStatus] = useState<'checked_in' | 'completed' | ''>('');
      const [page, setPage] = useState(1);
      const [submitted, setSubmitted] = useState({ start, end, employeeId, status, page });

      const { data: employees } = useQuery<EmployeeListResponse>({
        queryKey: ['employees-list', { active: true, all: true }],
            queryFn: () => listEmployees({ page: 1, page_size: 200, is_active: true }),
        staleTime: 60_000,
      });

      const { data, isLoading, error } = useQuery<AttendanceListResponse>({
        queryKey: ['attendance-records', submitted],
        queryFn: () =>
          listAttendanceRecords({
            start_date: submitted.start,
            end_date: submitted.end,
            employee_id: submitted.employeeId || undefined,
            status: (submitted.status as 'checked_in' | 'completed' | undefined) || undefined,
            page: submitted.page,
            page_size: 20,
          }),
        enabled: Boolean(submitted.start && submitted.end),
        placeholderData: (prev) => prev,
      });

      const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
          const apply = () => {
            if (end < start) {
              toast.error(t('endDateAfterStart'));
              return;
            }
            setSubmitted({ start, end, employeeId, status, page: 1 });
          };

      // Correction dialog
      const [correctTarget, setCorrectTarget] = useState<AttendanceRecord | null>(null);
      const [cIn, setCIn] = useState('');
      const [cOut, setCOut] = useState('');
      const [cReason, setCReason] = useState('');

      const openCorrect = (r: AttendanceRecord) => {
        setCorrectTarget(r);
        setCIn(toLocalInput(r.check_in_at));
        setCOut(toLocalInput(r.check_out_at));
        setCReason('');
      };

      const correctMut = useMutation({
        mutationFn: (payload: CorrectionData) => correctAttendanceRecord(correctTarget!.id, payload),
        retry: 1,
      onSuccess: (data) => {
        qc.invalidateQueries({ queryKey: ['attendance-records'] });
        setCorrectTarget(null);
        toast.success(data.message || t('correctionSaved'));
      },
      onError: (err: any) => toast.error(err?.message || t('correctionError')),
    });

      const submitCorrection = () => {
        const payload: CorrectionData = {
          check_in_at: fromLocalInput(cIn),
          check_out_at: fromLocalInput(cOut),
          correction_reason: cReason,
        };
        correctMut.mutate(payload);
      };

      const gotoPage = (p: number) => {
        const np = Math.min(Math.max(1, p), totalPages);
        setPage(np);
        setSubmitted({ ...submitted, page: np });
      };

      return (
        <Card>
          <SectionTitle icon={Clock}>{t('filterTitle')}</SectionTitle>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
            <Field label={t('fromDate')}><TextInput type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <Field label={t('toDate')}><TextInput type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
            <Field label={t('employee')}>
              <Select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}>
                <option value="">{t('all')}</option>
                {employees?.items?.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
              </Select>
            </Field>
            <Field label={t('status')}>
              <Select value={status} onChange={(e) => setStatus(e.target.value as 'checked_in' | 'completed' | '')}>
                <option value="">{t('all')}</option>
                <option value="checked_in">{t('checkedIn')}</option>
                <option value="completed">{t('checkedOut')}</option>
              </Select>
            </Field>
            <ButtonPrimary onClick={apply} className="h-[38px]">{t('filter')}</ButtonPrimary>
          </div>

          <div className="mt-4">
            {error ? (
              <ErrorAlert error={error} title={t('loadError')} />
            ) : isLoading && !data ? (
              <LoadingRows count={6} />
            ) : !data?.records?.length ? (
              <EmptyState filtered={Boolean(submitted.employeeId || submitted.status)} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[10px] font-mono uppercase text-slate-400 border-b border-slate-100">
                      <th className="py-2 px-2">{t('employeeColumn')}</th>
                      <th className="py-2 px-2">{t('dateColumn')}</th>
                      <th className="py-2 px-2">{t('checkInColumn')}</th>
                      <th className="py-2 px-2">{t('checkOutColumn')}</th>
                      <th className="py-2 px-2">{t('ipColumn')}</th>
                          <th className="py-2 px-2">{t('statusColumn')}</th>
                          <th className="py-2 px-2">{t('correctColumn')}</th>
                          <th className="py-2 px-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.records.map((r) => (
                      <tr key={r.id} className="border-b border-slate-50 hover:bg-slate-50">
                        <td className="py-2.5 px-2">
                          <div className="font-semibold text-slate-800 text-xs">{r.employee_name ?? '—'}</div>
                          <div className="font-mono text-[10px] text-slate-400">{r.employee_code ?? ''}</div>
                        </td>
                        <td className="py-2.5 px-2 text-xs text-slate-600">{r.work_date}</td>
                        <td className="py-2.5 px-2 text-xs text-slate-600">{formatDateTime(r.check_in_at)}</td>
                        <td className="py-2.5 px-2 text-xs text-slate-600">{formatDateTime(r.check_out_at)}</td>
                        <td className="py-2.5 px-2 font-mono text-[10px] text-slate-400">{r.check_in_ip ?? '—'}</td>
                            <td className="py-2.5 px-2">
                              <Badge tone={r.check_out_at ? 'emerald' : 'amber'}>
                                {r.check_out_at ? 'completed' : 'checked_in'}
                              </Badge>
                            </td>
                            <td className="py-2.5 px-2">
                              {r.corrected_at ? (
                                <span className="text-[10px] text-amber-600 font-medium" title={t('reason', { reason: r.correction_reason ?? '—' })}>{t('corrected')}</span>
                              ) : (
                                <span className="text-[10px] text-slate-300">—</span>
                              )}
                            </td>
                            <td className="py-2.5 px-2">
                              <button onClick={() => openCorrect(r)} className="p-1.5 rounded-lg hover:bg-indigo-50 text-slate-500 hover:text-indigo-600 transition-all" title={t('correct')}>
                                <Pencil className="w-4 h-4" />
                              </button>
                            </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {data && data.total > data.page_size && (
              <div className="flex items-center justify-between pt-4 border-t border-slate-100 mt-4">
                  <span className="text-xs text-slate-500">{t('recordsCount', { count: data.total })} · trang {submitted.page} / {totalPages}</span>
                    <button
                      onClick={() => {
                        const csv = [
                          [t('employeeColumn'), t('idColumn'), t('dateColumn'), t('checkInColumn'), t('checkOutColumn'), t('ipColumn'), t('statusColumn'), t('correctColumn')].join(','),
                          ...data.records.map((r) => [
                            r.employee_name ?? '',
                            r.employee_code ?? '',
                            r.work_date,
                            r.check_in_at ?? '',
                            r.check_out_at ?? '',
                            r.check_in_ip ?? '',
                            r.check_out_at ? 'completed' : 'checked_in',
                            r.corrected_at ? 'yes' : '',
                          ].join(',')),
                        ].join('\n');
                        const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `cham-cong-${submitted.start}_${submitted.end}.csv`;
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="text-[10px] text-indigo-600 hover:text-indigo-800 font-medium"
                    >
                      {t('exportCsv')}
                    </button>
                <div className="flex items-center gap-2">
                  <button onClick={() => gotoPage(submitted.page - 1)} disabled={submitted.page <= 1} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all"><ChevronLeft className="w-4 h-4" /></button>
                  <button onClick={() => gotoPage(submitted.page + 1)} disabled={submitted.page >= totalPages} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all"><ChevronRight className="w-4 h-4" /></button>
                </div>
              </div>
            )}
          </div>

          {/* Correction modal */}
          <Modal open={!!correctTarget} onClose={() => setCorrectTarget(null)} title={t('correctTitle')}>
            {correctTarget && (
              <div className="space-y-3">
                    <p className="text-xs text-slate-500">
                      {t.rich('correctEmployee', {
                        name: correctTarget.employee_name ?? '—',
                        date: correctTarget.work_date,
                        strong: (chunks) => <strong>{chunks}</strong>,
                      })}
                      <span className="text-[10px] text-slate-400 ml-2">{t('correctLocalTime')}</span>
                    </p>
                    <p className="text-[10px] text-slate-400">{t('correctAuditNote')}</p>
                    {correctTarget.corrected_at && (
                      <div className="p-2 bg-amber-50 rounded-lg border border-amber-100 text-[10px] text-amber-700">
                        {t('correctPrevious', { time: formatDateTime(correctTarget.corrected_at), reason: correctTarget.correction_reason ?? '—' })}
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-3">
                      <Field label={t('correctCheckIn')}>
                        <TextInput type="datetime-local" value={cIn} onChange={(e) => setCIn(e.target.value)} />
                      </Field>
                      <Field label={t('correctCheckOut')}>
                        <TextInput type="datetime-local" value={cOut} onChange={(e) => setCOut(e.target.value)} />
                      </Field>
                </div>
                <Field label={t('correctReason')}>
                  <TextInput value={cReason} onChange={(e) => setCReason(e.target.value)} placeholder={t('correctReasonPlaceholder')} />
                </Field>
                {correctMut.isError && <ErrorAlert error={correctMut.error} />}
                <div className="flex justify-end gap-2 pt-2">
                  <ButtonGhost onClick={() => setCorrectTarget(null)}>{tc('cancel')}</ButtonGhost>
                  <ButtonPrimary onClick={submitCorrection} disabled={correctMut.isPending || !cReason || (!cIn && !cOut)}>
                    <Save className="w-4 h-4" /> {correctMut.isPending ? tc('saving') : t('saveCorrection')}
                  </ButtonPrimary>
                </div>
              </div>
            )}
              </Modal>

            </Card>
          );
        }

        // ---------------------------------------------------------------------------
        // Network allowlist tab
        // ---------------------------------------------------------------------------
    // Network allowlist tab
    // ---------------------------------------------------------------------------

    function NetworkTab({ qc }: { qc: ReturnType<typeof useQueryClient> }) {
      const t = useTranslations('attendance');
      const formatDateTime = useFormatDateTime();
      const tc = useTranslations('common');
      const { data, isLoading, error } = useQuery<NetworkAllowlistResponse>({
        queryKey: ['attendance-network-allowlist'],
        queryFn: getNetworkAllowlist,
      });

          const [newCidr, setNewCidr] = useState('');
          const [bulkText, setBulkText] = useState('');
          const [replaceOpen, setReplaceOpen] = useState(false);
          const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

          const addMut = useMutation({
            mutationFn: (cidrs: string[]) => addNetworkToAllowlist(cidrs),
            retry: 1,
            onSuccess: () => {
              qc.invalidateQueries({ queryKey: ['attendance-network-allowlist'] });
              toast.success(t('cidrAdded'));
            },
            onError: (err: any) => toast.error(err?.message || tc('error')),
          });
          const removeMut = useMutation({
            mutationFn: (cidr: string) => removeNetworkFromAllowlist(cidr),
            retry: 1,
            onSuccess: () => {
              qc.invalidateQueries({ queryKey: ['attendance-network-allowlist'] });
              toast.success(t('cidrRemoved'));
            },
            onError: (err: any) => toast.error(err?.message || tc('error')),
          });
          const replaceMut = useMutation({
            mutationFn: (networks: string[]) => updateNetworkAllowlist(networks),
            retry: 1,
            onSuccess: () => {
              qc.invalidateQueries({ queryKey: ['attendance-network-allowlist'] });
              setReplaceOpen(false);
              setBulkText('');
              toast.success(t('cidrUpdated'));
            },
            onError: (err: any) => toast.error(err?.message || tc('error')),
          });

          const cidrRegex = /^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(\/(\d{1,2}))?$/;
          const addOne = () => {
            const v = newCidr.trim();
            if (!v) return;
            if (!cidrRegex.test(v)) {
              toast.error(t('cidrInvalidFormat'));
              return;
            }
            addMut.mutate([v]);
            setNewCidr('');
          };

      return (
        <Card>
          <SectionTitle icon={Network}>{t('networkTitle')}</SectionTitle>
              {!data?.networks?.length && !isLoading ? (
                <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
                  {t.rich('networkEmpty', { strong: (chunks) => <strong>{chunks}</strong> })}
                </p>
              ) : (
                <p className="text-xs text-slate-500 mb-4">
                  {t('networkDesc')}
                </p>
              )}

          {error ? <ErrorAlert error={error} title={t('networkLoadError')} />
            : isLoading && !data ? <LoadingRows count={3} />
            : !data?.networks?.length ? (
              <EmptyState filtered={false} emptyData={t('networkNoData')} />
            ) : (
              <div className="space-y-2">
                {data.networks.map((cidr) => (
                  <div key={cidr} className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="font-mono text-xs text-slate-700 flex-1">{cidr}</span>
                        <button onClick={() => setDeleteConfirm(cidr)} disabled={removeMut.isPending} className="p-1.5 rounded-lg hover:bg-rose-100 text-slate-400 hover:text-rose-500 transition-all" title={tc('delete')}>
                          <Trash2 className="w-4 h-4" />
                        </button>
                  </div>
                ))}
              </div>
            )}

              {data?.updated_at && (
                <p className="text-[10px] text-slate-400 mb-2">{t('networkLastUpdate', { time: formatDateTime(data.updated_at) })}</p>
              )}
              <div className="mt-4 pt-4 border-t border-slate-100 space-y-3">
                <Field label={t('networkAddCidr')} hint={t('networkAddHint')}>
                  <div className="flex gap-2">
                    <TextInput value={newCidr} onChange={(e) => setNewCidr(e.target.value)} placeholder={t('networkAddPlaceholder')} />
                    <ButtonPrimary onClick={addOne} disabled={addMut.isPending || !newCidr.trim()} className="whitespace-nowrap">
                      <Plus className="w-4 h-4" /> {t('networkAdd')}
                    </ButtonPrimary>
                  </div>
                </Field>
                <Field label={t('networkBulkCidr')}>
                  <textarea
                    rows={3}
                    placeholder={'192.168.1.0/24\n10.0.0.0/8'}
                    className="w-full px-3 py-2 rounded-lg border border-slate-200 bg-white text-xs font-mono focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                        e.preventDefault();
                        const lines = e.currentTarget.value.split('\n').map(s => s.trim()).filter(Boolean);
                        if (lines.length) { addMut.mutate(lines); e.currentTarget.value = ''; }
                      }
                    }}
                  />
                  <p className="text-[10px] text-slate-400 mt-1">{t('networkBulkHint')}</p>
                </Field>
            {addMut.isError && <ErrorAlert error={addMut.error} />}
            {removeMut.isError && <ErrorAlert error={removeMut.error} />}

            <ButtonGhost onClick={() => { setBulkText(data?.networks.join('\n') ?? ''); setReplaceOpen(true); }}>
              {t('networkReplaceAll')}
            </ButtonGhost>
          </div>

          <Modal open={replaceOpen} onClose={() => setReplaceOpen(false)} title={t('networkReplaceTitle')}>
            <div className="space-y-3">
              <Field label={t('networkReplaceDesc')}>
                <textarea
                  rows={8}
                  value={bulkText}
                  onChange={(e) => setBulkText(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-slate-200 bg-white text-sm font-mono focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                  placeholder={'192.168.1.0/24\n10.0.0.0/8'}
                />
              </Field>
                  <p className="text-[10px] text-rose-500">{t('networkReplaceWarning')}</p>
                  {replaceMut.isError && <ErrorAlert error={replaceMut.error} />}
                  <div className="flex justify-end gap-2">
                    <ButtonGhost onClick={() => setReplaceOpen(false)}>{tc('cancel')}</ButtonGhost>
                    <ButtonDanger onClick={() => replaceMut.mutate(bulkText.split('\n').map((s) => s.trim()).filter(Boolean))} disabled={replaceMut.isPending}>
                      {replaceMut.isPending ? tc('saving') : t('networkReplace')}
                    </ButtonDanger>
                  </div>
                </div>
              </Modal>

              {/* Confirm delete dialog */}
              <Modal open={!!deleteConfirm} onClose={() => setDeleteConfirm(null)} title={t('deleteConfirmTitle')}>
                <div className="space-y-3">
                  <p className="text-sm text-slate-700">
                    {t.rich('networkDeleteDesc', {
                      cidr: deleteConfirm ?? '',
                      code: (chunks) => <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs font-mono">{chunks}</code>,
                    })}
                  </p>
                  <p className="text-xs text-rose-600">
                    {t('networkDeleteWarning')}
                  </p>
                  <div className="flex justify-end gap-2 pt-2">
                    <ButtonGhost onClick={() => setDeleteConfirm(null)}>{tc('cancel')}</ButtonGhost>
                    <ButtonDanger onClick={() => { if (deleteConfirm) { removeMut.mutate(deleteConfirm); setDeleteConfirm(null); } }} disabled={removeMut.isPending}>
                      {removeMut.isPending ? t('networkDeleting') : tc('delete')}
                    </ButtonDanger>
                  </div>
                </div>
              </Modal>
            </Card>
          );
        }



