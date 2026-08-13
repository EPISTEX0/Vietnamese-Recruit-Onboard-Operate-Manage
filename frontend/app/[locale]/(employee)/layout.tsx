'use client';

import React from 'react';
import {
  LayoutDashboard, Clock, FileText, FileSpreadsheet,
  User,
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useAuthGuard } from '@/lib/auth/session';
import AppShell from '@/components/app-shell';
import type { NavItem } from '@/components/app-shell';
import type { UserRole } from '@/lib/auth/roles';

/** Employee self-service. Staff roles are sent back to their own home. */
const ALLOW: readonly UserRole[] = ['user'];

export default function EmployeeLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuthGuard({ requireAuth: true, allowRoles: ALLOW });
  const t = useTranslations();

  const navItems: NavItem[] = [
    { href: '/employee', label: t('employee.nav.dashboard'), icon: LayoutDashboard },
    { href: '/employee/attendance', label: t('employee.nav.attendance'), icon: Clock },
    { href: '/employee/requests', label: t('employee.nav.requests'), icon: FileText },
    { href: '/employee/payslips', label: t('employee.nav.payslips'), icon: FileSpreadsheet },
  ];

  return (
    <AppShell
      roleLabel={t('appShell.employeeLabel')}
      sidebarSectionLabel={t('appShell.essSection')}
      navItems={navItems}
      assistantHref="/employee/assistant"
      userDisplayNameFallback={t('employee.defaultName')}
      sidebarBadge={
        <div className="p-2.5 mb-3 bg-slate-50 rounded-xl border border-slate-200 flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-xs">
            <User className="w-4 h-4" />
          </div>
          <div className="overflow-hidden">
            <span className="font-semibold text-[11px] text-slate-800 block truncate">
              {user?.name ?? t('employee.defaultName')}
            </span>
            <span className="text-[9px] text-slate-400 block truncate">
              {user?.email ?? ''}
            </span>
          </div>
        </div>
      }
    >
      {children}
    </AppShell>
  );
}
