'use client';

import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/i18n/navigation';
import { UserPlus, ArrowLeft } from 'lucide-react';
import { createEmployee } from '@/lib/api/employees';
import { listDepartments } from '@/lib/api/departments';
import { listPositions } from '@/lib/api/positions';
import type { EmployeeCreateData, Department, Position } from '@/lib/api/types';
import {
  PageHeader, Card, SectionTitle, Field, TextInput, TextArea, Select,
  ButtonPrimary, ButtonGhost, ErrorAlert,
} from '@/components/shared-ui';

const empty: EmployeeCreateData = {
  full_name: '',
  email: '',
  phone: '',
  date_of_birth: '',
  gender: 'male',
  address: '',
  department_id: undefined,
  position_id: undefined,
  start_date: '',
  id_number: '',
  tax_code: '',
  contract_type: '',
};

export default function NewEmployeePage() {
  const t = useTranslations('employees');
  const router = useRouter();
  const qc = useQueryClient();
  const [form, setForm] = useState<EmployeeCreateData>(empty);

  const { data: departments } = useQuery<Department[]>({ queryKey: ['departments-list'], queryFn: listDepartments, staleTime: 60_000 });
  const { data: positions } = useQuery<Position[]>({ queryKey: ['positions-list'], queryFn: listPositions, staleTime: 60_000 });

  const createMut = useMutation({
    mutationFn: (data: EmployeeCreateData) => createEmployee(data),
    onSuccess: (emp) => {
      qc.invalidateQueries({ queryKey: ['employees-list'] });
      router.push(`/employees/${emp.id}`);
    },
  });

  const set = (patch: Partial<EmployeeCreateData>) => setForm({ ...form, ...patch });
  const submit = () => {
    const payload: EmployeeCreateData = {
      ...form,
      department_id: form.department_id || undefined,
      position_id: form.position_id || undefined,
      date_of_birth: form.date_of_birth || undefined,
      start_date: form.start_date || undefined,
    };
    createMut.mutate(payload);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        icon={UserPlus}
        title={t('createEmployeeTitle')}
        subtitle={t('createEmployeeSubtitle')}
        actions={
          <ButtonGhost onClick={() => router.push('/employees')}>
            <ArrowLeft className="w-4 h-4" /> {t('backToList')}
          </ButtonGhost>
        }
      />
      <Card>
        <SectionTitle icon={UserPlus}>{t('basicInfo')}</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label={t('fullNameRequired')}>
            <TextInput value={form.full_name} onChange={(e) => set({ full_name: e.target.value })} />
          </Field>
          <Field label={t('emailRequired')}>
            <TextInput type="email" value={form.email} onChange={(e) => set({ email: e.target.value })} />
          </Field>
          <Field label={t('phoneLabel')}>
            <TextInput value={form.phone} onChange={(e) => set({ phone: e.target.value })} />
          </Field>
          <Field label={t('dobLabel')}>
            <TextInput type="date" value={form.date_of_birth} onChange={(e) => set({ date_of_birth: e.target.value })} />
          </Field>
          <Field label={t('genderLabel')}>
            <Select value={form.gender} onChange={(e) => set({ gender: e.target.value })}>
              <option value="male">{t('genderMale')}</option>
              <option value="female">{t('genderFemale')}</option>
              <option value="other">{t('genderOther')}</option>
            </Select>
          </Field>
          <Field label={t('contractTypeLabel')}>
            <TextInput value={form.contract_type} onChange={(e) => set({ contract_type: e.target.value })} placeholder={t('contractTypeLabel')} />
          </Field>
          <Field label={t('departmentLabel')}>
            <Select value={form.department_id ?? ''} onChange={(e) => set({ department_id: e.target.value || undefined })}>
              <option value="">—</option>
              {departments?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
          </Field>
          <Field label={t('positionLabel')}>
            <Select value={form.position_id ?? ''} onChange={(e) => set({ position_id: e.target.value || undefined })}>
              <option value="">—</option>
              {positions?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          </Field>
          <Field label={t('startDateLabel')}>
            <TextInput type="date" value={form.start_date} onChange={(e) => set({ start_date: e.target.value })} />
          </Field>
          <Field label={t('idNumberLabel')}>
            <TextInput value={form.id_number} onChange={(e) => set({ id_number: e.target.value })} />
          </Field>
          <Field label={t('taxCodeLabel')}>
            <TextInput value={form.tax_code} onChange={(e) => set({ tax_code: e.target.value })} />
          </Field>
          <Field label={t('addressLabel')}>
            <TextArea rows={2} value={form.address} onChange={(e) => set({ address: e.target.value })} />
          </Field>
        </div>

        {createMut.isError && <div className="mt-4"><ErrorAlert error={createMut.error} /></div>}

        <div className="flex justify-end gap-2 mt-5">
          <ButtonGhost onClick={() => router.push('/employees')}>{t('cancel')}</ButtonGhost>
          <ButtonPrimary onClick={submit} disabled={createMut.isPending || !form.full_name || !form.email}>
            {createMut.isPending ? t('saving') : t('addEmployee')}
          </ButtonPrimary>
        </div>
      </Card>
    </div>
  );
}
