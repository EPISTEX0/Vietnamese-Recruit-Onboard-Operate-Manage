import { getLocale } from 'next-intl/server';
import { redirect } from '@/i18n/navigation';

/**
 * `/settings` has no content of its own yet — the console's seven sections all
 * live one level down now that each has its own route.
 *
 * Redirecting to AI configuration keeps the system admin's landing behaviour
 * byte-for-byte what it was before the split, which is what lets
 * `homePathForRole('system_admin') === '/settings'` and its tests stay
 * untouched. #302 replaces this redirect with the Tổng quan hệ thống homepage.
 */
export default async function SettingsIndexPage() {
  const locale = await getLocale();
  redirect({ href: '/settings/ai', locale });
}
