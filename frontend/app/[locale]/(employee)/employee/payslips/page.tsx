'use client';
import { useLocale, useTranslations } from 'next-intl';

import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileSpreadsheet, Eye } from 'lucide-react';
import { fetchMyPayslips, fetchMyPayslip } from '@/lib/api/payslips';
import type { Payslip, PayslipListResponse } from '@/lib/api/payslips';
import {
  PageHeader, Card, SectionTitle, ButtonPrimary, ErrorAlert, EmptyState, Badge, Modal, formatVND, formatDateTime,
} from '@/components/shared-ui';

export default function EmployeePayslipsPage() {
 const t = useTranslations('employee');
 const locale = useLocale();

  const [yearFilter, setYearFilter] = useState<string>('');

  const { data, isLoading, error } = useQuery<PayslipListResponse>({
    queryKey: ['my-payslips'],
    queryFn: fetchMyPayslips,
  });

  const [viewId, setViewId] = useState<string | null>(null);
  const { data: viewed } = useQuery<Payslip>({
    queryKey: ['my-payslip', viewId],
    queryFn: () => fetchMyPayslip(viewId!),
    enabled: Boolean(viewId),
  });

  const toMonth = (p: string) => (p ? p.slice(0, 7) : '—');

  // BUG-12 defense-in-depth: BE `/api/payslips/me` đã filter `status='published'`
  // (payslip_repository.list_by_employee). FE filter lại một lần nữa để bảo
  // boundary Employee-chỉ-xem-published ngay cả nếu BE ever leak — KHÔNG bao
  // giờ render row draft/unpublished (và cũng không render label liên quan).
  const years = [...new Set((data?.payslips ?? []).map((p) => p.period_month?.slice(0, 4)).filter(Boolean))].sort().reverse();

  const publishedPayslips = (data?.payslips ?? []).filter(
    (p) => p.status === 'published' && (!yearFilter || p.period_month?.startsWith(yearFilter)),
  );

  return (
    <div className="space-y-6">
      <PageHeader
        icon={FileSpreadsheet}
        title={t('myPayslips')}
        subtitle={t('payslipDesc')}
      />

      <Card>
        <SectionTitle icon={FileSpreadsheet}>{t('payslipList')}</SectionTitle>
        {years.length > 1 && (
          <div className="mb-3 flex items-center gap-2">
            <span className="text-xs text-slate-500">{t('yearLabel')}</span>
            <select
              value={yearFilter}
              onChange={(e) => setYearFilter(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
            >
              <option value="">{t('all')}</option>
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
        )}
        {error ? <ErrorAlert error={error} title={t('loadPayslipError')} />
          : isLoading && !data ? <p className="text-sm text-slate-400">{t('loading')}</p>
          : !publishedPayslips.length ? <EmptyState filtered={false} emptyData={t('noPayslips')} />
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[10px] font-mono uppercase text-slate-400 border-b border-slate-100">
                    <th className="py-2 px-2">{t('periodColumn')}</th>
                    <th className="py-2 px-2">{t('grossColumn')}</th>
                    <th className="py-2 px-2">{t('netColumn')}</th>
                    <th className="py-2 px-2">{t('publishedColumn')}</th>
                    <th className="py-2 px-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {publishedPayslips.map((p) => (
                    <tr key={p.id} className="border-b border-slate-50 hover:bg-slate-50">
                      <td className="py-2.5 px-2 font-mono text-xs text-slate-600">{toMonth(p.period_month)}</td>
                      <td className="py-2.5 px-2 text-xs text-slate-600">{formatVND(p.gross_salary)}</td>
                      <td className="py-2.5 px-2 text-xs font-semibold text-slate-800">{formatVND(p.net_salary)}</td>
                      <td className="py-2.5 px-2"><Badge tone="emerald">{t('published')}</Badge></td>
                      <td className="py-2.5 px-2">
                        <button onClick={() => setViewId(p.id)} className="p-1.5 rounded-lg hover:bg-slate-200 text-slate-500 hover:text-indigo-600 transition-all" title={t('viewDetail')}>
                          <Eye className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      <Modal open={!!viewId} onClose={() => setViewId(null)} title={t('payslipDetail')}>
        {viewed && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Badge tone="emerald">{t('published')}</Badge>
              <span className="font-mono text-[10px] text-slate-400">{t('period')} {toMonth(viewed.period_month)} · {t('publishedAt')} {formatDateTime(viewed.published_at)}</span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Row label={t('grossSalary')} value={formatVND(viewed.gross_salary)} />
              <Row label={t('netSalary')} value={formatVND(viewed.net_salary)} />
              <Row label={t('deductions')} value={formatVND(viewed.deductions)} />
              <Row label={t('insurance')} value={formatVND(viewed.insurance_employee)} />
              <Row label={t('taxableIncome')} value={formatVND(viewed.taxable_income)} />
              <Row label={t('pitAmount')} value={formatVND(viewed.pit_amount)} />
                  {viewed.pdf_url && (
                    <div className="col-span-2 p-2 bg-slate-50 rounded-lg border border-slate-100">
                      <p className="text-[10px] font-mono text-slate-400 uppercase">{t('pdfLabel')}</p>
                      <a href={viewed.pdf_url} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold text-indigo-600 hover:underline break-all">
                        {t('viewPdf')}
                      </a>
                    </div>
                  )}
            </div>
            <div className="flex justify-end pt-2">
              <button onClick={() => window.print()} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition-all no-print">
                {t('printPayslip')}
              </button>
              <ButtonPrimary onClick={() => setViewId(null)}>{t('close')}</ButtonPrimary>
            </div>
          </div>
        )}
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