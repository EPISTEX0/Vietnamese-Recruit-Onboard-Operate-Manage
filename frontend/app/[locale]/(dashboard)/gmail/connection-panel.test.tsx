/**
 * @vitest-environment jsdom
 *
 * `degraded` (#363) is the sanitize fallback backend returns for any
 * unrecognized `organization_google_connections.status` value — a real
 * connection that is broken, not "never connected". Before #363 this status
 * was outside the frontend's `ConnectionStatus` union entirely, so
 * `status === 'connected'` and `status === 'reauthorization_required'` were
 * both `false` and the panel silently rendered the "not connected" branch,
 * telling HR to connect Gmail for the first time when it was actually
 * connected and broken.
 *
 * This test renders the real component against each status value and checks
 * both the label and the recovery action it offers — the two hidden by
 * TypeScript being unable to see a backend value missing from a union.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';

import messages from '@/messages/vi.json';
import ConnectionPanel from './connection-panel';

const t = messages.gmail;

function renderPanel(status: 'connected' | 'disconnected' | 'degraded' | 'reauthorization_required' | null) {
  return render(
    <NextIntlClientProvider locale="vi" messages={messages}>
      <ConnectionPanel
        status={status}
        email={null}
        loading={false}
        error={null}
        notConnectedCode={null}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        connectLoading={false}
        disconnectLoading={false}
      />
    </NextIntlClientProvider>,
  );
}

describe('ConnectionPanel status=degraded', () => {
  it('labels it distinctly from disconnected', () => {
    renderPanel('degraded');
    expect(screen.getByText(t.degraded)).toBeTruthy();
    expect(screen.queryByText(t.disconnected)).toBeNull();
  });

  it('offers the re-authorize action, not a fresh connect', () => {
    renderPanel('degraded');
    expect(screen.getByRole('button', { name: t.reauthorizeBtn })).toBeTruthy();
    expect(screen.queryByRole('button', { name: t.connectBtn })).toBeNull();
  });
});

describe('ConnectionPanel other statuses (regression baseline)', () => {
  it('still labels disconnected as disconnected, offering a fresh connect', () => {
    renderPanel('disconnected');
    expect(screen.getByText(t.disconnected)).toBeTruthy();
    expect(screen.getByRole('button', { name: t.connectBtn })).toBeTruthy();
  });

  it('still labels reauthorization_required distinctly, offering re-authorize', () => {
    renderPanel('reauthorization_required');
    expect(screen.getByText(t.reauthorize)).toBeTruthy();
    expect(screen.getByRole('button', { name: t.reauthorizeBtn })).toBeTruthy();
  });

  it('still labels connected as connected, with a disconnect action', () => {
    renderPanel('connected');
    expect(screen.getByText(t.connected)).toBeTruthy();
    expect(screen.getByRole('button', { name: t.disconnectBtn })).toBeTruthy();
  });
});
