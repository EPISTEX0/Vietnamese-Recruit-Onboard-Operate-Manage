'use client';
import { useTranslations } from 'next-intl';

import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { User, Save } from 'lucide-react';
import { getEmployee, updateEmployee } from '@/lib/api/employees';
import { listDepartments } from '@/lib/api/departments';
import { listPositions } from '@/lib/api/positions';
import type { Employee, Department, Position } from '@/lib/api/types';
import { useSession } from '@/lib/auth/session';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, TextArea, Select,
  ButtonPrimary, ErrorAlert, Badge, useFormatDateTime,
} from '@/components/shared-ui';

export default function EmployeeProfilePage() {
  const t = useTranslations('employee');
  const formatDateTime = useFormatDateTime();
  const { user } = useSession();
  const employeeId = user?.employee_id ?? null;
  const qc = useQueryClient();

  const { data: employee, isLoading, error } = useQuery<Employee>({
    queryKey: ['employee', employeeId],
    queryFn: () => getEmployee(employeeId!),
    enabled: Boolean(employeeId),
  });
  const { data: departments } = useQuery<Department[]>({ queryKey: ['departments-list'], queryFn: listDepartments, staleTime: 60_000 });
  const { data: positions } = useQuery<Position[]>({ queryKey: ['positions-list'], queryFn: listPositions, staleTime: 60_000 });

  const [phone, setPhone] = useState('');
  const [address, setAddress] = useState('');
  const [editing, setEditing] = useState(false);

  // Populate editable fields once the profile arrives.
  useEffect(() => {
    if (employee) {
      setPhone(employee.phone ?? '');
      setAddress(employee.address ?? '');
    }
  }, [employee]);

  const saveMut = useMutation({
    mutationFn: () => updateEmployee(employeeId!, { phone, address }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employee', employeeId] });
      setEditing(false);
    },
  });

  if (!employeeId) {
    return (
      <div className="space-y-6">
        <PageHeader icon={User} title={t('profileTitle')} subtitle={t('profileNoAccount')} />
        <Card><SectionTitle icon={User}>{t('profileInfo')}</SectionTitle>
          <p className="text-xs text-rose-600">{t('profileNoEmployee')}</p>
        </Card>
      </div>
    );
  }

  const deptName = (id: string | null) => departments?.find((d) => d.id === id)?.name ?? '—';
  const posName = (id: string | null) => positions?.find((p) => p.id === id)?.name ?? '—';

  return (
    <div className="space-y-6">
      <PageHeader
        icon={User}
        title={t('profileTitle')}
        subtitle={t('profileDesc')}
        actions={employee && !editing ? (
          <ButtonPrimary onClick={() => setEditing(true)}><Save className="w-4 h-4" /> {t('updateContact')}</ButtonPrimary>
        ) : null}
      />

      {isLoading ? <p className="text-sm text-slate-400">{t('loadingProfile')}</p>
        : error ? <ErrorAlert error={error} title={t('loadProfileError')} />
        : employee && (
          <Card>
            <SectionTitle icon={User}>{t('profileInfo')}</SectionTitle>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label={t('employeeCode')}><TextInput value={employee.employee_code} disabled /></Field>
              <Field label={t('fullName')}><TextInput value={employee.full_name} disabled /></Field>
              <Field label={t('email')}><TextInput value={employee.email} disabled /></Field>
              <Field label={t('status')}>
                <div className="pt-1"><Badge tone={employee.is_active ? 'emerald' : 'rose'}>{employee.is_active ? t('active') : t('inactive')}</Badge></div>
              </Field>
              <Field label={t('department')}><TextInput value={deptName(employee.department_id)} disabled /></Field>
              <Field label={t('position')}><TextInput value={posName(employee.position_id)} disabled /></Field>
              <Field label={t('startDate')}><TextInput value={employee.start_date ?? '—'} disabled /></Field>
              <Field label={t('contractType')}><TextInput value={employee.contract_type ?? '—'} disabled /></Field>

              <Field label={t('phoneEditable')}>
                <TextInput value={phone} disabled={!editing} onChange={(e) => setPhone(e.target.value)} />
              </Field>
              <Field label={t('addressEditable')}>
                <TextArea rows={2} value={address} disabled={!editing} onChange={(e) => setAddress(e.target.value)} />
              </Field>
            </div>

            <div className="flex items-center justify-between pt-4 mt-4 border-t border-slate-100">
              <span className="text-[10px] font-mono text-slate-400">{t('updatedAt', { time: formatDateTime(employee.updated_at) })}</span>
              {editing && (
                <div className="flex gap-2">
                  <button onClick={() => { setEditing(false); }} className="px-3 py-2 rounded-lg text-xs font-semibold bg-white border border-slate-200 hover:bg-slate-50">{t('cancel')}</button>
                  <ButtonPrimary onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
                    {saveMut.isPending ? t('saving') : t('saveChanges')}
                  </ButtonPrimary>
                </div>
              )}
            </div>
            {saveMut.isError && <div className="mt-3"><ErrorAlert error={saveMut.error} /></div>}
          </Card>
        )}
    </div>
  );
}