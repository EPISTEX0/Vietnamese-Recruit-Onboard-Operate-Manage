/**
 * The System Admin Quick-Start Guide, derived from live data.
 *
 * ADR-0009 §4 gives the role three Essential Setup Tasks; ADR-0014 decides
 * their completion is *inferred*, never stored. There is no dismissed flag, no
 * manual tick, nothing in localStorage. The intended consequence: revoke the AI
 * key six months from now and that task comes back, because the checklist
 * describes the deployment's real state rather than what the admin once
 * clicked.
 *
 * Shaped like `lib/auth/roles.ts` — a pure module in `lib/` with no React
 * dependency and its test next to it.
 *
 * The one hard constraint: this module **takes query results from the outside**
 * and never fetches anything itself. That is not a preference. It is the only
 * reason the branch below can be tested as a plain function, and this branch is
 * the single place in the homepage that can be wrong without anyone noticing.
 *
 * The rule that shapes every decision here: a task whose data we cannot read is
 * `unknown`, never `todo`. The person reading this widget installed the system
 * minutes ago and has no context to doubt a sentence on screen. Drawing "chưa
 * tạo tài khoản HR" because `listUsers()` returned 500 sends them off to redo
 * work they already did.
 */

import type { UserRole } from '@/lib/auth/roles';

/**
 * The three tasks, in the order the admin should do them: OAuth gates login, AI
 * gates the assistant, the HR account is the handover.
 */
export const SETUP_TASK_IDS = ['googleOAuth', 'aiConfiguration', 'hrAccount'] as const;

export type SetupTaskId = (typeof SETUP_TASK_IDS)[number];

export type SetupTaskStatus = 'done' | 'todo' | 'unknown';

/**
 * Why a task is `unknown`, so the widget can render the two apart: `loading`
 * draws a skeleton ("wait"), `error` draws an undetermined mark with a retry
 * ("act"). Collapsing them leaves the admin in front of a skeleton that will
 * never resolve.
 */
export type SetupTaskUnknownReason = 'loading' | 'error';

/** Where clicking a task takes the admin. */
export interface SetupTaskAction {
  href: string;
}

/**
 * One query's outcome as the caller observed it.
 *
 * Deliberately the structural subset of `@tanstack/react-query`'s
 * `UseQueryResult` that this module reads, so the page hands its query objects
 * straight in and the test hands in three-field literals.
 */
export interface QueryResult<T> {
  status: 'pending' | 'error' | 'success';
  data?: T | undefined;
  error?: unknown;
}

/*
 * The payload fields each task actually reads — nothing more.
 *
 * Structural rather than the full `OAuthConfig` / `OrganizationAIConfiguration`
 * / `AdminUser` imports: the real API types are assignable to these, so the page
 * loses no type safety, while the test does not have to mint thirty irrelevant
 * fields to state "AI is configured".
 */

/** `getOAuthConfig()` — the endpoint always answers 200 (see `oauthTaskDone`). */
export interface OAuthConfigFields {
  client_id: string;
}

/** `getOrganizationAIConfiguration()` — carries an explicit `configured` flag. */
export interface AIConfigurationFields {
  configured: boolean;
}

/** `listUsers()` — one entry per provisioned account. */
export interface UserAccountFields {
  role: string;
}

export interface SetupGuideSources {
  oauthConfig: QueryResult<OAuthConfigFields>;
  aiConfiguration: QueryResult<AIConfigurationFields>;
  users: QueryResult<readonly UserAccountFields[]>;
}

export interface SetupTaskView {
  id: SetupTaskId;
  status: SetupTaskStatus;
  /** Set only while `status` is `unknown`; `null` otherwise. */
  unknownReason: SetupTaskUnknownReason | null;
  /** Always present — see `TASK_ACTIONS` for why that is a real guarantee. */
  action: SetupTaskAction;
}

export interface SetupProgress {
  done: number;
  total: number;
}

export interface SetupGuideView {
  /**
   * Whether to render the widget at all. The checklist is the opening phase's
   * overlay, not permanent homepage content — leaving it on screen at 3/3
   * creates a second empty state on the day it is finally satisfied.
   */
  visible: boolean;
  tasks: readonly SetupTaskView[];
  /**
   * `null` until every task has resolved, so the first paint cannot read 0/3
   * and snap to 3/3 a moment later — the same lie as `todo`, just shorter.
   */
  progress: SetupProgress | null;
}

/**
 * Where each task sends the admin. Total over `SetupTaskId`, deliberately.
 *
 * Module-private: it was exported so a test could inject a route map, and #314
 * removed the parameter that took one. `SetupTaskAction` stays exported because
 * `SetupTaskView.action` is part of what callers read.
 */
type SetupTaskActions = Readonly<Record<SetupTaskId, SetupTaskAction>>;

/**
 * The console's route map, and the module's one invariant about navigation:
 * **every task in `SETUP_TASK_IDS` has a destination**.
 *
 * `googleOAuth` was `null` from #302 until #307, when `/settings/oauth` gave it
 * a real one. The nullable arm outlived that by one ticket. It was justified as
 * the guard that stops a row pointing somewhere that cannot do the job, but it
 * never was one: a wrong `href` typechecks and renders as a link just the same,
 * so the only case it caught was someone deliberately writing `null` (#314).
 *
 * Totality catches more, and earlier. Adding an id to `SETUP_TASK_IDS` without
 * a route here is a `tsc` error on this object — where the omission is — rather
 * than a row that renders dead at runtime and is read as a checklist item
 * nobody can act on.
 *
 * So a task with nowhere to go is no longer expressible, and that is the point.
 * The day one exists, that is a design decision to reopen — pointing it at a
 * nearby section to round the count up is the lie this page exists to prevent,
 * and so is drawing it as an un-clickable line and calling it handled.
 */
const TASK_ACTIONS: SetupTaskActions = {
  googleOAuth: { href: '/settings/oauth' },
  aiConfiguration: { href: '/settings/ai' },
  hrAccount: { href: '/settings/users' },
};

/**
 * A task's completion test, given the payload its query returned.
 *
 * `null` means "this payload does not answer the question" — which resolves to
 * `unknown`, never `todo`. Every reader below is written so that anything it
 * does not positively recognise falls into that hole rather than out the
 * `todo` side.
 */
type CompletionReader<T> = (data: T) => boolean | null;

/**
 * `GET /api/system-admin/oauth/config` always answers 200: it returns the
 * database configuration if one exists and otherwise falls back to the
 * environment's. So "not configured" can only ever surface as an empty client
 * id, and an HTTP failure is a genuine failure rather than a missing config —
 * which is exactly why those two must not collapse into one status.
 */
const oauthTaskDone: CompletionReader<OAuthConfigFields> = (config) =>
  typeof config?.client_id === 'string' ? config.client_id.trim().length > 0 : null;

/**
 * Read the explicit flag, never infer from `provider`/`model` being non-empty:
 * a half-finished AI section carries both with no working key behind them, and
 * guessing from the strings would call that done.
 */
const aiTaskDone: CompletionReader<AIConfigurationFields> = (config) =>
  typeof config?.configured === 'boolean' ? config.configured : null;

/**
 * Typed against `UserRole` rather than left as a bare `'hr'` literal: this
 * comparison is what decides whether the handover task is done, and if the role
 * is ever renamed in `lib/auth/roles.ts` the rename has to fail here at compile
 * time rather than turn the task permanently `todo` in silence.
 */
const HR_ROLE: UserRole = 'hr';

/** The deployment has handed over once any account carries the HR role. */
const hrTaskDone: CompletionReader<readonly UserAccountFields[]> = (users) =>
  Array.isArray(users) ? users.some((user) => user?.role === HR_ROLE) : null;

function resolveTask<T>(
  id: SetupTaskId,
  result: QueryResult<T>,
  isDone: CompletionReader<T>,
): SetupTaskView {
  const view = (status: SetupTaskStatus, unknownReason: SetupTaskUnknownReason | null = null) => ({
    id,
    status,
    unknownReason,
    action: TASK_ACTIONS[id],
  });

  // Error first, before any look at `data`. React Query keeps the last good
  // payload alongside `error` when a background refetch fails, and reading that
  // stale answer would quietly turn a live outage into a confident screen.
  if (result.status === 'error') return view('unknown', 'error');
  if (result.status === 'pending') return view('unknown', 'loading');

  const data = result.data;
  // A settled query with nothing in it should not happen through React Query,
  // but the direction it falls matters more than its likelihood.
  if (data == null) return view('unknown', 'error');

  const done = isDone(data);
  if (done == null) return view('unknown', 'error');
  return view(done ? 'done' : 'todo');
}

/**
 * Build the widget's entire view-model. The component that renders it makes no
 * decisions of its own — it draws what comes back from here.
 *
 * Takes `sources` alone. #307 gave the route map a second parameter so a test
 * could inject a task with no destination; #314 made that state unrepresentable
 * instead, which leaves nothing for the parameter to say.
 */
export function buildSetupGuide(sources: SetupGuideSources): SetupGuideView {
  const tasks: readonly SetupTaskView[] = [
    resolveTask('googleOAuth', sources.oauthConfig, oauthTaskDone),
    resolveTask('aiConfiguration', sources.aiConfiguration, aiTaskDone),
    resolveTask('hrAccount', sources.users, hrTaskDone),
  ];

  const allResolved = tasks.every((task) => task.status !== 'unknown');

  return {
    visible: !tasks.every((task) => task.status === 'done'),
    tasks,
    progress: allResolved
      ? { done: tasks.filter((task) => task.status === 'done').length, total: tasks.length }
      : null,
  };
}
