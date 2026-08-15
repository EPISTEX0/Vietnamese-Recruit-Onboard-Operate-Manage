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
import { Bot, Cpu, Activity, FileText, Users, ShieldCheck, Mail, KeyRound, LayoutDashboard } from 'lucide-react';
import { useTranslations } from 'next-intl';
import AppShell from '@/components/app-shell';
import type { NavGroup, NavItem } from '@/components/app-shell';
import { useAuthGuard } from '@/lib/auth/session';
import type { UserRole } from '@/lib/auth/roles';

const ALLOW: readonly UserRole[] = ['system_admin'];

export default function SystemAdminLayout({ children }: { children: React.ReactNode }) {
  useAuthGuard({ requireAuth: true, allowRoles: ALLOW });
  const t = useTranslations();

  /**
   * The console's home sits above the groups, ungrouped — it is not one of the
   * seven configuration sections, it is the place the admin lands after login
   * and the answer to "what do I do now?".
   */
  const navItems: NavItem[] = [
    { href: '/settings', label: t('settings.systemOverview.title'), icon: LayoutDashboard },
  ];

  /**
   * Three groups, one per kind of responsibility, mirroring the HR shell. A
   * flat run of eight labels would make the admin read all eight to find one.
   *
   * Google OAuth opens "Người dùng & Truy cập" and sits above the other three:
   * it decides who can sign in at all, while the accounts, the allowlist and
   * the email domains below it only matter once someone can. That is also the
   * order the Quick-Start Guide walks a fresh deployment through.
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
        { href: '/settings/oauth', label: t('settings.oauth.nav'), icon: KeyRound },
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
      navItems={navItems}
      navGroups={navGroups}
      userDisplayNameFallback={t('appShell.systemAdminFallbackName')}
    >
      {children}
    </AppShell>
  );
}
