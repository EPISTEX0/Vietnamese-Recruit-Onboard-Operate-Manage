'use client';

import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from '@/i18n/navigation';
import { useTranslations } from 'next-intl';
import { Users, Plus, Upload, Search, ChevronLeft, ChevronRight } from 'lucide-react';
import { listEmployees } from '@/lib/api/employees';
import { listDepartments } from '@/lib/api/departments';
import { listPositions } from '@/lib/api/positions';
import type { Employee, EmployeeListResponse, Department, Position } from '@/lib/api/types';
import {
  PageHeader, Card, LoadingRows, EmptyState, ErrorAlert, Badge,
  ButtonPrimary, ButtonGhost, Field, TextInput, Select,
} from '@/components/shared-ui';

export default function EmployeesListPage() {
  const t = useTranslations('employees');
  const router = useRouter();

  const [search, setSearch] = useState('');
  const [departmentId, setDepartmentId] = useState('');
  const [positionId, setPositionId] = useState('');
  const [activeFilter, setActiveFilter] = useState<'active' | 'inactive' | 'all'>('active');
  const [page, setPage] = useState(1);
  const [submitted, setSubmitted] = useState({ search, departmentId, positionId, activeFilter, page });

  const { data, isLoading, error } = useQuery<EmployeeListResponse>({
    queryKey: ['employees-list', submitted],
    queryFn: () =>
      listEmployees({
        page: submitted.page,
        page_size: 20,
        search: submitted.search || undefined,
        department_id: submitted.departmentId || undefined,
        position_id: submitted.positionId || undefined,
        is_active:
          submitted.activeFilter === 'active'
            ? true
            : submitted.activeFilter === 'inactive'
              ? false
              : undefined,
      }),
    placeholderData: (prev) => prev,
  });

  const { data: departments } = useQuery<Department[]>({
    queryKey: ['departments-list'],
    queryFn: listDepartments,
    staleTime: 60 * 1000,
  });
  const { data: positions } = useQuery<Position[]>({
    queryKey: ['positions-list'],
    queryFn: listPositions,
    staleTime: 60 * 1000,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const hasFilters = Boolean(submitted.search || submitted.departmentId || submitted.positionId || submitted.activeFilter !== 'active');

  const applyFilters = () => {
    setSubmitted({ search, departmentId, positionId, activeFilter, page: 1 });
    setPage(1);
  };

  const gotoPage = (p: number) => {
    const np = Math.min(Math.max(1, p), totalPages);
    setPage(np);
    setSubmitted({ search, departmentId, positionId, activeFilter, page: np });
  };

  const deptName = (id: string | null) => departments?.find((d) => d.id === id)?.name ?? '—';
  const posName = (id: string | null) => positions?.find((p) => p.id === id)?.name ?? '—';

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Users}
        title={t('title')}
        subtitle={t('subtitle')}
        actions={
          <div className="flex gap-2">
            <ButtonGhost onClick={() => router.push('/employees/import')}>
              <Upload className="w-4 h-4" /> {t('import')}
            </ButtonGhost>
            <ButtonPrimary onClick={() => router.push('/employees/new')}>
              <Plus className="w-4 h-4" /> {t('addEmployee')}
            </ButtonPrimary>
          </div>
        }
      />

      {/* Filters */}
      <Card>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
          <Field label={t('searchPlaceholder')}>
            <TextInput
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyFilters()}
              placeholder={t('searchPlaceholder')}
            />
          </Field>
          <Field label={t('department')}>
            <Select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
              <option value="">{t('all')}</option>
              {departments?.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </Field>
          <Field label={t('position')}>
            <Select value={positionId} onChange={(e) => setPositionId(e.target.value)}>
              <option value="">{t('all')}</option>
              {positions?.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
          </Field>
          <Field label={t('statusFilter')}>
            <Select value={activeFilter} onChange={(e) => setActiveFilter(e.target.value as 'active' | 'inactive' | 'all')}>
              <option value="active">{t('active')}</option>
              <option value="inactive">{t('inactive')}</option>
              <option value="all">{t('all')}</option>
            </Select>
          </Field>
          <ButtonPrimary onClick={applyFilters} className="h-[38px]">
            <Search className="w-4 h-4" /> {t('filter')}
          </ButtonPrimary>
        </div>
      </Card>

      {/* Table */}
      <Card>
        {error ? (
          <ErrorAlert error={error} title={t('loadError')} />
        ) : isLoading && !data ? (
          <LoadingRows count={6} />
        ) : !data?.items?.length ? (
          <EmptyState filtered={hasFilters} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-mono uppercase text-slate-400 border-b border-slate-100">
                  <th className="py-2 px-2 font-semibold">{t('employeeCode')}</th>
                  <th className="py-2 px-2 font-semibold">{t('fullName')}</th>
                  <th className="py-2 px-2 font-semibold">{t('email')}</th>
                  <th className="py-2 px-2 font-semibold">{t('department')}</th>
                  <th className="py-2 px-2 font-semibold">{t('position')}</th>
                  <th className="py-2 px-2 font-semibold">{t('statusFilter')}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((emp: Employee) => (
                  <tr
                    key={emp.id}
                    onClick={() => router.push(`/employees/${emp.id}`)}
                    className="border-b border-slate-50 hover:bg-slate-50 cursor-pointer transition-colors"
                  >
                    <td className="py-2.5 px-2 font-mono text-xs text-slate-500">{emp.employee_code}</td>
                    <td className="py-2.5 px-2 font-semibold text-slate-800">{emp.full_name}</td>
                    <td className="py-2.5 px-2 text-slate-600">{emp.email}</td>
                    <td className="py-2.5 px-2 text-slate-600">{deptName(emp.department_id)}</td>
                    <td className="py-2.5 px-2 text-slate-600">{posName(emp.position_id)}</td>
                    <td className="py-2.5 px-2">
                      <Badge tone={emp.is_active ? 'emerald' : 'rose'}>
                        {emp.is_active ? t('active') : t('inactive')}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {data && data.total > data.page_size && (
          <div className="flex items-center justify-between pt-4 border-t border-slate-100 mt-4">
            <span className="text-xs text-slate-500">
              {t('employeesCount', { count: data.total })} · {t('pageOf', { page, total: totalPages })}
            </span>
            <div className="flex items-center gap-2">
              <button onClick={() => gotoPage(page - 1)} disabled={page <= 1} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button onClick={() => gotoPage(page + 1)} disabled={page >= totalPages} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all">
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
