'use client';

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import {
  Users, FileText, KeyRound, Save, Upload, Download, Trash2, Plus, ArrowLeft,
} from 'lucide-react';
import {
  getEmployee, updateEmployee, deleteEmployee, listDocuments, uploadDocument, downloadDocument, deleteDocument,
  getEmployeeAccountStatus, createEmployeeAccount, deleteEmployeeAccount,
} from '@/lib/api/employees';
import { listDepartments } from '@/lib/api/departments';
import { listPositions } from '@/lib/api/positions';
import type { Employee, EmployeeDocument, Department, Position, EmployeeUpdateData } from '@/lib/api/types';
import type { EmployeeAccountStatusResponse, EmployeeAccountCreateResponse } from '@/lib/api/auth';
import { isUserRole } from '@/lib/auth/roles';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, TextArea, Select,
  ButtonPrimary, ButtonGhost, ButtonDanger, Badge, ErrorAlert, Modal, useFormatDateTime, useFormatDate,
} from '@/components/shared-ui';

const DOC_TYPES_KEY = ['docContract', 'docIdCard', 'docDegree', 'docInsurance', 'docOther'] as const;

export default function EmployeeDetailPage() {
  const formatDateTime = useFormatDateTime();
  const formatDate = useFormatDate();
  const t = useTranslations('employees');
  const tc = useTranslations('common');
  const tr = useTranslations('roles');
  const params = useParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();
  const qc = useQueryClient();

  const DOC_TYPES = DOC_TYPES_KEY.map((k) => t(k));

  const { data: employee, isLoading, error } = useQuery<Employee>({
    queryKey: ['employee', id],
    queryFn: () => getEmployee(id),
    enabled: Boolean(id),
  });
  const { data: departments } = useQuery<Department[]>({ queryKey: ['departments-list'], queryFn: listDepartments, staleTime: 60_000 });
  const { data: positions } = useQuery<Position[]>({ queryKey: ['positions-list'], queryFn: listPositions, staleTime: 60_000 });

  const { data: documents } = useQuery<EmployeeDocument[]>({
    queryKey: ['employee-documents', id],
    queryFn: () => listDocuments(id),
    enabled: Boolean(id),
  });

  const { data: account } = useQuery<EmployeeAccountStatusResponse>({
    queryKey: ['employee-account', id],
    queryFn: () => getEmployeeAccountStatus(id),
    enabled: Boolean(id),
  });

  // ---- Edit form ----
  const [form, setForm] = useState<Partial<Employee> | null>(null);
  const editActive = form !== null;
  const startEdit = () => setForm(employee ? { ...employee } : {});
  const submitEdit = () => {
    if (!form) return;
    const { id:_id, is_active:_a, employee_code:_c, candidate_id:_cid, created_at:_ca, updated_at:_ua, ...rest } = form;
    const u = Object.fromEntries(
      Object.entries(rest).map(([k, v]) => [k, v ?? undefined]),
    ) as EmployeeUpdateData;
    updateMut.mutate(u);
  };
  const cancelEdit = () => setForm(null);

  const updateMut = useMutation({
    mutationFn: (data: EmployeeUpdateData) => updateEmployee(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employee', id] });
      qc.invalidateQueries({ queryKey: ['employees-list'] });
      setForm(null);
    },
  });

  // ---- Documents ----
  const [uploadOpen, setUploadOpen] = useState(false);
  const [docType, setDocType] = useState(DOC_TYPES[0]);
  const [docDesc, setDocDesc] = useState('');
  const [docFile, setDocFile] = useState<File | null>(null);

  const uploadMut = useMutation({
    mutationFn: () => {
      if (!docFile) throw new Error(t('selectFile'));
      return uploadDocument(id, docFile, docType, docDesc || undefined);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employee-documents', id] });
      setUploadOpen(false);
      setDocFile(null);
      setDocDesc('');
      setDocType(DOC_TYPES[0]);
    },
  });

  const delDocMut = useMutation({
    mutationFn: (documentId: string) => deleteDocument(documentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['employee-documents', id] }),
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

  // ---- Account ----
  const [createdPwd, setCreatedPwd] = useState<string | null>(null);
  const createAccountMut = useMutation<EmployeeAccountCreateResponse, unknown, void>({
    mutationFn: () => createEmployeeAccount(id),
    onSuccess: (data) => {
      setCreatedPwd(data.temporary_password);
      qc.invalidateQueries({ queryKey: ['employee-account', id] });
    },
  });

  // ---- Delete Employee ----
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const deleteEmpMut = useMutation({
    mutationFn: () => deleteEmployee(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employees-list'] });
      router.push('/employees');
    },
  });

  // ---- Delete Account ----
  const deleteAccountMut = useMutation({
    mutationFn: () => deleteEmployeeAccount(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employee-account', id] });
    },
  });

  if (isLoading) return <div className="text-sm text-slate-400">{t('loadingProfile')}</div>;
  if (error) return <ErrorAlert error={error} title={t('loadProfileError')} />;
  if (!employee) return null;

  const editing = editActive ? form! : employee;

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Users}
        title={employee.full_name}
        subtitle={`${employee.employee_code} · ${employee.email}`}
        actions={
          <div className="flex gap-2">
            <ButtonDanger onClick={() => setDeleteConfirm(true)} disabled={deleteEmpMut.isPending}>
              <Trash2 className="w-4 h-4" /> {t('delete')}
            </ButtonDanger>
            <ButtonGhost onClick={() => router.push('/employees')}>
              <ArrowLeft className="w-4 h-4" /> {t('backToList')}
            </ButtonGhost>
            {!editActive ? (
              <ButtonPrimary onClick={startEdit}><Save className="w-4 h-4" /> {t('editTitle')}</ButtonPrimary>
            ) : (
              <>
                <ButtonGhost onClick={cancelEdit}>{tc('cancel')}</ButtonGhost>
                <ButtonPrimary onClick={submitEdit} disabled={updateMut.isPending}>
                  {updateMut.isPending ? t('saving') : t('saveChanges')}
                </ButtonPrimary>
              </>
            )}
          </div>
        }
      />

      <Card>
        <SectionTitle icon={Users}>{t('profileInfo')}</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label={t('fullNameLabel')}>
            <TextInput value={editing.full_name ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, full_name: e.target.value })} />
          </Field>
          <Field label={t('emailLabel')}>
            <TextInput value={editing.email ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, email: e.target.value })} />
          </Field>
          <Field label={t('phoneLabel')}>
            <TextInput value={editing.phone ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, phone: e.target.value })} />
          </Field>
          <Field label={t('dobLabel')}>
            <TextInput type="date" value={editing.date_of_birth ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, date_of_birth: e.target.value || undefined })} />
          </Field>
          <Field label={t('genderLabel')}>
            <Select value={editing.gender ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, gender: e.target.value || undefined })}>
              <option value="">—</option>
              <option value="male">{t('genderMale')}</option>
              <option value="female">{t('genderFemale')}</option>
              <option value="other">{t('genderOther')}</option>
            </Select>
          </Field>
          <Field label={t('departmentLabel')}>
            <Select value={editing.department_id ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, department_id: e.target.value || undefined })}>
              <option value="">—</option>
              {departments?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
            {(!departments || departments.length === 0) && editActive && (
              <p className="text-[10px] text-amber-600 mt-1">{t('noDepartment')}</p>
            )}
          </Field>
          <Field label={t('positionLabel')}>
            <Select value={editing.position_id ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, position_id: e.target.value || undefined })}>
              <option value="">—</option>
              {positions?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
            {(!positions || positions.length === 0) && editActive && (
              <p className="text-[10px] text-amber-600 mt-1">{t('noPosition')}</p>
            )}
          </Field>
          <Field label={t('startDateLabel')}>
            <TextInput type="date" value={editing.start_date ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, start_date: e.target.value })} />
          </Field>
          <Field label={t('idNumberLabel')}>
            <TextInput value={editing.id_number ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, id_number: e.target.value })} />
          </Field>
          <Field label={t('taxCodeLabel')}>
            <TextInput value={editing.tax_code ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, tax_code: e.target.value })} />
          </Field>
          <Field label={t('contractTypeLabel')}>
            <Select value={editing.contract_type ?? ''} disabled={!editActive}
              onChange={(e) => setForm({ ...form!, contract_type: e.target.value || undefined })}>
              <option value="">—</option>
              <option value="full_time">{t('contractFullTime')}</option>
              <option value="indefinite">{t('contractIndefinite')}</option>
              <option value="probation">{t('contractProbation')}</option>
              <option value="contractor">{t('contractContractor')}</option>
              <option value="intern">{t('contractIntern')}</option>
            </Select>
          </Field>
          <Field label={t('addressLabel')}>
            <TextArea value={editing.address ?? ''} disabled={!editActive} rows={2}
              onChange={(e) => setForm({ ...form!, address: e.target.value })} />
          </Field>
        </div>
        <div className="flex items-center gap-2 mt-4">
          <Badge tone={employee.is_active ? 'emerald' : 'rose'}>
            {employee.is_active ? t('active') : t('inactive')}
          </Badge>
          <span className="text-[10px] font-mono text-slate-400">{t('editTitle')} {formatDateTime(employee.updated_at)}</span>
        </div>
      </Card>

      {/* Documents */}
      <Card>
        <SectionTitle icon={FileText}>{t('documents')}</SectionTitle>
        <div className="flex justify-between items-center mb-3">
          <p className="text-xs text-slate-500">{t('documentsDesc')}</p>
          <ButtonPrimary onClick={() => setUploadOpen(true)}><Plus className="w-4 h-4" /> {t('uploadDoc')}</ButtonPrimary>
        </div>
        {!documents || documents.length === 0 ? (
          <p className="text-sm text-slate-400 py-4 text-center">{t('noDocuments')}</p>
        ) : (
          <div className="space-y-2">
            {documents.map((doc) => (
              <div key={doc.id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg border border-slate-100">
                <FileText className="w-4 h-4 text-slate-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-slate-800 truncate">{doc.file_name}</p>
                  <p className="text-[10px] text-slate-400">{doc.document_type} · {formatDate(doc.uploaded_at)}</p>
                </div>
                <button onClick={() => handleDownload(doc.id, doc.file_name)} className="p-1.5 rounded-lg hover:bg-slate-200 text-slate-500 hover:text-indigo-600 transition-all" title={t('uploadDoc')}>
                  <Download className="w-4 h-4" />
                </button>
                <button onClick={() => delDocMut.mutate(doc.id)} disabled={delDocMut.isPending} className="p-1.5 rounded-lg hover:bg-rose-100 text-slate-400 hover:text-rose-500 transition-all" title={tc('delete')}>
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}

        <Modal open={uploadOpen} onClose={() => setUploadOpen(false)} title={t('uploadDoc')}>
          <div className="space-y-3">
            <Field label={t('documentType')}>
              <Select value={docType} onChange={(e) => setDocType(e.target.value)}>
                {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            </Field>
            <Field label={t('documentDesc')}>
              <TextInput value={docDesc} onChange={(e) => setDocDesc(e.target.value)} />
            </Field>
            <Field label={t('documentFile')}>
              <input type="file" onChange={(e) => setDocFile(e.target.files?.[0] ?? null)} className="text-xs" />
            </Field>
            {uploadMut.isError && <ErrorAlert error={uploadMut.error} />}
            <div className="flex justify-end gap-2 pt-2">
              <ButtonGhost onClick={() => setUploadOpen(false)}>{tc('cancel')}</ButtonGhost>
              <ButtonPrimary onClick={() => uploadMut.mutate()} disabled={uploadMut.isPending || !docFile}>
                <Upload className="w-4 h-4" /> {uploadMut.isPending ? t('uploading') : t('uploadDocAction')}
              </ButtonPrimary>
            </div>
          </div>
        </Modal>
      </Card>

      {/* Employee Account */}
      <Card>
        <SectionTitle icon={KeyRound}>{t('employeeAccount')}</SectionTitle>
        <p className="text-xs text-slate-500 mb-3">
          {t.rich('accountDesc', { strong: (c) => <strong>{c}</strong> })}
        </p>
        {account?.exists ? (
          <div className="p-3 bg-slate-50 rounded-lg border border-slate-100 text-xs space-y-1">
            <p><span className="text-slate-500">{t('accountEmail')}:</span> <span className="font-semibold text-slate-800">{account.email}</span></p>
            <p><span className="text-slate-500">{t('accountRole')}:</span> <span className="font-mono">{isUserRole(account.role) ? tr(account.role) : account.role}</span></p>
            <p><span className="text-slate-500">{t('accountMustChangePwd')}:</span> {account.must_change_password ? t('accountYes') : t('accountNo')}</p>
            <div className="pt-2">
              <ButtonDanger onClick={() => deleteAccountMut.mutate()} disabled={deleteAccountMut.isPending}>
                <Trash2 className="w-4 h-4" /> {deleteAccountMut.isPending ? t('accountDeleting') : t('accountDelete')}
              </ButtonDanger>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {employee.is_active ? (
              <>
                <p className="text-xs text-slate-500">{t('accountNoAccount')}</p>
                <ButtonPrimary onClick={() => createAccountMut.mutate()} disabled={createAccountMut.isPending}>
                  <KeyRound className="w-4 h-4" /> {createAccountMut.isPending ? t('accountCreating') : t('accountCreate')}
                </ButtonPrimary>
              </>
            ) : (
              <p className="text-xs text-rose-600">{t('accountInactive')}</p>
            )}
          </div>
        )}
        {createAccountMut.isError && <div className="mt-2"><ErrorAlert error={createAccountMut.error} /></div>}
        <Modal open={!!createdPwd} onClose={() => setCreatedPwd(null)} title={t('accountCreatedTitle')}>
          <div className="space-y-2">
            <p className="text-xs text-slate-500">{t('accountCreatedDesc')}</p>
            <div className="p-3 bg-slate-900 rounded-lg font-mono text-sm text-emerald-300 break-all">{createdPwd ?? ''}</div>
            <p className="text-[10px] text-slate-400">{t('accountCreatedWarning')}</p>
            <div className="flex justify-end pt-2">
              <ButtonPrimary onClick={() => setCreatedPwd(null)}>{t('accountCreatedAck')}</ButtonPrimary>
            </div>
          </div>
        </Modal>

        {/* Delete Employee Confirmation */}
        <Modal open={deleteConfirm} onClose={() => setDeleteConfirm(false)} title={t('deleteTitle')}>
          <div className="space-y-3">
            <p className="text-sm text-slate-600">
              {t.rich('deleteConfirm', { name: employee.full_name, code: employee.employee_code, strong: (chunks) => <strong>{chunks}</strong> })}
            </p>
            <p className="text-xs text-rose-600">
              {t('deleteWarning')}
            </p>
            {deleteEmpMut.isError && <ErrorAlert error={deleteEmpMut.error} />}
            <div className="flex justify-end gap-2 pt-2">
              <ButtonGhost onClick={() => setDeleteConfirm(false)}>{tc('cancel')}</ButtonGhost>
              <ButtonDanger onClick={() => deleteEmpMut.mutate()} disabled={deleteEmpMut.isPending}>
                {deleteEmpMut.isPending ? t('deleting') : t('delete')}
              </ButtonDanger>
            </div>
          </div>
        </Modal>
      </Card>
    </div>
  );
}
