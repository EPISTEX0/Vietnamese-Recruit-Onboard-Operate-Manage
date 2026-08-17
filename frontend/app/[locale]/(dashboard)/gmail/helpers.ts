import type { useFormatter } from 'next-intl';
import { ApiError } from '@/lib/api/types';
import { getErrorMessage } from '@/lib/api/error-codes';

/**
 * Not a component or hook, so it cannot call `useFormatter()` itself — the
 * caller (always a component, #313) passes its own down instead.
 */
export function fmtDate(iso: string | null, format: ReturnType<typeof useFormatter>): string {
  if (!iso) return '—';
  try {
    // Backend có thể gửi Unix timestamp (giây) dạng string hoặc ISO 8601
    const parsed = Number(iso);
    const date = Number.isFinite(parsed) && parsed > 1000000000
      ? new Date(parsed * 1000) // Unix timestamp giây → ms
      : new Date(iso);          // ISO 8601
    if (isNaN(date.getTime())) return iso;
    return format.dateTime(date, 'full');
  } catch {
    return iso;
  }
}

export function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) return getErrorMessage(err.errorCode);
  if (err instanceof Error) return err.message;
  return 'Lỗi không xác định';
}

export const NAVY = 'bg-slate-900';
