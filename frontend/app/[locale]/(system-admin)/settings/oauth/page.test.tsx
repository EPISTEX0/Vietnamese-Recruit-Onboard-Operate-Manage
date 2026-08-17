/**
 * @vitest-environment jsdom
 *
 * Cấu hình Google OAuth — the console's only surface that touches a client
 * secret, and the only one that can tell an admin which of two configurations
 * their deployment is actually running.
 *
 * Two properties carry this file. Everything else here is ordinary rendering.
 *
 * 1. **No plaintext secret, on any path.** The backend only ever sends
 *    `client_secret_masked`, so the way plaintext could appear is the console
 *    inventing it: seeding the "new secret" field from the masked value, or
 *    keeping a typed secret in state after it has been sent. Worse than a leak
 *    in the first case — a form seeded with `****abcd` and submitted would
 *    store those asterisks as the real secret and break every future login.
 * 2. **`source` is drawn, and drawn honestly.** The database configuration
 *    overrides the environment one, the two can hold different values, and
 *    nothing else in the deployment says which is in effect. An unrecognised
 *    source must not be rounded to either known one.
 *
 * Written as renders driving the real chain — component → `lib/api/admin` →
 * `fetch` → React Query — with only `fetch` faked, for the reason
 * `../error-states.test.tsx` gives: the defects being guarded against are
 * missing branches and missing wiring, and a unit test over some extracted
 * helper stays green the day the page stops calling it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { formats } from '@/i18n/request';

import OAuthConfigPage from './page';

const t = messages.settings.oauth;

/** A configuration served from the database — the overriding one. */
const FROM_DATABASE = {
  client_id: '1234-db.apps.googleusercontent.com',
  client_secret_masked: '****cd12',
  redirect_uri: 'https://vroom.test/api/auth/callback',
  updated_at: '2026-01-02T03:04:05Z',
  source: 'database',
};

/** The fallback: no database row, so the deployment runs its env vars. */
const FROM_ENVIRONMENT = {
  client_id: '9999-env.apps.googleusercontent.com',
  client_secret_masked: '****ef34',
  redirect_uri: 'https://vroom.test/api/auth/callback',
  updated_at: null,
  source: 'environment',
};

/** The sentence a 500 puts in `detail.message`, echoed by `apiErrorText`. */
const FAILURE_DETAIL = 'Lỗi máy chủ khi đọc cấu hình OAuth';

/** What `POST /oauth/config` answers with when Google validation fails. */
const VALIDATION_DETAIL = 'Could not verify credentials with Google';

const json = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

type Call = { url: string; init?: RequestInit };

/**
 * A `fetch` that answers GETs and POSTs separately and records every call, so a
 * test can assert on the body that left the browser rather than on the props of
 * some intermediate.
 */
function stubFetch(handlers: {
  get: () => Response;
  post?: (body: Record<string, unknown>) => Response;
}) {
  const calls: Call[] = [];
  const fake = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      return handlers.post?.(body) ?? json({});
    }
    return handlers.get();
  });
  vi.stubGlobal('fetch', fake);
  return {
    calls,
    gets: () => calls.filter((c) => c.init?.method !== 'POST'),
    posts: () => calls.filter((c) => c.init?.method === 'POST'),
    bodies: () =>
      calls
        .filter((c) => c.init?.method === 'POST')
        .map((c) => JSON.parse(String(c.init!.body)) as Record<string, unknown>),
  };
}

let queryClient: QueryClient;

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages} formats={formats}>
        <OAuthConfigPage />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

/** The secret input, found by its label rather than by DOM position. */
const secretInput = () => screen.getByLabelText(t.clientSecretNew) as HTMLInputElement;

beforeEach(() => {
  queryClient = new QueryClient({
    // A retrying query would strand the page on its loading branch and every
    // assertion below would time out rather than fail honestly.
    defaultOptions: { queries: { retry: false } },
  });
});

afterEach(() => {
  // `onlineManager` is module-global: a case that goes offline would leave
  // every later query paused.
  onlineManager.setOnline(true);
  vi.unstubAllGlobals();
  queryClient.clear();
});

// --- the client secret -----------------------------------------------------

describe('the client secret', () => {
  it('never prefills the new-secret field, not even with the masked value', async () => {
    // The masked value is the only secret-shaped string the console holds, and
    // seeding the field with it would submit `****cd12` as the real secret on
    // the next save — replacing working credentials with asterisks.
    stubFetch({ get: () => json(FROM_DATABASE) });

    renderPage();

    await screen.findByText(FROM_DATABASE.client_secret_masked);
    expect(secretInput().value).toBe('');
  });

  it('seeds the two non-secret fields from the running configuration', async () => {
    // The counterweight to the assertion above: "nothing is prefilled" would
    // also pass it, and would make every edit a retype of all three fields.
    stubFetch({ get: () => json(FROM_DATABASE) });

    renderPage();

    const clientId = (await screen.findByLabelText(t.clientId)) as HTMLInputElement;
    const redirect = screen.getByLabelText(t.redirectUri) as HTMLInputElement;
    expect(clientId.value).toBe(FROM_DATABASE.client_id);
    expect(redirect.value).toBe(FROM_DATABASE.redirect_uri);
  });

  it('sends the typed secret and keeps nothing of it once saved', async () => {
    const typed = 'GOCSPX-real-secret-value';
    const stub = stubFetch({
      get: () => json(FROM_DATABASE),
      post: () => json({ ...FROM_DATABASE, client_secret_masked: '****alue' }),
    });

    renderPage();
    await screen.findByLabelText(t.clientSecretNew);

    fireEvent.change(secretInput(), { target: { value: typed } });
    fireEvent.click(screen.getByRole('button', { name: t.save }));

    await waitFor(() => expect(stub.bodies()).toHaveLength(1));
    expect(stub.bodies()[0]!.client_secret).toBe(typed);

    // Cleared after the round trip, so a shared screen or a stray screenshot
    // is not still showing it.
    await waitFor(() => expect(secretInput().value).toBe(''));
    expect(document.body.textContent).not.toContain(typed);
  });

  it('renders the secret only in the masked form the backend sent', async () => {
    stubFetch({ get: () => json(FROM_DATABASE) });

    renderPage();

    expect(await screen.findByText(FROM_DATABASE.client_secret_masked)).toBeTruthy();
    // No reveal affordance can exist: the console has no unmasked form of this
    // value to reveal, so a toggle could only ever show the asterisks again.
    expect(secretInput().getAttribute('type')).toBe('password');
  });
});

// --- source: the point of the screen ---------------------------------------

describe('which configuration is in effect', () => {
  it('says so when the deployment runs the database configuration', async () => {
    stubFetch({ get: () => json(FROM_DATABASE) });

    renderPage();

    expect(await screen.findByText(t.sourceDatabase)).toBeTruthy();
    expect(screen.getByText(t.sourceDatabaseDesc)).toBeTruthy();
    expect(screen.queryByText(t.sourceEnvironmentDesc)).toBeNull();
  });

  it('says so when the deployment runs its environment variables', async () => {
    // The case that has no other surface anywhere: an admin editing
    // `GOOGLE_CLIENT_ID` in the env file has, until this screen, no way to
    // learn whether a database row is quietly overriding it.
    stubFetch({ get: () => json(FROM_ENVIRONMENT) });

    renderPage();

    expect(await screen.findByText(t.sourceEnvironment)).toBeTruthy();
    expect(screen.getByText(t.sourceEnvironmentDesc)).toBeTruthy();
    expect(screen.queryByText(t.sourceDatabaseDesc)).toBeNull();
  });

  it('refuses to guess when the backend names a source it does not know', async () => {
    // Rounding an unknown source to either known one would be a confident
    // sentence about where the running credentials come from, invented.
    stubFetch({ get: () => json({ ...FROM_DATABASE, source: 'vault' }) });

    renderPage();

    expect(await screen.findByText(t.sourceUnknownDesc)).toBeTruthy();
    expect(screen.queryByText(t.sourceDatabaseDesc)).toBeNull();
    expect(screen.queryByText(t.sourceEnvironmentDesc)).toBeNull();
  });
});

// --- the three query states, in the order #305 fixed -----------------------

describe('reading the current configuration', () => {
  it('shows the retryable error box, and never the not-configured sentence, when the read fails', async () => {
    stubFetch({ get: () => json({ detail: { message: FAILURE_DETAIL } }, 500) });

    renderPage();

    expect(await screen.findByText(FAILURE_DETAIL)).toBeTruthy();
    expect(screen.getByRole('button', { name: messages.settings.retry })).toBeTruthy();
    // "There is no client ID" is a claim about the deployment. A failed read
    // supports no claim at all — and this one sends an admin off to re-enter
    // credentials that are already in place.
    expect(screen.queryByText(t.notConfigured)).toBeNull();
  });

  it('offers no save form while the current configuration is unreadable', async () => {
    // The form's two non-secret fields seed from the answer we did not get, so
    // an admin overwriting credentials here would be typing over values nobody
    // could read back.
    //
    // Seeded with a stale answer on purpose. Gating the form on `data` alone
    // would pass on a cold failure — there is nothing to seed from — and still
    // draw the form over the last good payload React Query keeps beside
    // `error`, which is the case that actually reaches a user: a form
    // pre-filled from credentials that may already have been replaced.
    queryClient.setQueryData(['oauth-config'], FROM_DATABASE, { updatedAt: 1 });
    stubFetch({ get: () => json({ detail: { message: FAILURE_DETAIL } }, 500) });

    renderPage();

    await screen.findByText(FAILURE_DETAIL);
    await waitFor(() => {
      expect(screen.queryByLabelText(t.clientSecretNew)).toBeNull();
    });
  });

  it('offers no save form on a cold read failure either', async () => {
    stubFetch({ get: () => json({ detail: { message: FAILURE_DETAIL } }, 500) });

    renderPage();

    await screen.findByText(FAILURE_DETAIL);
    expect(screen.queryByLabelText(t.clientSecretNew)).toBeNull();
  });

  it('still says not-configured when the read succeeds with an empty client id', async () => {
    // The other direction: turning "nothing is set up yet" into an error is
    // the same defect wearing the opposite sign, and this is what a freshly
    // installed deployment looks like.
    stubFetch({ get: () => json({ ...FROM_ENVIRONMENT, client_id: '', client_secret_masked: '' }) });

    renderPage();

    expect(await screen.findByText(t.notConfigured)).toBeTruthy();
    expect(screen.queryByRole('button', { name: messages.settings.retry })).toBeNull();
    // The source still has to be readable — "you are running the environment
    // and it is blank" is precisely the state a first-run admin is in.
    expect(screen.getByText(t.sourceEnvironmentDesc)).toBeTruthy();
    // And the form is how they get out of it.
    expect(screen.getByLabelText(t.clientSecretNew)).toBeTruthy();
  });

  it('claims nothing while the query is paused offline', async () => {
    // The third way `data` comes back undefined, and the one `isLoading`
    // misses: React Query pauses rather than fires when the browser is
    // offline, leaving `status: 'pending'` with `fetchStatus: 'paused'`.
    onlineManager.setOnline(false);
    const stub = stubFetch({ get: () => json(FROM_DATABASE) });

    renderPage();

    await waitFor(() => {
      expect(screen.queryByText(t.notConfigured)).toBeNull();
    });
    expect(screen.queryByText(t.sourceEnvironmentDesc)).toBeNull();
    expect(screen.queryByText(t.sourceDatabaseDesc)).toBeNull();
    // Guards the assertions above against passing because a request quietly
    // succeeded instead of because the paused branch was read.
    expect(stub.gets()).toHaveLength(0);
  });

  it('replaces the last good answer with the error box when a refetch fails', async () => {
    // React Query keeps `data` alongside `error` when a background refetch
    // fails, so a branch reading `data` first would keep presenting
    // credentials that are no longer known to be the ones in effect.
    queryClient.setQueryData(['oauth-config'], FROM_DATABASE, { updatedAt: 1 });
    stubFetch({ get: () => json({ detail: { message: FAILURE_DETAIL } }, 500) });

    renderPage();

    expect(await screen.findByText(FAILURE_DETAIL)).toBeTruthy();
    await waitFor(() => {
      expect(screen.queryByText(FROM_DATABASE.client_id)).toBeNull();
    });
  });
});

// --- saving ----------------------------------------------------------------

describe('saving new credentials', () => {
  const fill = () => {
    fireEvent.change(screen.getByLabelText(t.clientId), {
      target: { value: '5555-new.apps.googleusercontent.com' },
    });
    fireEvent.change(secretInput(), { target: { value: 'GOCSPX-new-secret' } });
    fireEvent.change(screen.getByLabelText(t.redirectUri), {
      target: { value: 'https://vroom.test/api/auth/callback' },
    });
  };

  it('shows the backend sentence when the credentials are rejected', async () => {
    // The 400 carries `OAUTH_VALIDATION_FAILED` with a message naming which
    // check failed — here, that Google's discovery document could not be
    // fetched. Collapsing it into a generic failure would leave the admin
    // guessing whether their credentials or their network were the problem.
    stubFetch({
      get: () => json(FROM_ENVIRONMENT),
      post: () => json({ detail: { code: 'OAUTH_VALIDATION_FAILED', message: VALIDATION_DETAIL } }, 400),
    });

    renderPage();
    await screen.findByLabelText(t.clientSecretNew);
    fill();
    fireEvent.click(screen.getByRole('button', { name: t.save }));

    expect(await screen.findByText(VALIDATION_DETAIL)).toBeTruthy();
    expect(screen.queryByText(t.saved)).toBeNull();
  });

  it('shows what was written even when a re-read would still serve the old row', async () => {
    // The deployment loses this race in practice: a save that had already
    // landed in `oauth_configs` was followed by a `GET` returning the previous
    // row, and only the read after that was correct. So the stub keeps serving
    // the pre-save answer on every `GET` — a page that decides from a refetch
    // flips to "database" and then back to "environment", telling the admin
    // their save did not take.
    const stub = stubFetch({
      get: () => json(FROM_ENVIRONMENT),
      post: () => json({ ...FROM_DATABASE, client_id: '5555-new.apps.googleusercontent.com' }),
    });

    renderPage();
    await screen.findByText(t.sourceEnvironmentDesc);
    fill();
    fireEvent.click(screen.getByRole('button', { name: t.save }));

    expect(await screen.findByText(t.sourceDatabaseDesc)).toBeTruthy();
    expect(await screen.findByText(t.saved)).toBeTruthy();
    // And it stays: no refetch is allowed to overwrite the write's own answer.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByText(t.sourceDatabaseDesc)).toBeTruthy();
    expect(screen.queryByText(t.sourceEnvironmentDesc)).toBeNull();
    expect(stub.gets()).toHaveLength(1);
  });

  it('leaves the cache entry the homepage checklist reads holding the new configuration', async () => {
    // `['oauth-config']` is the same entry Tổng quan hệ thống builds its
    // Quick-Start guide from, and updating it is what turns the OAuth task
    // from "Chưa làm" to "Đã xong" with no reload. Asserted on the cache
    // rather than through a second render because the two surfaces meet
    // exactly here.
    const saved = { ...FROM_DATABASE, client_id: '5555-new.apps.googleusercontent.com' };
    stubFetch({ get: () => json({ ...FROM_ENVIRONMENT, client_id: '' }), post: () => json(saved) });

    renderPage();
    await screen.findByText(t.notConfigured);
    fill();
    fireEvent.click(screen.getByRole('button', { name: t.save }));

    await waitFor(() => {
      expect(queryClient.getQueryData(['oauth-config'])).toEqual(saved);
    });
    // Marked stale all the same, so a later mount re-reads it once the write
    // is certain to be visible.
    expect(queryClient.getQueryState(['oauth-config'])?.isInvalidated).toBe(true);
  });

  it('refuses to send a blank secret over a working one', async () => {
    // The backend validates `client_id` and `redirect_uri` but not that the
    // secret is non-empty — it would encrypt and store `''`, leaving a
    // configuration that looks complete on screen and fails every login. This
    // form is the only thing standing in front of that.
    const stub = stubFetch({ get: () => json(FROM_DATABASE), post: () => json(FROM_DATABASE) });

    renderPage();
    await screen.findByLabelText(t.clientSecretNew);

    fireEvent.click(screen.getByRole('button', { name: t.save }));

    await waitFor(() => expect(screen.getByRole('button', { name: t.save })).toBeTruthy());
    expect(stub.posts()).toHaveLength(0);
  });
});
