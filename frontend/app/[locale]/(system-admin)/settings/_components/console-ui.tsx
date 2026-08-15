'use client';

/**
 * Primitives shared by the System Admin Console routes.
 *
 * Console-local on purpose — these are deliberately NOT moved into
 * `components/shared-ui.tsx`, and deliberately NOT replaced by the lookalikes
 * already there (ADR-0014):
 *
 * - `ErrorBox` carries a retry button; `ErrorBanner` does not. Every surface
 *   here is a single query the admin can simply re-run, so losing the button
 *   would mean reloading the page to recover from a transient 500.
 * - `SectionCard` carries a header strip (icon + title + description + action);
 *   `Card` is body-only. Swapping either way loses something.
 *
 * Only the console renders these, so they stay next to the routes that do.
 */

import React from 'react';
import { AlertCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';

/**
 * A console content card.
 *
 * The header strip renders only when `title` is given. A section route whose
 * whole body is one card puts its title in the page's `PageHeader` instead and
 * omits it here — otherwise the same heading appears twice, a few dozen pixels
 * apart. Sections built from several blocks (AI configuration) keep the strip
 * so each block is still labelled.
 */
export function SectionCard({ icon, title, desc, action, children }: {
  icon?: React.ReactNode; title?: string; desc?: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {title && (
        <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
          {icon && <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center shrink-0">{icon}</div>}
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-bold text-slate-900">{title}</h2>
            {desc && <p className="text-[12px] text-slate-500">{desc}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  );
}

export function ErrorBox({ text, onRetry }: { text: string; onRetry: () => void }) {
  const t = useTranslations('settings');
  return (
    <div className="flex items-center gap-3 p-4 bg-rose-50 border border-rose-200 rounded-xl text-[13px] text-rose-700">
      <AlertCircle className="w-5 h-5 shrink-0" /><span className="flex-1">{text}</span>
      <button onClick={onRetry} className="font-medium underline hover:text-rose-800">{t('retry')}</button>
    </div>
  );
}

/**
 * "There are no records" — a statement of fact about the deployment, so a
 * surface may only draw it once its query has actually answered.
 *
 * Every list route here therefore branches error *before* it looks at `data`
 * (#305). Two reasons it is that order and not the other:
 *
 * - An unanswered query leaves `data` undefined, and a length check on
 *   undefined reads as zero. Ordering empty first turns every failure into
 *   "danh sách trống". On the three access-control sections that is the
 *   dangerous direction: an admin told the allowlist is empty concludes nobody
 *   holds access, when the system merely could not say who does.
 * - React Query keeps the last good payload alongside `error` when a background
 *   refetch fails, so a branch that reads `data` first quietly presents a list
 *   of who *used to* hold access as who holds it now.
 *
 * The waiting arm is keyed on `isPending`, never on `isLoading`: `isLoading` is
 * `isPending && isFetching`, so a query paused because the browser is offline
 * reports neither loading nor error and slips past both arms into this one.
 * `StatCard` in `../page.tsx` hit the same edge and names it there.
 *
 * Same rule as `lib/system-admin/setup-guide.ts`, which resolves an unreadable
 * answer to `unknown` rather than to `todo`.
 */
export function Empty({ text }: { text: string }) {
  return <p className="text-[13px] text-slate-400 py-10 text-center">{text}</p>;
}
