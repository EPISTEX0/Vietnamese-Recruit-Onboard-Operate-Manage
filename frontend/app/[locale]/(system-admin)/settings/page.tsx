'use client';

/**
 * Tổng quan hệ thống — the System Admin Console's homepage, and the screen a
 * system admin lands on straight after login.
 *
 * It answers two questions in the order a deployment meets them. Just
 * installed: "what do I do now?" — the Quick-Start Guide. Running for months:
 * "how is it doing?" — four status cards and the last ten changes.
 *
 * The checklist is designed to retire itself: it is the opening phase's
 * overlay, not permanent content, and it disappears once all three tasks are
 * done. The operational readouts below it are what stays, which is why they
 * exist here at all — solving "empty right after setup" with a checklist alone
 * just creates a second empty screen on the day it is finished.
 *
 * Every decision about the checklist is made in `lib/system-admin/setup-guide`,
 * a pure module tested on its own. This file draws what that module returns and
 * decides nothing.
 *
 * Five queries, all pre-existing, no backend change (ADR-0014). `runtime-health`
 * deliberately reuses `/settings/health`'s query key so the two share one cache
 * entry rather than hitting the endpoint twice.
 */

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslations, useLocale, useFormatter } from 'next-intl';
import {
  LayoutDashboard, Bot, Activity, Users, FileText,
  CheckCircle2, Circle, HelpCircle, ChevronRight, RefreshCw,
} from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type {
  AdminUser, AuditLog, OAuthConfig, OrganizationAIConfiguration,
  PaginatedAuditLogs, RuntimeHealthResponse,
} from '@/lib/api/admin';
import { Link } from '@/i18n/navigation';
import { USER_ROLES } from '@/lib/auth/roles';
import { buildSetupGuide, type SetupTaskId, type SetupTaskView } from '@/lib/system-admin/setup-guide';
import { PageHeader, formatAuditDetails, BADGE_TONE_PARTS, type BadgeTone } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from './_components/console-ui';
import { apiErrorText } from './_components/api-error-text';

const DAY_MS = 24 * 60 * 60 * 1000;

/** i18n key per task. Kept here, not in the pure module — it is presentation. */
const TASK_TITLE_KEY: Record<SetupTaskId, string> = {
  googleOAuth: 'taskGoogleOAuth',
  aiConfiguration: 'taskAiConfiguration',
  hrAccount: 'taskHrAccount',
};

export default function SystemOverviewPage() {
  const t = useTranslations('settings');
  const to = useTranslations('settings.systemOverview');
  const tq = useTranslations('settings.quickStart');
  const tr = useTranslations('roles');
  const ta = useTranslations('audit');
  const locale = useLocale();
  const format = useFormatter();

  const oauthConfig = useQuery<OAuthConfig>({
    queryKey: ['oauth-config'], queryFn: admin.getOAuthConfig, staleTime: 30_000,
  });
  const aiConfiguration = useQuery<OrganizationAIConfiguration>({
    queryKey: ['ai-config'], queryFn: admin.getOrganizationAIConfiguration, staleTime: 30_000,
  });
  const users = useQuery<AdminUser[]>({
    queryKey: ['admin-users'], queryFn: admin.listUsers, staleTime: 30_000,
  });
  // Same key and staleTime as `/settings/health` so both surfaces share one
  // cache entry instead of polling the endpoint twice.
  const runtimeHealth = useQuery<RuntimeHealthResponse>({
    queryKey: ['runtime-health'], queryFn: admin.getRuntimeHealth, staleTime: 30_000,
  });

  /*
   * One request feeds both the count card and the activity list: the response
   * carries `total` alongside `items` (ADR-0014).
   *
   * The window is computed inside `queryFn`, not during render. Reading the
   * clock while rendering is impure and a fresh timestamp in the query key
   * would make every render a cache miss; doing it at fetch time keeps the key
   * stable and re-measures the 24 hours on each refetch, which is what "last 24
   * hours" should mean anyway.
   */
  const auditLogs = useQuery<PaginatedAuditLogs>({
    queryKey: ['audit-logs', 'last-24h'],
    queryFn: () => admin.getAuditLogs({
      start_date: new Date(Date.now() - DAY_MS).toISOString(),
      page_size: 10,
    }),
    staleTime: 30_000,
  });

  const guide = buildSetupGuide({ oauthConfig, aiConfiguration, users });

  /** Which query a task's retry button re-runs. Wiring, not logic. */
  const retryTask: Record<SetupTaskId, () => void> = {
    googleOAuth: () => { void oauthConfig.refetch(); },
    aiConfiguration: () => { void aiConfiguration.refetch(); },
    hrAccount: () => { void users.refetch(); },
  };

  const services = runtimeHealth.data?.services ?? [];
  const healthyServices = services.filter((s) => s.status === 'healthy').length;

  const accountsByRole = USER_ROLES
    .map((role) => ({ role, count: users.data?.filter((u) => u.role === role).length ?? 0 }))
    .filter((entry) => entry.count > 0);

  return (
    <div className="space-y-6 animate-fadeSlideIn">
      <PageHeader icon={LayoutDashboard} title={to('title')} subtitle={to('subtitle')} />

      {guide.visible && (
        <SectionCard
          icon={<CheckCircle2 className="w-5 h-5 text-indigo-600" />}
          title={tq('title')}
          desc={tq('subtitle')}
          action={guide.progress && (
            <span className="text-[12px] font-semibold text-indigo-600 shrink-0">
              {tq('progress', { done: guide.progress.done, total: guide.progress.total })}
            </span>
          )}
        >
          <div className="space-y-2">
            {guide.tasks.map((task) => (
              <SetupTaskRow key={task.id} task={task} onRetry={retryTask[task.id]} />
            ))}
          </div>
        </SectionCard>
      )}

      {/* Four status cards. Each reads its own query, so one failure shows a
          dash in that card and leaves the other three alone — Dashboard HR's
          precedent. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={Bot} tone="indigo" kicker={to('cardAiKicker')}
          isLoading={aiConfiguration.status === 'pending'} isError={aiConfiguration.status === 'error'}
          value={aiConfiguration.data?.configured
            ? (aiConfiguration.data.provider ?? '—')
            : <span className="text-base">{to('aiNotConfigured')}</span>}
          sub={to('cardAiSub')}
        />
        <StatCard
          icon={Activity} tone="emerald" kicker={to('cardRuntimeKicker')}
          isLoading={runtimeHealth.status === 'pending'} isError={runtimeHealth.status === 'error'}
          value={`${healthyServices}/${services.length}`}
          sub={to('cardRuntimeSub')}
        />
        <StatCard
          icon={Users} tone="sky" kicker={to('cardAccountsKicker')}
          isLoading={users.status === 'pending'} isError={users.status === 'error'}
          value={users.data?.length ?? 0}
          sub={accountsByRole.map((entry) => `${tr(entry.role)} ${entry.count}`).join(' · ')}
        />
        <StatCard
          icon={FileText} tone="amber" kicker={to('cardAuditKicker')}
          isLoading={auditLogs.status === 'pending'} isError={auditLogs.status === 'error'}
          value={auditLogs.data?.total ?? 0}
          sub={to('cardAuditSub')}
        />
      </div>

      <SectionCard
        icon={<FileText className="w-5 h-5 text-indigo-600" />}
        title={to('recentActivity')}
        desc={to('recentActivityDesc')}
      >
        {auditLogs.status === 'pending' ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => <div key={i} className="animate-pulse h-12 bg-slate-100 rounded-xl" />)}
          </div>
        ) : auditLogs.status === 'error' ? (
          <ErrorBox text={apiErrorText(auditLogs.error)} onRetry={() => { void auditLogs.refetch(); }} />
        ) : (auditLogs.data?.items.length ?? 0) === 0 ? (
          <Empty text={to('noActivity24h')} />
        ) : (
          <div className="space-y-2">
            {auditLogs.data!.items.map((log: AuditLog) => (
              <div key={log.id} className="p-3 bg-slate-50 rounded-xl flex items-start gap-3">
                <div className="w-8 h-8 rounded-lg bg-indigo-100 text-indigo-600 flex items-center justify-center text-[11px] font-bold shrink-0">
                  {log.admin_email?.[0]?.toUpperCase() ?? '?'}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[13px] font-medium text-slate-700">{log.admin_email}</span>
                    <span className="text-[10px] bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded font-medium">
                      {ta(log.action_type)}
                    </span>
                  </div>
                  <p className="text-[12px] text-slate-500">{formatAuditDetails(log.details, locale)}</p>
                </div>
                <span className="text-[11px] text-slate-400 shrink-0">
                  {format.dateTime(new Date(log.created_at), 'short')}
                </span>
              </div>
            ))}
            <div className="pt-2 text-right">
              <Link href="/settings/audit" className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700">
                {t('auditLog')} <ChevronRight className="w-3.5 h-3.5 inline" />
              </Link>
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

/**
 * One checklist row, and always a link: every task has a destination (#314).
 *
 * Takes the whole task view-model and renders it — the three statuses and the
 * two shapes of `unknown` all come in already decided.
 */
function SetupTaskRow({ task, onRetry }: { task: SetupTaskView; onRetry: () => void }) {
  const tq = useTranslations('settings.quickStart');
  const t = useTranslations('settings');

  // Still loading: a skeleton says "wait". Rendering a status here — any status
  // — would be inventing an answer nobody has yet.
  if (task.status === 'unknown' && task.unknownReason === 'loading') {
    return (
      <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl">
        <div className="w-5 h-5 rounded-full bg-slate-200 animate-pulse shrink-0" />
        <div className="h-4 flex-1 max-w-[16rem] rounded bg-slate-200 animate-pulse" />
      </div>
    );
  }

  const title = tq(TASK_TITLE_KEY[task.id]);

  const label = (
    <>
      {task.status === 'done'
        ? <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
        : task.status === 'todo'
          ? <Circle className="w-5 h-5 text-slate-300 shrink-0" />
          : <HelpCircle className="w-5 h-5 text-amber-500 shrink-0" />}
      <div className="flex-1 min-w-0">
        <p className={`text-[13px] font-medium ${task.status === 'done' ? 'text-slate-400' : 'text-slate-700'}`}>
          {/* Done and todo are otherwise told apart by icon shape and colour
              alone, which a screen reader cannot announce. */}
          {task.status !== 'unknown' && (
            <span className="sr-only">
              {task.status === 'done' ? tq('statusDone') : tq('statusTodo')}:{' '}
            </span>
          )}
          {title}
        </p>
        {task.status === 'unknown' && (
          // Not "chưa làm". The query failed, and saying otherwise sends a
          // freshly-installed admin off to redo work they already did.
          <p className="text-[11px] text-amber-600">{tq('statusUnknown')}</p>
        )}
      </div>
    </>
  );

  const rowClass = 'flex items-center gap-3 p-3 bg-slate-50 rounded-xl';
  const labelClass = 'flex items-center gap-3 flex-1 min-w-0 text-left';

  return (
    <div className={`${rowClass} hover:bg-slate-100 transition-colors`}>
      <Link href={task.action.href} className={labelClass}>
        {label}
        <ChevronRight className="w-4 h-4 text-slate-300 shrink-0" />
      </Link>
      {/* Sibling of the link, never a child of it: a button nested inside an
          anchor is a `nested-interactive` violation, and it only worked at all
          because next/link happens to honour `defaultPrevented`. */}
      {task.status === 'unknown' && (
        <button
          onClick={onRetry}
          className="shrink-0 flex items-center gap-1 text-[11px] font-medium text-amber-700 underline hover:text-amber-800"
        >
          <RefreshCw className="w-3 h-3" /> {t('retry')}
        </button>
      )}
    </div>
  );
}

/**
 * One bento readout.
 *
 * Loading draws a skeleton and a failure draws a dash, per card — so a single
 * broken query never blanks the row (Dashboard HR's precedent).
 *
 * Callers pass `status === 'pending'`, not `isLoading`: React Query's
 * `isLoading` is `isPending && isFetching`, so a query paused because the
 * browser is offline reports neither loading nor error, and the card would
 * print a confident `0` — the same class of lie as calling an unread task
 * "chưa làm".
 */
function StatCard({ icon: Icon, tone, kicker, isLoading, isError, value, sub }: {
  icon: React.ComponentType<{ className?: string }>;
  tone: BadgeTone;
  kicker: string;
  isLoading: boolean;
  isError: boolean;
  value: React.ReactNode;
  sub: string;
}) {
  // One tone rather than a `bg`/`fg` pair of bare class strings: they were
  // never independent, and passing them separately is what let this row drift
  // to `text-*-600` while the shared table said `text-*-700` (#308). Going
  // through the table closes that gap for all four cards — including the
  // indigo one, whose icon is now 700 rather than the 600 that `DESIGN.md`
  // gives brand-accent icons. That shade collision is the stated cost of one
  // API for the whole row; see "Icon mang màu gì" in `DESIGN.md`.
  const { bg, fg } = BADGE_TONE_PARTS[tone];

  return (
    <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
      <div className="flex items-center justify-between mb-3">
        <div className={`p-2 rounded-lg ${bg}`}><Icon className={`w-5 h-5 ${fg}`} /></div>
        <span className="text-[10px] font-mono uppercase text-slate-400">{kicker}</span>
      </div>
      {isLoading ? (
        <div className="animate-pulse h-8 bg-slate-100 rounded w-3/4" />
      ) : isError ? (
        <p className="text-xs text-rose-500">—</p>
      ) : (
        <>
          <div className="text-2xl font-bold text-slate-900 truncate">{value}</div>
          <p className="text-xs text-slate-500 mt-1 truncate">{sub}</p>
        </>
      )}
    </div>
  );
}
