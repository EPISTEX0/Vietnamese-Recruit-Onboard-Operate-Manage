'use client';

import { useEffect } from 'react';
import { useRouter } from '@/i18n/navigation';
import { useSession } from '@/lib/auth/session';
import { homePathForRole } from '@/lib/auth/roles';
import { getSetupStatus } from '@/lib/api/auth';
import { RefreshCw } from 'lucide-react';
import { useTranslations } from 'next-intl';

export default function Home() {
  const router = useRouter();
  const { isAuthenticated, role, isLoading: sessionLoading } = useSession();
  const t = useTranslations('common');

  useEffect(() => {
    async function route() {
      if (sessionLoading) return;

      // Authenticated — each role lands on its own home.
      if (isAuthenticated && role) {
        router.replace(homePathForRole(role));
        return;
      }

      // Not authenticated — check setup status
      try {
        const status = await getSetupStatus();
        if (status.setup_complete) {
          router.replace('/login');
        } else {
          router.replace('/setup');
        }
      } catch {
        // Backend unavailable — go to setup which has retry
        router.replace('/setup');
      }
    }
    route();
  }, [sessionLoading, isAuthenticated, role, router]);

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-50">
      <div className="text-center space-y-3">
        <RefreshCw className="w-8 h-8 text-indigo-600 animate-spin mx-auto" />
        <p className="text-sm font-mono text-slate-500">{t('loading')}</p>
      </div>
    </div>
  );
}
