/**
 * Turn whatever a mutation threw into the sentence the console shows.
 *
 * Console-local, not a shared-ui export: every write surface here is a system
 * setup form, and a rejected setup form is almost always a field-level
 * validation failure the admin has to act on. Collapsing those to the generic
 * error-code message would hide which field was wrong.
 */

import { ApiError } from '@/lib/api/types';
import { getErrorMessage } from '@/lib/api/error-codes';

export function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    // For validation errors, show field-level messages if available
    if (err.fieldErrors && Object.keys(err.fieldErrors).length > 0) {
      return Object.values(err.fieldErrors).join('; ');
    }
    // Use the specific message from the error, fall back to generic error code mapping
    return err.message || getErrorMessage(err.errorCode);
  }
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
