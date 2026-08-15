/**
 * @vitest-environment jsdom
 *
 * Document status reaches the screen in the reader's language, at both places
 * this page prints it.
 *
 * `page.tsx` shipped two components called `StatusBadge`: one nested inside
 * `KnowledgeBasePage` that reads `t('statusPending')`, and one at module level
 * with `'Pending'` / `'Processing'` / `'Ready'` / `'Error'` written into it.
 * Both render sites — `DocumentRow` and `DetailModal` — are top-level siblings
 * of `KnowledgeBasePage` rather than nested in it, so neither can see the
 * nested definition and both resolve to the hardcoded one. The translated
 * badge was dead code and the whole page printed English on a product whose
 * default locale is `vi` (#318).
 *
 * Two properties have to hold together, and each catches what the other
 * cannot:
 *
 * - The `vi` half fails on the defect as shipped. It is the reason this file
 *   exists, and it is red on the tree before the fix.
 * - The `en` half is green either way today, because the hardcoded strings
 *   happen to match `en.json` word for word — which is precisely why the bug
 *   survived. It is here so the fix cannot be "hardcode Vietnamese instead":
 *   the two halves can only pass together if the label comes from the
 *   catalogue for the active locale rather than from a literal.
 *
 * Expected labels are read out of the message files rather than typed in, so a
 * copy change lands in one place. That is not circular: nothing here asserts
 * that a key exists, only that whatever the catalogue says is what the badge
 * shows.
 *
 * Both render sites get their own case on purpose. They are different
 * components reached by different paths — a row is on screen at load, a detail
 * badge only after opening the modal — so a test touching one says nothing
 * about the other. That is the same reasoning that let the dead badge sit
 * unnoticed.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  DocumentDetail,
  DocumentListItem,
  DocumentListResponse,
  DocumentStatus,
} from '@/lib/api/knowledge-base';
import enMessages from '@/messages/en.json';
import viMessages from '@/messages/vi.json';

// The page's only I/O. Faked wholesale rather than through `fetch`, because
// what is under test is the label the badge prints for a status, and every
// status has to be arranged by hand anyway.
vi.mock('@/lib/api/knowledge-base', () => ({
  listDocuments: vi.fn(),
  getDocumentDetail: vi.fn(),
  uploadDocument: vi.fn(),
  updateDocumentMetadata: vi.fn(),
  replaceDocumentFile: vi.fn(),
  deleteDocument: vi.fn(),
}));

const api = await import('@/lib/api/knowledge-base');
const { default: KnowledgeBasePage } = await import('./page');

const STATUSES: DocumentStatus[] = ['pending', 'processing', 'ready', 'error'];

/** The four labels a catalogue offers for `DocumentStatus`, keyed by status. */
function statusLabels(messages: {
  knowledgeBase: {
    statusPending: string;
    statusProcessing: string;
    statusReady: string;
    statusError: string;
  };
}): Record<DocumentStatus, string> {
  return {
    pending: messages.knowledgeBase.statusPending,
    processing: messages.knowledgeBase.statusProcessing,
    ready: messages.knowledgeBase.statusReady,
    error: messages.knowledgeBase.statusError,
  };
}

const LOCALES = [
  { locale: 'vi', messages: viMessages, labels: statusLabels(viMessages) },
  { locale: 'en', messages: enMessages, labels: statusLabels(enMessages) },
];

function listItem(status: DocumentStatus): DocumentListItem {
  return {
    id: `doc-${status}`,
    display_name: `Quy chế ${status}`,
    category: 'policy',
    status,
    file_name: `${status}.pdf`,
    file_size: 2048,
    mime_type: 'application/pdf',
    chunk_count: 3,
    description: null,
    // Left null so the `error` case renders the same row shape as the other
    // three; the error tooltip is a different control and not what is measured.
    error_message: null,
    created_at: '2026-01-02T03:04:05Z',
    updated_at: '2026-01-02T03:04:05Z',
  };
}

function documentDetail(status: DocumentStatus): DocumentDetail {
  return { ...listItem(status), storage_path: `kb/hr/${status}.pdf`, kb_type: 'hr' };
}

function oneDocument(status: DocumentStatus): DocumentListResponse {
  return { items: [listItem(status)], total: 1, page: 1, page_size: 10 };
}

let queryClient: QueryClient | undefined;

afterEach(() => {
  queryClient?.clear();
  queryClient = undefined;
  vi.mocked(api.listDocuments).mockReset();
  vi.mocked(api.getDocumentDetail).mockReset();
});

function renderPage(locale: string, messages: Record<string, unknown>) {
  // `retry: false` so a mistake in the fakes surfaces as a failed assertion
  // rather than as a timeout.
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale={locale} messages={messages}>
        <KnowledgeBasePage />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

/**
 * The badge is a `<span>`; the status filter above the list renders the same
 * four strings as `<option>`s. Matching on the element keeps this looking at
 * what the document says about itself rather than at the filter's vocabulary.
 */
const BADGE = { selector: 'span' } as const;

describe.each(LOCALES)('knowledge-base status labels ($locale)', ({ locale, messages, labels }) => {
  it.each(STATUSES)('the document row prints %s in this locale', async (status) => {
    vi.mocked(api.listDocuments).mockResolvedValue(oneDocument(status));

    renderPage(locale, messages);

    expect(await screen.findByText(labels[status], BADGE)).toBeTruthy();
  });

  it.each(STATUSES)('the detail modal prints %s in this locale', async (status) => {
    vi.mocked(api.listDocuments).mockResolvedValue(oneDocument(status));
    vi.mocked(api.getDocumentDetail).mockResolvedValue(documentDetail(status));

    renderPage(locale, messages);
    fireEvent.click(await screen.findByRole('button', { name: listItem(status).display_name }));

    // Scoped to the dialog: the row that opened it is still mounted behind and
    // wears the same label, so an unscoped query would pass on the row alone.
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(labels[status], BADGE)).toBeTruthy();
  });
});
