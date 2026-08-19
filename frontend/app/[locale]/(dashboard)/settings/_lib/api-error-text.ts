/**
 * Turn whatever a mutation threw into the sentence the page shows.
 *
 * Mirrors the System Admin console's own `api-error-text.ts`: prefers the
 * server's own message over the generic error-code mapping, since a
 * `422 AI_CONFIG_INVALID` here almost always names the exact precondition
 * (consent, data policy) HR needs to act on next.
 */

import { ApiError } from '@/lib/api/types';
import { getErrorMessage } from '@/lib/api/error-codes';

export function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.fieldErrors && Object.keys(err.fieldErrors).length > 0) {
      return Object.values(err.fieldErrors).join('; ');
    }
    return err.message || getErrorMessage(err.errorCode);
  }
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
