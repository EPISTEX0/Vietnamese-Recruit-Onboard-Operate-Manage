/**
 * `oauthConfigUpdateSchema` — the client-side gate in front of
 * `POST /api/system-admin/oauth/config`.
 *
 * It had no consumer until #307 gave the console an OAuth section, so nothing
 * had ever run a real deployment's values through it. Two rules are worth
 * pinning now that something does.
 */

import { describe, it, expect } from 'vitest';

import { oauthConfigUpdateSchema } from './admin-schemas';

const valid = {
  client_id: '1234.apps.googleusercontent.com',
  client_secret: 'GOCSPX-a-real-looking-secret',
  redirect_uri: 'https://vroom.example.com/api/auth/callback',
};

const withRedirect = (redirect_uri: string) => ({ ...valid, redirect_uri });

describe('the client secret rule', () => {
  it('refuses an empty secret', () => {
    // Load-bearing, not cosmetic. The backend validates `client_id` and
    // `redirect_uri` but never checks the secret is non-empty
    // (`oauth_config_manager.py:190-201`) — it would encrypt and store `''`,
    // leaving a configuration that reads as complete on screen and fails every
    // login. This rule is the only thing in front of that.
    expect(oauthConfigUpdateSchema.safeParse({ ...valid, client_secret: '' }).success).toBe(false);
  });

  it('refuses a secret that is only whitespace', () => {
    expect(oauthConfigUpdateSchema.safeParse({ ...valid, client_secret: '   ' }).success).toBe(false);
  });

  it('strips the newline a paste from the Google console brings with it', () => {
    // The backend stores what it is sent. A secret with a trailing newline is
    // encrypted with the newline, and every login afterwards fails
    // `invalid_client` while this screen shows a complete configuration.
    const parsed = oauthConfigUpdateSchema.safeParse({
      ...valid,
      client_id: '  1234.apps.googleusercontent.com\n',
      client_secret: 'GOCSPX-a-real-looking-secret\n',
      redirect_uri: ' https://vroom.example.com/api/auth/callback ',
    });

    expect(parsed.success).toBe(true);
    expect(parsed.success && parsed.data).toEqual(valid);
  });

  it('accepts a well-formed configuration', () => {
    expect(oauthConfigUpdateSchema.safeParse(valid).success).toBe(true);
  });
});

describe('the redirect URI rule', () => {
  it('accepts the loopback http URI a self-hosted deployment actually runs', () => {
    // `http://localhost:8000/api/auth/callback` is this codebase's own default
    // (`identity/infrastructure/config.py`), and Google's own rule carves
    // loopback out of its https requirement. An https-only check rejects the
    // exact string the console prints one card above the form, leaving the
    // admin with a value they can read and cannot re-save.
    expect(oauthConfigUpdateSchema.safeParse(
      withRedirect('http://localhost:8000/api/auth/callback'),
    ).success).toBe(true);
    expect(oauthConfigUpdateSchema.safeParse(
      withRedirect('http://127.0.0.1:8000/api/auth/callback'),
    ).success).toBe(true);
  });

  it('still refuses plaintext http to any other host', () => {
    // The carve-out is loopback, not http. A redirect URI over the network in
    // the clear is where the authorization code leaks.
    expect(oauthConfigUpdateSchema.safeParse(
      withRedirect('http://vroom.example.com/api/auth/callback'),
    ).success).toBe(false);
    // Not a prefix check: a host that merely starts with the loopback name is
    // a different machine entirely.
    expect(oauthConfigUpdateSchema.safeParse(
      withRedirect('http://localhost.attacker.example/api/auth/callback'),
    ).success).toBe(false);
  });

  it('reads the scheme case-insensitively, the way a URL is defined', () => {
    // Both arms parse, so neither can disagree with the other about what
    // `HTTPS` means — a prefix check on one side and a parse on the other let
    // `HTTP://localhost/cb` through while turning `HTTPS://vroom.example/cb`
    // away with a message telling the admin to use the scheme they just used.
    expect(oauthConfigUpdateSchema.safeParse(
      withRedirect('HTTPS://vroom.example.com/api/auth/callback'),
    ).success).toBe(true);
    expect(oauthConfigUpdateSchema.safeParse(
      withRedirect('HTTP://vroom.example.com/api/auth/callback'),
    ).success).toBe(false);
  });

  it('refuses anything that is not a URL', () => {
    expect(oauthConfigUpdateSchema.safeParse(withRedirect('vroom.example.com')).success).toBe(false);
    expect(oauthConfigUpdateSchema.safeParse(withRedirect('')).success).toBe(false);
  });
});
