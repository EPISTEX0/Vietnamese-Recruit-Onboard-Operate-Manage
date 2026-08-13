import { redirect } from 'next/navigation';
import { getLocale } from 'next-intl/server';

/**
 * Non-localized root. There is no session here (server component, no cookie
 * read), so it cannot pick a role's home — hand off to the localized root,
 * which routes on the live session.
 */
export default async function Home() {
  const locale = await getLocale();
  redirect(`/${locale}`);
}
