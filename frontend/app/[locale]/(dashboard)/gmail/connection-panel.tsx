'use client';

import React from 'react';
import { useTranslations } from 'next-intl';
import { Plug, Unplug, Loader2 } from 'lucide-react';
import { getErrorMessage } from '@/lib/api/error-codes';
import type { OrganizationGoogleConnectionStatus } from '@/lib/api/types';

interface ConnectionPanelProps {
  status: OrganizationGoogleConnectionStatus | null;
  email: string | null;
  loading: boolean;
  error: string | null;
  notConnectedCode: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  connectLoading: boolean;
  disconnectLoading: boolean;
}

export default function ConnectionPanel({
  status, email, loading, error, notConnectedCode, onConnect, onDisconnect, connectLoading, disconnectLoading,
}: ConnectionPanelProps) {
  const t = useTranslations('gmail');
  const connected = status === 'connected';
  // `degraded` is the DB-value sanitize fallback (see `types.ts`): a
  // connection exists but is broken, not "never connected" — give it its
  // own label rather than falling into the `disconnected` bucket (#363).
  const degraded = status === 'degraded';
  const needsReauth = status === 'reauthorization_required' || degraded;
  return (
    <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm flex flex-col sm:flex-row sm:items-center gap-3">
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${connected ? 'bg-emerald-50' : 'bg-slate-100'}`}>
        {connected ? <Plug className="w-5 h-5 text-emerald-600" /> : <Unplug className="w-5 h-5 text-slate-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-bold text-slate-900">{t('connectionTitle')}</h2>
          <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold border ${
            connected ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
              : degraded ? 'bg-rose-50 text-rose-700 border-rose-200'
              : status === 'reauthorization_required' ? 'bg-amber-50 text-amber-700 border-amber-200'
              : 'bg-slate-50 text-slate-500 border-slate-200'
          }`}>
            {connected ? t('connected') : degraded ? t('degraded') : status === 'reauthorization_required' ? t('reauthorize') : t('disconnected')}
          </span>
        </div>
        {connected && email && <p className="text-xs text-slate-500 mt-0.5 truncate">{email}</p>}
        {notConnectedCode && (
          <p className="text-[11px] text-rose-600 mt-0.5 font-mono">{notConnectedCode}: {getErrorMessage(notConnectedCode)}</p>
        )}
        {error && !notConnectedCode && <p className="text-[11px] text-rose-600 mt-0.5">{error}</p>}
        {loading && !status && <p className="text-[11px] text-slate-400 mt-0.5">{t('checkingStatus')}</p>}
      </div>
      {!connected && (
        <button
          onClick={onConnect}
          disabled={connectLoading || loading}
          className="inline-flex items-center gap-1.5 text-xs font-medium bg-indigo-600 text-white rounded-lg px-3 py-2 hover:bg-indigo-700 disabled:opacity-50"
        >
          {connectLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
          {needsReauth ? t('reauthorizeBtn') : t('connectBtn')}
        </button>
      )}
      {connected && (
        <button
          onClick={() => { if (confirm(t('disconnectConfirm'))) onDisconnect(); }}
          disabled={disconnectLoading}
          className="inline-flex items-center gap-1.5 text-xs font-medium bg-white border border-slate-200 text-slate-600 rounded-lg px-3 py-2 hover:bg-slate-50 disabled:opacity-50"
        >
          {disconnectLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Unplug className="w-3.5 h-3.5" />}
          {t('disconnectBtn')}
        </button>
      )}
    </div>
  );
}
