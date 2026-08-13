'use client';

import React from 'react';
import {
  LayoutDashboard, Inbox, UserCheck, Briefcase, Calendar,
  CheckSquare, Users, Clock, FileText, FileSpreadsheet,
  Mail, FileSearch, BarChart3, BookOpen
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import AppShell from '@/components/app-shell';

import type { NavGroup } from '@/components/app-shell';
import { useAuthGuard } from '@/lib/auth/session';
import type { UserRole } from '@/lib/auth/roles';

/**
 * Every surface in this route group is HR business (ADR-0009). System setup
 * lives in the `(system-admin)` group at /settings and is not reachable from
 * here — a system admin has no HR role and would only collect 403s.
 */
const ALLOW: readonly UserRole[] = ['hr'];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  useAuthGuard({ requireAuth: true, allowRoles: ALLOW });
  const t = useTranslations();

  const navGroups: NavGroup[] = [
    {
      label: t('recruitment.title'),
      items: [
        { href: '/recruitment/inbox', label: t('recruitment.nav.inbox'), icon: Inbox },
        { href: '/recruitment/candidates', label: t('recruitment.nav.candidates'), icon: UserCheck },
        { href: '/recruitment/job-openings', label: t('recruitment.nav.jobOpenings'), icon: Briefcase },
        { href: '/recruitment/interviews', label: t('recruitment.nav.interviews'), icon: Calendar },
        { href: '/recruitment/review', label: t('recruitment.nav.review'), icon: FileSearch },
        { href: '/recruitment/metrics', label: t('recruitment.nav.metrics'), icon: BarChart3 },
      ],
    },
    {
      label: t('employees.nav'), // "Nhân sự" section — use employees.nav as section label
      items: [
        { href: '/onboarding', label: t('onboarding.nav'), icon: CheckSquare },
        { href: '/employees', label: t('employees.nav'), icon: Users },
        { href: '/requests', label: t('requests.nav'), icon: FileText },
      ],
    },
    {
      label: t('attendance.nav'), // "Chấm công & Lương" section
      items: [
        { href: '/attendance', label: t('attendance.nav'), icon: Clock },
        { href: '/payroll/payslips', label: t('payroll.nav'), icon: FileSpreadsheet },
      ],
    },
    {
      label: t('system.nav'),
      items: [
        { href: '/knowledge-base', label: t('system.knowledgeBase'), icon: BookOpen },
        { href: '/gmail', label: t('system.gmail'), icon: Mail },
      ],
    },
  ];

  return (
    <AppShell
      roleLabel={t('appShell.hrLabel')}
      sidebarSectionLabel={t('appShell.hrSection')}
      navGroups={navGroups}
      navItems={[
        { href: '/dashboard', label: t('dashboard.title'), icon: LayoutDashboard },
      ]}
      assistantHref="/assistant"
      userDisplayNameFallback={t('appShell.hrFallbackName')}
    >
      <div>
        {children}
      </div>
    </AppShell>
  );
}
