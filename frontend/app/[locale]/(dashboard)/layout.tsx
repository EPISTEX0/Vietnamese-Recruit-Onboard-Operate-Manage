'use client';

import React from 'react';
import {
  LayoutDashboard, Inbox, UserCheck, Briefcase, Calendar,
  CheckSquare, Users, Clock, FileText, FileSpreadsheet,
  Mail, Settings, FileSearch, BarChart3, BookOpen
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useRouter, usePathname } from '@/i18n/navigation';
import AppShell from '@/components/app-shell';
    
import type { NavGroup } from '@/components/app-shell';
import { useAuthGuard } from '@/lib/auth/session';

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  useAuthGuard({ requireAuth: true, requireAdmin: true });
  const router = useRouter();
  const t = useTranslations();
  const pathname = usePathname();

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
        { href: '/settings', label: t('system.settings'), icon: Settings },
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
      userDisplayNameFallback="HR Admin"
      topBarExtra={
        <>
          <button
            onClick={() => router.push('/settings')}
            className="p-1.5 rounded-full hover:bg-slate-100 text-slate-500 hover:text-slate-700 transition-all"
            title={t('system.settings')}
          >
            <Settings className="w-4 h-4" />
          </button>
        </>
      }
    >
      <div>
        {children}
      </div>
    </AppShell>
  );
}
