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
import { Bot, Cpu, Activity, FileText, Users, Mail, KeyRound, LayoutDashboard } from 'lucide-react';
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
   * Four groups, one per kind of responsibility, mirroring the HR shell. A
   * flat run of seven labels would make the admin read all seven to find one.
   *
   * "Tích hợp Google" opens and sits above the other three: it decides who
   * can sign in at all (Google OAuth) and which Workspace domain the shared
   * account may be, while "Người dùng" and the system group below it only
   * matter once someone can. That is also the order the Quick-Start Guide
   * walks a fresh deployment through.
   *
   * "Tên miền Workspace được phép nối" sits in this group, not "Người dùng",
   * on purpose: #418 found it does not gate login (`allowed_domains` only
   * gates which Workspace domain the Organization Google Connection may use,
   * `organization_google_connection_service.py:291-294`) -- the account-list
   * grouping was the source of that misreading.
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
      label: t('settings.navGroups.googleIntegration'),
      items: [
        { href: '/settings/oauth', label: t('settings.oauth.nav'), icon: KeyRound },
        { href: '/settings/domains', label: t('settings.emailDomains'), icon: Mail },
      ],
    },
    {
      label: t('settings.navGroups.users'),
      items: [
        { href: '/settings/users', label: t('settings.usersRoles'), icon: Users },
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
