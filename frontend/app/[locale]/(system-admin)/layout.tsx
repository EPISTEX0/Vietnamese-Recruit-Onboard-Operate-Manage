'use client';

/**
 * System Admin shell.
 *
 * Separate route group from `(dashboard)` on purpose: the system admin console
 * carries no HR business surface, and the HR dashboard carries no system
 * setup. Only `system_admin` gets in; an HR or employee account that lands
 * here is bounced to its own home by the guard.
 *
 * The route group leaves URLs untouched — the console stays at `/settings`.
 */

import React from 'react';
import { Bot, Cpu, Activity, FileText, Users, ShieldCheck, Mail } from 'lucide-react';
import { useTranslations } from 'next-intl';
import AppShell from '@/components/app-shell';
import type { NavGroup } from '@/components/app-shell';
import { useAuthGuard } from '@/lib/auth/session';
import type { UserRole } from '@/lib/auth/roles';

const ALLOW: readonly UserRole[] = ['system_admin'];

export default function SystemAdminLayout({ children }: { children: React.ReactNode }) {
  useAuthGuard({ requireAuth: true, allowRoles: ALLOW });
  const t = useTranslations();

  /**
   * Three groups, one per kind of responsibility, mirroring the HR shell. A
   * flat run of seven labels would make the admin read all seven to find one.
   */
  const navGroups: NavGroup[] = [
    {
      label: t('settings.navGroups.ai'),
      items: [
        { href: '/settings/ai', label: t('settings.aiConfig'), icon: Bot },
        { href: '/settings/tools', label: t('settings.aiTools'), icon: Cpu },
      ],
    },
    {
      label: t('settings.navGroups.usersAccess'),
      items: [
        { href: '/settings/users', label: t('settings.usersRoles'), icon: Users },
        { href: '/settings/whitelist', label: t('settings.accessWhitelist'), icon: ShieldCheck },
        { href: '/settings/domains', label: t('settings.emailDomains'), icon: Mail },
      ],
    },
    {
      label: t('settings.navGroups.system'),
      items: [
        { href: '/settings/health', label: t('settings.systemHealth'), icon: Activity },
        { href: '/settings/audit', label: t('settings.auditLog'), icon: FileText },
      ],
    },
  ];

  /*
   * No `sidebarBadge` here: the top bar already names the role, and stacking a
   * "Quản trị hệ thống" badge on top of the "QUẢN TRỊ HỆ THỐNG" section label
   * spent the sidebar's first inches saying the same thing twice. The prop
   * itself stays on AppShell — the Employee shell renders the signed-in user's
   * name and email through it.
   */
  return (
    <AppShell
      roleLabel={t('appShell.systemAdminLabel')}
      sidebarSectionLabel={t('appShell.systemAdminSection')}
      navGroups={navGroups}
      userDisplayNameFallback={t('appShell.systemAdminFallbackName')}
    >
      {children}
    </AppShell>
  );
}
