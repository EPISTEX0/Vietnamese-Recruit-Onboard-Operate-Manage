'use client';

/** Users & roles — the account roster, plus the form that provisions staff. */

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { Users, Loader2, Plus, Check, X, AlertCircle } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type { AdminUser } from '@/lib/api/admin';
import { useSession } from '@/lib/auth/session';
import { STAFF_ROLES, USER_ROLES, type StaffRole, type UserRole } from '@/lib/auth/roles';
import { staffAccountCreateSchema } from '@/lib/api/admin-schemas';
import { PageHeader } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function UsersRolesPage() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const tr = useTranslations('roles');
  const { user: currentUser } = useSession();
  // Same key *and* `staleTime` as the roster read on `/settings`. `staleTime` is
  // per observer, not per key, so without the second half this one inherits the
  // app-wide 5 minutes (`lib/query-client.ts`) while the homepage's says 30
  // seconds — one cache entry, two opinions about when it is stale, and the
  // console refetching on an order-of-navigation basis nothing announces (#310).
  // 30 seconds is the console's number, set on every other shared key here; the
  // roster is also invalidated explicitly by the role and provisioning
  // mutations below, so the window never hides the admin's own change.
  const { data, isPending, isError, error, refetch } = useQuery<AdminUser[]>({
    queryKey: ['admin-users'], queryFn: admin.listUsers, staleTime: 30_000,
  });
  const [roleError, setRoleError] = useState<string | null>(null);
  const roleMut = useMutation({
    mutationFn: ({ id, role }: { id: string; role: UserRole }) => admin.updateUserRole(id, role),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-users'] }); setRoleError(null); },
    onError: (err: unknown) => { setRoleError(apiErrorText(err)); },
  });

  const handleRoleChange = (targetUser: AdminUser, newRole: UserRole) => {
    if (targetUser.id === currentUser?.id) return; // Self-change blocked at UI level
    if (newRole === targetUser.role) return;
    const action = t('assignRole', { role: tr(newRole) });
    if (!window.confirm(t('confirmRoleChange', { action, name: targetUser.name }))) return;
    roleMut.mutate({ id: targetUser.id, role: newRole });
  };

  const isSelf = (userId: string) => currentUser?.id === userId;

  return (
    <div className="space-y-5">
      <PageHeader icon={Users} title={t('usersRoles')} subtitle={t('usersRolesDesc')} />
      <SectionCard>
        {roleError && (
          <div className="mb-3 p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>{roleError}</span>
          </div>
        )}
        <CreateStaffAccount />
        {/* Error before data — see `Empty` in ../_components/console-ui. */}
        {isError ? <ErrorBox text={apiErrorText(error)} onRetry={() => { void refetch(); }} /> :
          isPending ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
          (data?.length ?? 0) === 0 ? <Empty text={t('noUsers')} /> :
          <div className="space-y-2">
            {data!.map((u) => (
              <div key={u.id} className={`flex items-center gap-3 p-3 rounded-xl transition-colors ${isSelf(u.id) ? 'bg-indigo-50 border border-indigo-100' : 'bg-slate-50'}`}>
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center text-sm font-bold shrink-0 ${isSelf(u.id) ? 'bg-indigo-600 text-white' : 'bg-indigo-100 text-indigo-600'}`}>{u.name?.[0] ?? '?'}</div>
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-medium text-slate-800">
                    {u.name}
                    {isSelf(u.id) && <span className="ml-1.5 text-[10px] font-medium bg-indigo-600 text-white px-1.5 py-0.5 rounded">{t('you')}</span>}
                  </p>
                  <p className="text-[11px] text-slate-400">{u.email} · {t('created')} {new Date(u.created_at).toLocaleDateString('vi-VN')}</p>
                </div>
                <select
                  value={u.role}
                  onChange={(e) => handleRoleChange(u, e.target.value as UserRole)}
                  disabled={roleMut.isPending || isSelf(u.id)}
                  className={`h-9 px-3 text-[13px] border border-slate-200 rounded-lg bg-white outline-none transition-all ${isSelf(u.id) ? 'cursor-not-allowed opacity-60 text-slate-400' : 'focus:border-indigo-400 cursor-pointer'}`}
                  title={isSelf(u.id) ? t('cannotSelfChange') : undefined}
                >
                  {USER_ROLES.map((r) => (
                    <option key={r} value={r}>{tr(r)}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>}
      </SectionCard>
    </div>
  );
}

/**
 * Provision an HR or System Admin account.
 *
 * First-run setup mints one System Admin and every other account-creation
 * route sits behind HR, so this form is the only way a fresh deployment gets
 * its first HR account. The temporary password is shown once and never again.
 */
function CreateStaffAccount() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const tr = useTranslations('roles');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<{ email: string; name: string; role: StaffRole }>({
    email: '', name: '', role: 'hr',
  });
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<admin.StaffAccountCreateResponse | null>(null);

  const parsed = staffAccountCreateSchema.safeParse(form);

  const createMut = useMutation({
    mutationFn: () => admin.createStaffAccount(staffAccountCreateSchema.parse(form)),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      setCreated(res);
      setForm({ email: '', name: '', role: 'hr' });
      setError(null);
    },
    onError: (e) => setError(apiErrorText(e)),
  });

  if (!open) {
    return (
      <div className="mb-4">
        <button
          onClick={() => { setOpen(true); setCreated(null); setError(null); }}
          className="inline-flex items-center gap-1.5 h-9 px-4 text-[13px] font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-all shadow-sm shadow-indigo-200"
        >
          <Plus className="w-4 h-4" /> {t('createStaffAccount')}
        </button>
      </div>
    );
  }

  return (
    <div className="mb-4 p-4 rounded-xl border border-indigo-100 bg-indigo-50/40 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-semibold text-slate-800">{t('createStaffAccount')}</p>
        <button onClick={() => setOpen(false)} className="p-1 text-slate-400 hover:text-slate-600" aria-label={t('close')}>
          <X className="w-4 h-4" />
        </button>
      </div>
      <p className="text-[12px] text-slate-500">{t('createStaffAccountDesc')}</p>

      {error && (
        <div className="p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {created ? (
        <div className="p-3 bg-white rounded-lg border border-emerald-200 space-y-1.5">
          <p className="text-[13px] font-medium text-emerald-700 flex items-center gap-1.5">
            <Check className="w-4 h-4" /> {t('staffAccountCreated', { email: created.user.email })}
          </p>
          <p className="text-[12px] text-slate-600">
            {t('temporaryPassword')}: <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">{created.temporary_password}</code>
          </p>
          <p className="text-[11px] text-amber-600">{t('temporaryPasswordNotice')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder={t('staffName')}
            className="h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400"
          />
          <input
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder={t('staffEmail')}
            className="h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400"
          />
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as StaffRole })}
            className="h-10 px-3 text-[13px] border border-slate-200 rounded-xl bg-white focus:border-indigo-400 outline-none transition-all cursor-pointer"
          >
            {STAFF_ROLES.map((r) => (
              <option key={r} value={r}>{tr(r)}</option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-center gap-2">
        {created ? (
          <button
            onClick={() => setCreated(null)}
            className="h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-all"
          >
            {t('createAnother')}
          </button>
        ) : (
          <button
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending || !parsed.success}
            className="inline-flex items-center gap-1.5 h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-all"
          >
            {createMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            {t('create')}
          </button>
        )}
        {!created && !parsed.success && (form.email || form.name) && (
          <span className="text-[11px] text-slate-400">{parsed.error.issues[0]?.message}</span>
        )}
      </div>
    </div>
  );
}
