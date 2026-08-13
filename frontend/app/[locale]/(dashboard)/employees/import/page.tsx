'use client';

import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/i18n/navigation';
import { Upload, ArrowLeft, CheckCircle, XCircle, Download } from 'lucide-react';
import { importEmployees } from '@/lib/api/employees';
import type { ImportResult } from '@/lib/api/types';
import {
  PageHeader, Card, SectionTitle, ButtonPrimary, ButtonGhost, ErrorAlert,
} from '@/components/shared-ui';

export default function ImportEmployeesPage() {
  const t = useTranslations('employees');
  const router = useRouter();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  const importMut = useMutation({
    mutationFn: () => {
      if (!file) throw new Error(t('selectFile'));
      return importEmployees(file);
    },
    onSuccess: (r) => {
      setResult(r);
      qc.invalidateQueries({ queryKey: ['employees-list'] });
      qc.invalidateQueries({ queryKey: ['departments-list'] });
      qc.invalidateQueries({ queryKey: ['positions-list'] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Upload}
        title={t('importTitle')}
        subtitle={t('importSubtitle')}
        actions={
          <ButtonGhost onClick={() => router.push('/employees')}>
            <ArrowLeft className="w-4 h-4" /> {t('backToList')}
          </ButtonGhost>
        }
      />

      <Card>
        <SectionTitle icon={Upload}>{t('selectFile')}</SectionTitle>
        <input
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-xs"
        />
        <p className="text-[10px] text-slate-400 mt-2">{t('fileHint')}</p>

        <div className="mt-3">
          <ButtonGhost onClick={() => {
            const headers = 'full_name,email,phone,date_of_birth,gender,address,department,position,start_date,id_number,tax_code,contract_type';
            const sample = 'Nguyễn Văn A,a@example.com,0912345678,1990-01-01,male,123 Đường ABC Hà Nội,Kỹ thuật,Lập trình viên,2024-01-01,001234567890,1234567890,full_time';
            const bom = '\uFEFF';
            const blob = new Blob([bom + headers + '\n' + sample], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'employee_import_template.csv';
            a.click();
            URL.revokeObjectURL(url);
          }}>
            <Download className="w-4 h-4" /> {t('downloadTemplate')}
          </ButtonGhost>
        </div>

        {importMut.isError && <div className="mt-3"><ErrorAlert error={importMut.error} /></div>}

        <div className="flex justify-end mt-4">
          <ButtonPrimary onClick={() => importMut.mutate()} disabled={importMut.isPending || !file}>
            <Upload className="w-4 h-4" /> {importMut.isPending ? t('importing') : t('import')}
          </ButtonPrimary>
        </div>
      </Card>

      {result && (
        <Card>
          <SectionTitle icon={CheckCircle}>{t('importResults')}</SectionTitle>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-100">
              <p className="text-2xl font-bold text-slate-900">{result.total_rows}</p>
              <p className="text-[10px] text-slate-400">{t('totalRows')}</p>
            </div>
            <div className="p-3 bg-emerald-50 rounded-xl border border-emerald-100">
              <p className="text-2xl font-bold text-emerald-700">{result.success_count}</p>
              <p className="text-[10px] text-emerald-600">{t('successCount')}</p>
            </div>
            <div className="p-3 bg-rose-50 rounded-xl border border-rose-100">
              <p className="text-2xl font-bold text-rose-700">{result.error_count}</p>
              <p className="text-[10px] text-rose-600">{t('errorCount')}</p>
            </div>
            <div className="p-3 bg-indigo-50 rounded-xl border border-indigo-100">
              <p className="text-2xl font-bold text-indigo-700">
                {(result.departments_created ?? 0) + (result.positions_created ?? 0)}
              </p>
              <p className="text-[10px] text-indigo-600">{t('newDeptPos')}</p>
            </div>
          </div>

          {result.errors?.length > 0 && (
            <div className="mt-4">
              <div className="flex items-center gap-2 mb-2 text-rose-600">
                <XCircle className="w-4 h-4" />
                <span className="text-xs font-semibold">{t('errorDetails')}</span>
              </div>
              <div className="max-h-48 overflow-y-auto space-y-1">
                {result.errors.map((e, i) => (
                  <div key={i} className="text-[11px] font-mono text-slate-600 p-2 bg-slate-50 rounded">
                    {t('rowError', { row: e.row })}: {e.message}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex justify-end mt-4">
            <ButtonGhost onClick={() => router.push('/employees')}>{t('backToList')}</ButtonGhost>
          </div>
        </Card>
      )}
    </div>
  );
}
