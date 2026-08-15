/**
 * The one place in the Tổng quan hệ thống homepage that can be wrong without
 * anyone noticing.
 *
 * Every branch here exists to defend a single property: an unreadable answer
 * must never be drawn as "chưa làm". The reader of this widget is someone who
 * finished installing the deployment minutes ago and has no context to doubt a
 * sentence on screen — telling them "chưa tạo tài khoản HR" because
 * `listUsers()` returned 500 sends them off to redo work they already did.
 *
 * These tests only ever look at the returned view-model. They never assert how
 * a status was computed, only what the widget would show — so re-deriving a
 * task from a different field of the same response leaves them green.
 */

import { describe, it, expect } from 'vitest';

import {
  buildSetupGuide,
  SETUP_TASK_IDS,
  type QueryResult,
  type SetupGuideSources,
  type SetupGuideView,
  type SetupTaskId,
} from './setup-guide';

// --- input builders --------------------------------------------------------
//
// Named for the situation they describe, not for the payload they carry, so a
// test below reads as the deployment state it is about.

const settled = <T>(data: T): QueryResult<T> => ({ status: 'success', data });
const loading = <T>(): QueryResult<T> => ({ status: 'pending' });
const failed = <T>(error: unknown = new Error('HTTP 500')): QueryResult<T> => ({
  status: 'error',
  error,
});

const oauthConfigured = () => settled({ client_id: '1234.apps.googleusercontent.com' });
const oauthUnconfigured = () => settled({ client_id: '' });
const aiConfigured = () => settled({ configured: true });
const aiUnconfigured = () => settled({ configured: false });
const staffWithHr = () => settled([{ role: 'system_admin' }, { role: 'hr' }]);
const staffWithoutHr = () => settled([{ role: 'system_admin' }, { role: 'user' }]);

/** A deployment where all three setup tasks are genuinely finished. */
const everythingDone = (): SetupGuideSources => ({
  oauthConfig: oauthConfigured(),
  aiConfiguration: aiConfigured(),
  users: staffWithHr(),
});

/** A deployment that has just finished first-run setup — nothing configured. */
const nothingDone = (): SetupGuideSources => ({
  oauthConfig: oauthUnconfigured(),
  aiConfiguration: aiUnconfigured(),
  users: staffWithoutHr(),
});

const taskOf = (view: SetupGuideView, id: SetupTaskId) => {
  const found = view.tasks.find((t) => t.id === id);
  if (!found) throw new Error(`view-model carries no task "${id}"`);
  return found;
};

/**
 * Replace exactly one source, leaving the other two settled.
 *
 * Lets a test isolate one task's reaction to loading/error without the other
 * two dragging `progress` and `visible` around with it.
 */
const withSource = (
  base: SetupGuideSources,
  key: keyof SetupGuideSources,
  result: QueryResult<never>,
): SetupGuideSources => ({ ...base, [key]: result });

/** Which source each task reads, so the loading/error sweeps can be table-driven. */
const SOURCE_OF_TASK: Record<SetupTaskId, keyof SetupGuideSources> = {
  googleOAuth: 'oauthConfig',
  aiConfiguration: 'aiConfiguration',
  hrAccount: 'users',
};

// --- the three tasks -------------------------------------------------------

describe('the Essential Setup Tasks', () => {
  it('are the three of ADR-0009 §4, in that order', () => {
    // Order is the order the admin should do them in, not an implementation
    // detail: OAuth gates login, AI gates the assistant, the HR account is the
    // handover. A reshuffle here is a product change, not a refactor.
    expect(SETUP_TASK_IDS).toEqual(['googleOAuth', 'aiConfiguration', 'hrAccount']);
  });

  it('appear in the view-model in that same order, once each', () => {
    const view = buildSetupGuide(nothingDone());

    expect(view.tasks.map((t) => t.id)).toEqual([...SETUP_TASK_IDS]);
  });
});

// --- action: navigable vs guidance-only ------------------------------------

describe('task actions', () => {
  it('sends the AI and HR tasks to the console section that does the work', () => {
    const view = buildSetupGuide(nothingDone());

    expect(taskOf(view, 'aiConfiguration').action).toEqual({ href: '/settings/ai' });
    expect(taskOf(view, 'hrAccount').action).toEqual({ href: '/settings/users' });
  });

  it('gives the Google OAuth task no destination, because the console has none', () => {
    // Not an oversight: there is no OAuth configuration screen anywhere in the
    // app (#307 is where one gets decided). A placeholder href pointing at some
    // other section would be exactly the lie this whole page exists to prevent.
    const view = buildSetupGuide(nothingDone());

    expect(taskOf(view, 'googleOAuth').action).toBeNull();
  });

  it('keeps the action fixed regardless of how the task resolved', () => {
    // Where a task sends you is a property of the console's shape, not of the
    // deployment's data — so it must not wobble as queries settle.
    const done = buildSetupGuide(everythingDone());
    const pending = buildSetupGuide({
      oauthConfig: loading(),
      aiConfiguration: loading(),
      users: loading(),
    });

    for (const id of SETUP_TASK_IDS) {
      expect(taskOf(done, id).action, id).toEqual(taskOf(pending, id).action);
    }
  });
});

// --- done / todo -----------------------------------------------------------

describe('resolving a task against live data', () => {
  it('marks every task done on a fully configured deployment', () => {
    const view = buildSetupGuide(everythingDone());

    expect(view.tasks.map((t) => t.status)).toEqual(['done', 'done', 'done']);
  });

  it('marks every task todo on a freshly installed deployment', () => {
    const view = buildSetupGuide(nothingDone());

    expect(view.tasks.map((t) => t.status)).toEqual(['todo', 'todo', 'todo']);
  });

  it('reads Google OAuth as unconfigured from an empty client id', () => {
    // The endpoint always answers 200 and falls back to the environment
    // configuration, so "not configured" can only show up as an empty id.
    const view = buildSetupGuide({ ...everythingDone(), oauthConfig: oauthUnconfigured() });

    expect(taskOf(view, 'googleOAuth').status).toBe('todo');
  });

  it('does not mistake a whitespace-only client id for a configured one', () => {
    const view = buildSetupGuide({
      ...everythingDone(),
      oauthConfig: settled({ client_id: '   ' }),
    });

    expect(taskOf(view, 'googleOAuth').status).toBe('todo');
  });

  it('reads the AI task from the explicit configured flag', () => {
    const configured = buildSetupGuide({ ...nothingDone(), aiConfiguration: aiConfigured() });
    const unconfigured = buildSetupGuide({ ...everythingDone(), aiConfiguration: aiUnconfigured() });

    expect(taskOf(configured, 'aiConfiguration').status).toBe('done');
    expect(taskOf(unconfigured, 'aiConfiguration').status).toBe('todo');
  });

  it('treats an AI provider that is filled in but not configured as todo', () => {
    // A half-finished AI section carries a provider and model with no working
    // key. Guessing "done" from a non-empty string would call that finished.
    const view = buildSetupGuide({
      ...everythingDone(),
      aiConfiguration: settled({ configured: false, provider: 'openai', model: 'gpt-4o' }),
    });

    expect(taskOf(view, 'aiConfiguration').status).toBe('todo');
  });

  it('marks the HR task done as soon as one account carries the HR role', () => {
    const view = buildSetupGuide({ ...nothingDone(), users: staffWithHr() });

    expect(taskOf(view, 'hrAccount').status).toBe('done');
  });

  it('marks the HR task todo when the deployment has only its system admin', () => {
    const view = buildSetupGuide({
      ...everythingDone(),
      users: settled([{ role: 'system_admin' }]),
    });

    expect(taskOf(view, 'hrAccount').status).toBe('todo');
  });

  it('marks the HR task todo on an empty user list', () => {
    const view = buildSetupGuide({ ...everythingDone(), users: settled([]) });

    expect(taskOf(view, 'hrAccount').status).toBe('todo');
  });
});

// --- unknown: the property the whole module exists for ----------------------

describe('a task whose data cannot be read', () => {
  for (const id of SETUP_TASK_IDS) {
    it(`reports "${id}" as unknown while its query is still running`, () => {
      const view = buildSetupGuide(withSource(everythingDone(), SOURCE_OF_TASK[id], loading()));
      const task = taskOf(view, id);

      expect(task.status).toBe('unknown');
      expect(task.status).not.toBe('todo');
    });

    it(`reports "${id}" as unknown when its query failed`, () => {
      const view = buildSetupGuide(withSource(everythingDone(), SOURCE_OF_TASK[id], failed()));
      const task = taskOf(view, id);

      expect(task.status).toBe('unknown');
      expect(task.status).not.toBe('todo');
    });

    it(`tells loading apart from failure for "${id}"`, () => {
      // The two render differently — a skeleton says "wait", an undetermined
      // mark with a retry says "act". Collapsing them strands the admin in
      // front of a skeleton that will never resolve.
      const whileLoading = buildSetupGuide(
        withSource(everythingDone(), SOURCE_OF_TASK[id], loading()),
      );
      const whenFailed = buildSetupGuide(withSource(everythingDone(), SOURCE_OF_TASK[id], failed()));

      expect(taskOf(whileLoading, id).unknownReason).toBe('loading');
      expect(taskOf(whenFailed, id).unknownReason).toBe('error');
    });
  }

  it('carries no reason on a task that did resolve', () => {
    const view = buildSetupGuide(everythingDone());

    for (const task of view.tasks) {
      expect(task.unknownReason, task.id).toBeNull();
    }
  });

  it('separates an empty client id from a failed OAuth read', () => {
    // Both are "no usable client id", and the endpoint never 404s — so this
    // pair is the only thing standing between a real 500 and the screen
    // telling a fresh admin to go configure OAuth they already configured.
    const unconfigured = buildSetupGuide({ ...everythingDone(), oauthConfig: oauthUnconfigured() });
    const errored = buildSetupGuide({ ...everythingDone(), oauthConfig: failed() });

    expect(taskOf(unconfigured, 'googleOAuth').status).toBe('todo');
    expect(taskOf(errored, 'googleOAuth').status).toBe('unknown');
  });

  it('separates a missing HR account from a failed user list read', () => {
    const noHr = buildSetupGuide({ ...everythingDone(), users: staffWithoutHr() });
    const errored = buildSetupGuide({ ...everythingDone(), users: failed() });

    expect(taskOf(noHr, 'hrAccount').status).toBe('todo');
    expect(taskOf(errored, 'hrAccount').status).toBe('unknown');
  });

  it('stays unknown on a failed refetch that still carries the previous answer', () => {
    // React Query keeps the last good `data` alongside `error` when a
    // background refetch fails. Reading the stale answer would quietly turn a
    // live outage into a confident screen.
    const view = buildSetupGuide({
      ...everythingDone(),
      users: { status: 'error', data: [{ role: 'hr' }], error: new Error('HTTP 503') },
    });

    expect(taskOf(view, 'hrAccount').status).toBe('unknown');
  });

  for (const missing of [undefined, null]) {
    it(`refuses to read a settled query whose data is ${missing} as todo`, () => {
      // Should not happen through React Query, but the fallthrough direction is
      // what matters: an answer we cannot read is never "chưa làm".
      const view = buildSetupGuide({
        ...everythingDone(),
        aiConfiguration: { status: 'success', data: missing as never },
      });

      expect(taskOf(view, 'aiConfiguration').status).toBe('unknown');
    });
  }

  it('refuses to read a malformed payload as todo', () => {
    const view = buildSetupGuide({
      oauthConfig: settled({ client_id: null as never }),
      aiConfiguration: settled({ configured: 'yes' as never }),
      users: settled({ length: 0 } as never),
    });

    expect(view.tasks.map((t) => t.status)).toEqual(['unknown', 'unknown', 'unknown']);
  });
});

// --- progress --------------------------------------------------------------

describe('overall progress', () => {
  it('is withheld until every task has resolved', () => {
    // Otherwise the first paint reads 0/3 and snaps to 3/3 a moment later,
    // which is the same lie as `todo`, just shorter-lived.
    const view = buildSetupGuide(withSource(everythingDone(), 'users', loading()));

    expect(view.progress).toBeNull();
  });

  it('is withheld when a task failed, not just when one is loading', () => {
    const view = buildSetupGuide(withSource(everythingDone(), 'users', failed()));

    expect(view.progress).toBeNull();
  });

  it('counts the finished tasks once all three have resolved', () => {
    const view = buildSetupGuide({ ...nothingDone(), aiConfiguration: aiConfigured() });

    expect(view.progress).toEqual({ done: 1, total: 3 });
  });

  it('reads 0/3 on a freshly installed deployment', () => {
    const view = buildSetupGuide(nothingDone());

    expect(view.progress).toEqual({ done: 0, total: 3 });
  });

  it('reads 3/3 once everything is configured', () => {
    const view = buildSetupGuide(everythingDone());

    expect(view.progress).toEqual({ done: 3, total: 3 });
  });
});

// --- visibility ------------------------------------------------------------

describe('whether the widget shows at all', () => {
  it('retires once all three tasks are done', () => {
    // The checklist is the opening phase's overlay, not permanent homepage
    // content. Leaving it at 3/3 forever is the second empty state.
    expect(buildSetupGuide(everythingDone()).visible).toBe(false);
  });

  it('stays up on a freshly installed deployment', () => {
    expect(buildSetupGuide(nothingDone()).visible).toBe(true);
  });

  it('stays up while the answers are still loading', () => {
    const view = buildSetupGuide({
      oauthConfig: loading(),
      aiConfiguration: loading(),
      users: loading(),
    });

    expect(view.visible).toBe(true);
  });

  it('stays up when a single task is unknown and the rest are done', () => {
    // Hiding on "two done, one unreadable" would silently drop a task the
    // admin may still owe the deployment.
    const view = buildSetupGuide(withSource(everythingDone(), 'oauthConfig', failed()));

    expect(view.visible).toBe(true);
  });

  it('stays up when one task is still todo', () => {
    const view = buildSetupGuide({ ...everythingDone(), aiConfiguration: aiUnconfigured() });

    expect(view.visible).toBe(true);
  });
});
