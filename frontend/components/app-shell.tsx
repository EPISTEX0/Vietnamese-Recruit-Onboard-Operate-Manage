/**
 * Shared app shell — single layout used by both Dashboard and Employee route groups.
 *
 * Extracted from app/(dashboard)/layout.tsx and app/(employee)/layout.tsx
 * (~85% duplicate). All visual differences are parameterized as props.
 *
 * Follows AI Studio design system: slate/indigo, rounded-full pill, Be Vietnam Pro + JetBrains Mono.
 *
 * v2: Added nav grouping (navGroups), mobile hamburger menu.
 * v3: i18n — replaced hardcoded text with useTranslations.
 * v4: Optimistic active sidebar, navigation skeleton, smooth transitions.
 */

'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePathname, useRouter } from '@/i18n/navigation';
import { Sparkles, LogOut, Menu, X } from 'lucide-react';
import { useSession } from '@/lib/auth/session';
import { useTranslations } from 'next-intl';
import LocaleSwitcher from '@/components/locale-switcher';

/**
 * Map of sidebar routes to their primary React Query cache keys.
 * Used by the hover prefetch strategy to warm up query data
 * before the user clicks through.
 */
const ROUTE_QUERY_MAP: Record<string, import('@tanstack/react-query').QueryKey[]> = {
  '/dashboard': [
    ['recruitment-metrics'],
  ],
  '/recruitment/inbox': [
    ['recruitment-inbox'],
    ['recruitment-job-openings', 'open'],
  ],
  '/recruitment/candidates': [
    ['recruitment-candidates'],
  ],
  '/recruitment/job-openings': [
    ['recruitment-job-openings'],
  ],
  '/recruitment/interviews': [
    ['google-calendars'],
    ['recruitment-candidates', { status: ['reviewing', 'interview_scheduled'], page_size: 100 }],
    ['recruitment-conflicts'],
  ],
  '/onboarding': [
    ['onboarding', 'counts'],
  ],
  '/employees': [
    ['employees-list'],
    ['departments-list'],
    ['positions-list'],
  ],
  '/attendance': [
    ['attendance-today', 'me'],
    ['employees-list', { active: true, all: true }],
  ],
  '/payroll/payslips': [
    ['admin-payslips'],
    ['employees-list', { all: true }],
  ],
  '/knowledge-base': [
    ['kb-documents'],
  ],
  '/gmail': [
    ['gmail-connection'],
    ['gmail-messages'],
  ],
  // One entry per system admin console section, each naming its own query key
  // family. `/settings` used to be a single entry that pulled three unrelated
  // keys on hover, back when all seven sections shared one route.
  //
  // `audit-logs` is the family prefix, not the exact key: the audit page runs
  // `['audit-logs', params]` because its filters are part of the key, the same
  // prefix it invalidates against.
  '/settings/ai': [
    ['ai-config'],
  ],
  '/settings/tools': [
    ['assistant-tools'],
  ],
  '/settings/health': [
    ['runtime-health'],
  ],
  '/settings/audit': [
    ['audit-logs'],
  ],
  '/settings/users': [
    ['admin-users'],
  ],
  '/settings/whitelist': [
    ['whitelist'],
  ],
  '/settings/domains': [
    ['org-domains'],
  ],
  '/employee': [
    ['employee-dashboard'],
  ],
  '/employee/attendance': [
    ['attendance-today', 'me'],
  ],
  '/employee/requests': [
    ['my-requests'],
  ],
  '/employee/payslips': [
    ['my-payslips'],
  ],
};

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

export interface NavGroup {
  /** Section header label, e.g. "Tuyển dụng" */
  label: string;
  items: NavItem[];
}

export interface AppShellProps {
  /** Role label shown in the top bar, e.g. "/ Quản trị" or "/ Nhân viên" */
  roleLabel: string;
  /** Section heading above the sidebar nav, e.g. "Hệ Thống HR" or "Trang Nhân Viên (ESS)" */
  sidebarSectionLabel: string;
  /** Navigation items rendered in the sidebar (flat style — backward compat) */
  navItems?: NavItem[];
  /** Navigation groups with section headers (preferred over navItems) */
  navGroups?: NavGroup[];
  /** Sidebar badge: the org/user card rendered above the nav section */
  sidebarBadge?: React.ReactNode;
  /**
   * Where the AI Assistant button links to, e.g. "/assistant" or
   * "/employee/assistant". Omit to hide the button — the HR assistant is an HR
   * surface, so the system admin shell has nothing to point it at.
   */
  assistantHref?: string;
  /** Default fallback for user display name */
  userDisplayNameFallback: string;
  /** Optional extra button in the top bar (e.g. settings gear icon) */
  topBarExtra?: React.ReactNode;
  children: React.ReactNode;
}

export default function AppShell({
  roleLabel,
  sidebarSectionLabel,
  navItems,
  navGroups,
  sidebarBadge,
  assistantHref,
  userDisplayNameFallback,
  topBarExtra,
  children,
}: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useSession();
  const t = useTranslations('appShell');
  const queryClient = useQueryClient();

  // Mobile sidebar toggle
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Optimistic active path — set immediately on click, cleared on actual route change
  const [optimisticPath, setOptimisticPath] = useState<string | null>(null);

  // Navigation pending flag — set on click, cleared when pathname updates
  const [isNavigating, setIsNavigating] = useState(false);

  // Close sidebar + clear optimistic path + navigation flag on route change
  useEffect(() => {
    setSidebarOpen(false);
    setOptimisticPath(null);
    setIsNavigating(false);
  }, [pathname]);

  const isActive = (href: string) => {
    // Check optimistic first for instant visual feedback
    if (optimisticPath === href) return true;
    // Fallback to actual pathname
    if (href === '/dashboard') return pathname === '/dashboard' || pathname === '/';
    if (href === '/employee') return pathname === '/employee' || pathname === '/employee/dashboard';
    return pathname.startsWith(href);
  };

  const handleNavClick = useCallback((href: string) => {
    if (optimisticPath === href && isActive(href)) return; // already there
    setOptimisticPath(href);
    setIsNavigating(true);
    router.push(href);
  }, [optimisticPath, isActive, router]);

  const handleNavHover = useCallback((href: string) => {
    // Prefetch the Next.js page bundle
    router.prefetch(href);
    // Warm up React Query cache for the target route
    const keys = ROUTE_QUERY_MAP[href] ?? [];
    keys.forEach(key => {
      queryClient.prefetchQuery({ queryKey: key });
    });
  }, [router, queryClient]);

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
    } catch {
      // ignore
    }
    router.replace('/login');
  };

  // Resolve items to render: flat items first, then grouped items
  const groups: NavGroup[] = [
    ...(navItems && navItems.length > 0 ? [{ label: '', items: navItems }] : []),
    ...(navGroups ?? []),
  ];

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans flex flex-col selection:bg-indigo-500 selection:text-white">
      {/* Top bar */}
      <header className="bg-white border-b border-slate-200 px-4 py-2 flex items-center justify-between z-50 shadow-sm">
        <div className="flex items-center gap-3">
          {/* Hamburger for mobile */}
          <button
            onClick={() => setSidebarOpen((p) => !p)}
            className="lg:hidden p-1.5 rounded-lg hover:bg-slate-100 text-slate-500 transition-all"
            title={t('menu')}
          >
            {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>

          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-black tracking-tighter text-sm shadow-md shadow-indigo-100">
            VR
          </div>
          <span className="font-semibold text-sm text-slate-800">{t('brand')}</span>
          <span className="text-[10px] text-slate-400 font-mono hidden sm:inline">
            {roleLabel}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <LocaleSwitcher />
          {topBarExtra}
          <span className="text-xs text-slate-500 font-medium hidden sm:block">
            {user?.name ?? userDisplayNameFallback}
          </span>
          <button
            onClick={handleLogout}
            className="p-1.5 rounded-full hover:bg-rose-50 text-slate-400 hover:text-rose-500 transition-all"
            title={t('logout')}
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row relative">
        {/* Mobile overlay */}
        {sidebarOpen && (
          <div
            className="lg:hidden fixed inset-0 bg-black/40 z-40"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Sidebar */}
        <aside
          className={`w-full lg:w-56 bg-white border-r border-slate-200 p-3 shrink-0 space-y-1 shadow-sm overflow-y-auto
            fixed lg:sticky top-[49px] lg:top-0 left-0 bottom-0 z-50 lg:z-auto
            transition-transform lg:transition-none
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
          `}
        >
          {sidebarBadge && sidebarBadge}

          {sidebarSectionLabel && (
            <div className="text-[9px] font-mono font-bold text-purple-500 px-3 uppercase tracking-wider mb-2">
              {sidebarSectionLabel}
            </div>
          )}

          {groups.map((group, gi) => (
            <div key={gi}>
              {group.label && (
                <div className="text-[9px] font-mono font-bold text-slate-400 px-3 uppercase tracking-wider mb-1 mt-2 first:mt-0">
                      {group.label}
                    </div>
              )}
              {group.items.map((item) => {
                const active = isActive(item.href);

                return (
                  <button
                    key={item.href}
                    onClick={() => handleNavClick(item.href)}
                    onMouseEnter={() => handleNavHover(item.href)}
                    className={`relative w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium transition-all duration-150 ease-out select-none ${
                              active
                                ? 'bg-indigo-50 text-indigo-600 font-semibold shadow-sm shadow-indigo-50/10 scale-[1.02]'
                                : 'text-slate-600 hover:bg-slate-50 hover:text-indigo-600'
                            }`}
                  >
                    {active && <span className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 bg-indigo-500 rounded-full" />}<item.icon className="w-4 h-4 shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </button>
                );
              })}
            </div>
          ))}

          {/* AI Assistant Button */}
          {assistantHref && (
            <div className="pt-3 mt-3 border-t border-slate-100">
              <button
                    onMouseEnter={() => handleNavHover(assistantHref)}
                    onClick={() => handleNavClick(assistantHref)}
                className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-gradient-to-r from-indigo-600 to-indigo-500 text-white hover:from-indigo-500 hover:to-indigo-400 shadow-md shadow-indigo-100 transition-all"
              >
                <Sparkles className="w-4 h-4" />
                {t('assistant')}
              </button>
            </div>
          )}
        </aside>

        {/* Main Content */}
        <main className="flex-1 p-4 lg:p-6 overflow-auto relative">
          {isNavigating ? (
            <div className="space-y-5 animate-fadeIn">
              {/* Page header skeleton */}
              <div className="flex items-center gap-2 mb-1">
                <div className="w-5 h-5 rounded bg-slate-200 animate-pulse" />
                <div className="h-6 w-48 rounded bg-slate-200 animate-pulse" />
              </div>
              <div className="h-4 w-64 rounded bg-slate-100 animate-pulse mb-6" />
              {/* Card skeletons */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm">
                    <div className="w-9 h-9 rounded-lg bg-slate-100 animate-pulse mb-3" />
                    <div className="h-8 w-3/4 rounded bg-slate-100 animate-pulse mb-2" />
                    <div className="h-3 w-1/2 rounded bg-slate-50 animate-pulse" />
                  </div>
                ))}
              </div>
              {/* List skeleton */}
              <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm space-y-3">
                <div className="flex items-center gap-2 mb-4">
                  <div className="w-5 h-5 rounded bg-slate-200 animate-pulse" />
                  <div className="h-5 w-32 rounded bg-slate-200 animate-pulse" />
                </div>
                {[...Array(5)].map((_, i) => (
                  <div key={i} className="h-12 rounded-lg bg-slate-50 animate-pulse" />
                ))}
              </div>
            </div>
          ) : (
            children
          )}
        </main>
      </div>
    </div>
  );
}
