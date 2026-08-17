'use client';
import { useLocale, useTranslations } from 'next-intl';

import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FolderOpen, FileText, Upload, Download, Plus } from 'lucide-react';
import { listDocuments, uploadDocument, downloadDocument } from '@/lib/api/employees';
import type { EmployeeDocument } from '@/lib/api/types';
import { useSession } from '@/lib/auth/session';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, Select, ButtonPrimary, ButtonGhost,
  ErrorAlert, EmptyState, Modal, useFormatDate,
} from '@/components/shared-ui';

const DOC_TYPES = ['Hợp đồng', 'CMND/CCCD', 'Bằng cấp', 'Sổ bảo hiểm', 'Khác'];

export default function EmployeeDocumentsPage() {
  const t = useTranslations('employee');
  const locale = useLocale();
  const formatDate = useFormatDate();
  const { user } = useSession();
  const employeeId = user?.employee_id ?? null;
  const qc = useQueryClient();

  const { data: documents, isLoading, error } = useQuery<EmployeeDocument[]>({
    queryKey: ['employee-documents', employeeId],
    queryFn: () => listDocuments(employeeId!),
    enabled: Boolean(employeeId),
  });

  const [uploadOpen, setUploadOpen] = useState(false);
  const [docType, setDocType] = useState(DOC_TYPES[0]);
  const [docDesc, setDocDesc] = useState('');
  const [docFile, setDocFile] = useState<File | null>(null);

  useEffect(() => {
    if (!uploadOpen) {
      setDocType(DOC_TYPES[0]);
      setDocDesc('');
      setDocFile(null);
    }
  }, [uploadOpen]);

  const uploadMut = useMutation({
    mutationFn: () => {
      if (!docFile) throw new Error(t('pleaseSelectFile'));
      return uploadDocument(employeeId!, docFile, docType, docDesc || undefined);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employee-documents', employeeId] });
      setUploadOpen(false);
    },
  });

  const handleDownload = async (docId: string, name: string) => {
    try {
      const blob = await downloadDocument(docId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error(e);
    }
  };

  if (!employeeId) return null;

  return (
    <div className="space-y-6">
      <PageHeader
        icon={FolderOpen}
        title={t('documentsTitle')}
        subtitle={t('documentsDesc')}
        actions={<ButtonPrimary onClick={() => setUploadOpen(true)}><Plus className="w-4 h-4" /> {t('uploadDoc')}</ButtonPrimary>}
      />

      <Card>
        <SectionTitle icon={FolderOpen}>{t('documentList')}</SectionTitle>
        {error ? <ErrorAlert error={error} title={t('loadDocumentsError')} />
          : isLoading && !documents ? <p className="text-sm text-slate-400">{t('loading')}</p>
          : !documents?.length ? <EmptyState filtered={false} emptyData={t('noDocuments')} />
          : (
            <div className="space-y-2">
              {documents.map((doc) => (
                <div key={doc.id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg border border-slate-100">
                  <FileText className="w-4 h-4 text-slate-400 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-semibold text-slate-800 truncate">{doc.file_name}</p>
                    <p className="text-[10px] text-slate-400">{doc.document_type} · {formatDate(doc.uploaded_at)}</p>
                  </div>
                  <button onClick={() => handleDownload(doc.id, doc.file_name)} className="p-1.5 rounded-lg hover:bg-slate-200 text-slate-500 hover:text-indigo-600 transition-all" title={t('download')}>
                    <Download className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        <p className="text-[10px] text-slate-400 mt-3">{t('deleteByHR')}</p>
      </Card>

      <Modal open={uploadOpen} onClose={() => setUploadOpen(false)} title={t('uploadTitle')}>
        <div className="space-y-3">
          <Field label={t('documentType')}>
            <Select value={docType} onChange={(e) => setDocType(e.target.value)}>
              {DOC_TYPES.map((type) => <option key={type} value={type}>{t('doc_' + type)}</option>)}
            </Select>
          </Field>
          <Field label={t('descOptional')}>
            <TextInput value={docDesc} onChange={(e) => setDocDesc(e.target.value)} />
          </Field>
          <Field label={t('fileLabel')}>
            <input type="file" onChange={(e) => setDocFile(e.target.files?.[0] ?? null)} className="text-xs" />
          </Field>
          {uploadMut.isError && <ErrorAlert error={uploadMut.error} />}
          <div className="flex justify-end gap-2 pt-2">
            <ButtonGhost onClick={() => setUploadOpen(false)}>{t('cancel')}</ButtonGhost>
            <ButtonPrimary onClick={() => uploadMut.mutate()} disabled={uploadMut.isPending || !docFile}>
              <Upload className="w-4 h-4" /> {uploadMut.isPending ? t('uploading') : t('uploadAction')}
            </ButtonPrimary>
          </div>
        </div>
      </Modal>
    </div>
  );
}