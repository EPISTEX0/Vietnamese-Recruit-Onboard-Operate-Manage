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
import { ShieldCheck, Settings } from 'lucide-react';
import { useTranslations } from 'next-intl';
import AppShell from '@/components/app-shell';
import type { NavItem } from '@/components/app-shell';
import { useAuthGuard } from '@/lib/auth/session';
import type { UserRole } from '@/lib/auth/roles';

const ALLOW: readonly UserRole[] = ['system_admin'];

export default function SystemAdminLayout({ children }: { children: React.ReactNode }) {
  useAuthGuard({ requireAuth: true, allowRoles: ALLOW });
  const t = useTranslations();

  const navItems: NavItem[] = [
    { href: '/settings', label: t('system.settings'), icon: Settings },
  ];

  return (
    <AppShell
      roleLabel={t('appShell.systemAdminLabel')}
      sidebarSectionLabel={t('appShell.systemAdminSection')}
      navItems={navItems}
      userDisplayNameFallback={t('appShell.systemAdminFallbackName')}
      sidebarBadge={
        <div className="p-2.5 mb-3 bg-slate-50 rounded-xl border border-slate-200 flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600">
            <ShieldCheck className="w-4 h-4" />
          </div>
          <span className="font-semibold text-[11px] text-slate-800 block truncate">
            {t('roles.system_admin')}
          </span>
        </div>
      }
    >
      {children}
    </AppShell>
  );
}
