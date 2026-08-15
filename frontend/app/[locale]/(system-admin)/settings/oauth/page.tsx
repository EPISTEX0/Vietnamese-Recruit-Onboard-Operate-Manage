'use client';

/**
 * Cấu hình Google OAuth — the credentials this deployment signs people in with.
 *
 * The eighth console section, and the only surface in the repo that touches an
 * OAuth client secret. Until #307 the backend half had existed on its own
 * (`GET`/`POST /api/system-admin/oauth/config`) with nothing in front of it, so
 * changing these credentials meant editing environment variables and
 * restarting.
 *
 * **The screen is not the form.** The form is the obvious half; the thing this
 * section exists to say is `source`. The backend answers with the database
 * configuration when one exists and falls back to the environment otherwise —
 * two sources that can hold different values, with no bell that rings when they
 * diverge. An admin editing `GOOGLE_CLIENT_ID` in the deployment's env file
 * while a database row quietly overrides it has no way to find that out today.
 * So the source is drawn first, above the values, and it says which one is
 * *in effect* rather than merely which one exists.
 *
 * Secret handling, the property to keep intact:
 *
 * - The backend never sends a plaintext secret. `client_secret_masked` is all
 *   that ever crosses the wire (`identity/api/schemas.py:195`), and it is the
 *   only secret-derived value this module reads.
 * - `UpdateForm` therefore starts `client_secret` at `''` on every path — there
 *   is no value it *could* prefill, and no state here ever holds one that came
 *   from the server. The two non-secret fields do start from what is running,
 *   because both are already on screen a few dozen pixels above.
 *
 * Branch order is `isError → isPending → empty → data`, the invariant `Empty`
 * in `../_components/console-ui` carries, and the waiting arm is keyed on
 * `isPending` rather than `isLoading` for the reason named there: `isLoading`
 * is `isPending && isFetching`, so a query paused because the browser is
 * offline reports neither loading nor error. Here the empty arm says "no client
 * ID is set", which is a claim about the deployment — reachable only once the
 * query has actually answered.
 *
 * The query key is `['oauth-config']`, the same one Tổng quan hệ thống reads
 * for its Quick-Start checklist. That is what makes the OAuth task flip to done
 * without a reload: one cache entry, invalidated on save.
 */

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import {
  KeyRound, Loader2, Save, AlertCircle, CheckCircle2, Database, Server, HelpCircle,
} from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type { OAuthConfig } from '@/lib/api/admin';
import { oauthConfigUpdateSchema } from '@/lib/api/admin-schemas';
import { PageHeader } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

/** Shared with Tổng quan hệ thống, deliberately — see the module docstring. */
const OAUTH_CONFIG_KEY = ['oauth-config'];

export default function OAuthConfigPage() {
  const t = useTranslations('settings.oauth');
  const oauth = useQuery<OAuthConfig>({
    queryKey: OAUTH_CONFIG_KEY,
    queryFn: admin.getOAuthConfig,
    staleTime: 30_000,
  });

  return (
    <div className="space-y-5">
      <PageHeader icon={KeyRound} title={t('title')} subtitle={t('subtitle')} />

      <SectionCard
        icon={<KeyRound className="w-5 h-5 text-indigo-600" />}
        title={t('currentTitle')}
        desc={t('currentDesc')}
      >
        {/* Error before data — see `Empty` in ../_components/console-ui. */}
        {oauth.isError ? (
          <ErrorBox text={apiErrorText(oauth.error)} onRetry={() => { void oauth.refetch(); }} />
        ) : oauth.isPending ? (
          <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto my-5 block" />
        ) : (
          <CurrentConfig config={oauth.data} />
        )}
      </SectionCard>

      {/* No write surface while the current configuration is unreadable: the
          form's two non-secret fields start from it, and offering to overwrite
          credentials we could not read invites replacing a working setup with
          a half-remembered one. */}
      {!oauth.isError && !oauth.isPending && (
        <SectionCard
          icon={<Save className="w-5 h-5 text-indigo-600" />}
          title={t('updateTitle')}
          desc={t('updateDesc')}
        >
          <UpdateForm current={oauth.data} />
        </SectionCard>
      )}
    </div>
  );
}

/**
 * The configuration in effect, source first.
 *
 * `client_id` is read defensively before `.trim()`: the empty arm below is a
 * statement about the deployment, and a payload this module cannot read must
 * not become one — same rule as `lib/system-admin/setup-guide.ts`.
 */
function CurrentConfig({ config }: { config: OAuthConfig }) {
  const t = useTranslations('settings.oauth');
  const configured = typeof config.client_id === 'string' && config.client_id.trim().length > 0;

  return (
    <div className="space-y-4">
      <SourceBanner source={config.source} />
      {configured ? (
        <dl className="space-y-3">
          <ConfigRow label={t('clientId')} value={config.client_id} mono />
          {/* Whatever the backend masked it to. The console has no unmasked
              form of this value to reveal, so there is no eye toggle here. */}
          <ConfigRow
            label={t('clientSecret')}
            value={config.client_secret_masked || t('secretUnset')}
            mono
          />
          <ConfigRow label={t('redirectUri')} value={config.redirect_uri} mono />
          <ConfigRow label={t('updatedAt')} value={formatUpdatedAt(config.updated_at, t('updatedAtUnknown'))} />
        </dl>
      ) : (
        <Empty text={t('notConfigured')} />
      )}
    </div>
  );
}

/**
 * One `dt`/`dd` pair of the read-only configuration list.
 *
 * Deliberately not called `Field`: `@/components/shared-ui` exports a `Field`
 * of its own — a `<label>` wrapping an input — and this module already imports
 * from that file. Two components a rename away from colliding, one writable and
 * one not, on the page that shows a client secret.
 */
function ConfigRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-baseline gap-1 sm:gap-4">
      <dt className="text-[12px] text-slate-500 sm:w-40 shrink-0">{label}</dt>
      <dd className={`text-[13px] text-slate-800 break-all ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </div>
  );
}

/** `null` on every environment-sourced configuration — the env has no mtime. */
function formatUpdatedAt(updatedAt: string | null, fallback: string): string {
  if (!updatedAt) return fallback;
  const parsed = new Date(updatedAt);
  if (Number.isNaN(parsed.getTime())) return fallback;
  return parsed.toLocaleString('vi-VN', {
    hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric',
  });
}

/**
 * Which of the two configurations the deployment is actually running.
 *
 * The point of the whole section, so it is a banner rather than one more row in
 * the list below. An unrecognised value gets its own arm instead of falling
 * into either known one: guessing "environment" for a source the console does
 * not know would be a confident sentence about where the credentials come
 * from, invented.
 */
function SourceBanner({ source }: { source: string }) {
  const t = useTranslations('settings.oauth');

  const { Icon, label, desc, tone } =
    source === 'database'
      ? { Icon: Database, label: t('sourceDatabase'), desc: t('sourceDatabaseDesc'), tone: 'bg-indigo-50 border-indigo-200 text-indigo-800' }
      : source === 'environment'
        ? { Icon: Server, label: t('sourceEnvironment'), desc: t('sourceEnvironmentDesc'), tone: 'bg-amber-50 border-amber-200 text-amber-800' }
        : { Icon: HelpCircle, label: `${t('sourceUnknown')} (${source})`, desc: t('sourceUnknownDesc'), tone: 'bg-slate-50 border-slate-200 text-slate-700' };

  return (
    <div className={`flex items-start gap-3 p-3.5 rounded-xl border ${tone}`}>
      <Icon className="w-5 h-5 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold">
          <span className="font-normal opacity-80">{t('source')}: </span>{label}
        </p>
        <p className="text-[12px] opacity-90 mt-0.5">{desc}</p>
      </div>
    </div>
  );
}

/**
 * Write the credentials.
 *
 * Mounted only once the current configuration has arrived, which is what lets
 * the two non-secret fields seed from it in a `useState` initialiser: no
 * effect, and a background refetch cannot overwrite what the admin is typing.
 * `client_secret` is seeded to `''` here and reset to `''` after every
 * successful save — the console has no plaintext secret to put there.
 *
 * Client-side validation is `oauthConfigUpdateSchema`, and one of its three
 * rules is load-bearing rather than cosmetic: the backend validates `client_id`
 * and `redirect_uri` but *not* that the secret is non-empty
 * (`oauth_config_manager.py:190-201`), so a blank field would be encrypted and
 * stored, silently replacing working credentials with nothing.
 *
 * A rejected save shows the backend's own sentence via `apiErrorText`, because
 * the 400 it raises carries `OAUTH_VALIDATION_FAILED` with a message naming
 * which check failed; collapsing that to a generic failure would leave the
 * admin with no idea which half was wrong.
 *
 * Worth being exact about what that check is, since the copy on screen has to
 * be: `validate_credentials()` fetches Google's discovery document and reads
 * nothing from the submitted credentials (`oauth_config_manager.py:239-281`).
 * It proves Google is reachable, not that these credentials are the right
 * ones. A mistyped secret saves cleanly and fails at the first login attempt.
 */
function UpdateForm({ current }: { current: OAuthConfig }) {
  const t = useTranslations('settings.oauth');
  const qc = useQueryClient();

  const [form, setForm] = useState(() => ({
    client_id: current.client_id ?? '',
    client_secret: '',
    redirect_uri: current.redirect_uri ?? '',
  }));
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const parsed = oauthConfigUpdateSchema.safeParse(form);

  const saveMut = useMutation({
    mutationFn: () => admin.updateOAuthConfig(oauthConfigUpdateSchema.parse(form)),
    onSuccess: (written) => {
      /*
       * The cache is written from the POST's own response, not from a re-read.
       *
       * `POST /oauth/config` answers with the configuration it persisted, so
       * that body is already the authoritative answer; re-asking for it costs
       * a round trip and flashes stale credentials in between for nothing.
       *
       * This used to also be a correctness fix. The endpoint committed only in
       * FastAPI's dependency teardown, which runs after the response is sent,
       * so a `GET` fired immediately after a save could still read the old row
       * (observed here: a save at 10:43:46.98 answered by a refetch carrying
       * `updated_at` 10:41:36). #312 gave the handler an explicit commit, so
       * that race is gone — seeding from the response is now purely about the
       * round trip and the flicker.
       *
       * Still invalidated, so the entry is marked stale and any later mount
       * re-reads it — just with `refetchType: 'none'`, which suppresses the
       * immediate refetch and keeps the network out of the deciding path.
       * This is the same `['oauth-config']` entry Tổng quan hệ thống reads, so
       * its Quick-Start task turns done off this write with no reload.
       */
      qc.setQueryData(OAUTH_CONFIG_KEY, written);
      qc.invalidateQueries({ queryKey: OAUTH_CONFIG_KEY, refetchType: 'none' });
      setForm((prev) => ({ ...prev, client_secret: '' }));
      setSaveError(null);
      setSaved(true);
    },
    onError: (e) => { setSaveError(apiErrorText(e)); setSaved(false); },
  });

  const edit = (patch: Partial<typeof form>) => {
    setForm((prev) => ({ ...prev, ...patch }));
    setSaved(false);
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => { e.preventDefault(); if (parsed.success) saveMut.mutate(); }}
    >
      {saveError && (
        <div className="p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{saveError}</span>
        </div>
      )}
      {saved && (
        <div className="p-2.5 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-lg text-[12px] flex items-start gap-2">
          <CheckCircle2 className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{t('saved')}</span>
        </div>
      )}

      <TextField
        id="oauth-client-id"
        label={t('clientId')}
        value={form.client_id}
        onChange={(v) => edit({ client_id: v })}
      />
      <TextField
        id="oauth-client-secret"
        label={t('clientSecretNew')}
        hint={t('clientSecretHint')}
        placeholder={t('clientSecretPlaceholder')}
        value={form.client_secret}
        onChange={(v) => edit({ client_secret: v })}
        // Never `current-password`: no password manager should be invited to
        // fill this field, and nothing about the stored secret is readable.
        type="password"
        autoComplete="new-password"
      />
      <TextField
        id="oauth-redirect-uri"
        label={t('redirectUri')}
        value={form.redirect_uri}
        onChange={(v) => edit({ redirect_uri: v })}
      />

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={saveMut.isPending || !parsed.success}
          className="h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-all flex items-center gap-1.5 shadow-sm shadow-indigo-200"
        >
          {saveMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saveMut.isPending ? t('saving') : t('save')}
        </button>
        {/* Only once something has been typed: a first-run admin should not be
            met by a validation complaint about fields they have not reached. */}
        {!parsed.success && (form.client_id || form.client_secret || form.redirect_uri) && (
          <span className="text-[11px] text-slate-400">{parsed.error.issues[0]?.message}</span>
        )}
      </div>
    </form>
  );
}

function TextField({ id, label, hint, placeholder, value, onChange, type = 'text', autoComplete }: {
  id: string;
  label: string;
  hint?: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  type?: 'text' | 'password';
  autoComplete?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-[12px] font-medium text-slate-600 mb-1.5">{label}</label>
      <input
        id={id}
        type={type}
        autoComplete={autoComplete}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400 font-mono"
      />
      {hint && <p className="text-[11px] text-slate-400 mt-1.5">{hint}</p>}
    </div>
  );
}
