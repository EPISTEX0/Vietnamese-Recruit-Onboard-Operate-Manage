'use client';

/** Access whitelist — the emails and domain patterns allowed to sign in. */

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { ShieldCheck, Loader2, Plus, Trash2, AlertCircle } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type { WhitelistEntry } from '@/lib/api/admin';
import { PageHeader } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function AccessWhitelistPage() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ['whitelist'], queryFn: admin.listWhitelist });
  const [value, setValue] = useState('');
  const [wlError, setWlError] = useState<string | null>(null);
  const addMut = useMutation({
    mutationFn: () => admin.addWhitelistEntry(value.trim()),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['whitelist'] }); setValue(''); setWlError(null); },
    onError: (e) => setWlError(apiErrorText(e)),
  });
  const delMut = useMutation({
    mutationFn: (id: string) => admin.removeWhitelistEntry(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['whitelist'] }); setWlError(null); },
    onError: (e) => setWlError(apiErrorText(e)),
  });

  const handleDelete = (entry: WhitelistEntry) => {
    if (!entry.id) return;
    if (!window.confirm(t('confirmDeleteEntry', { value: entry.value }))) return;
    delMut.mutate(entry.id);
  };

  const sourceLabel = (w: WhitelistEntry) => {
    if (w.source === 'file') return t('readOnlySource');
    return t('manualAdd');
  };

  return (
    <div className="space-y-5">
      <PageHeader icon={ShieldCheck} title={t('accessWhitelist')} subtitle={t('whitelistDesc')} />
      <SectionCard>
        {wlError && (
          <div className="mb-3 p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>{wlError}</span>
          </div>
        )}
        <div className="flex gap-2 mb-4">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && value.trim()) addMut.mutate(); }}
            className="flex-1 h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400"
            placeholder={t('whitelistPlaceholder')}
          />
          <button
            onClick={() => addMut.mutate()}
            disabled={addMut.isPending || !value.trim()}
            className="h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-all flex items-center gap-1.5 shadow-sm shadow-indigo-200"
          >
            {addMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            {addMut.isPending ? t('adding') : t('add')}
          </button>
        </div>
        {/* Error before data — see `Empty` in ../_components/console-ui. */}
        {isError ? <ErrorBox text={apiErrorText(error)} onRetry={() => { void refetch(); }} /> :
          isPending ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
          (data?.items?.length ?? 0) === 0 ? <Empty text={t('emptyWhitelist')} /> :
          <div className="space-y-1.5">
            {data!.items.map((w: WhitelistEntry) => (
              <div key={w.id ?? w.value} className="flex items-center gap-2.5 p-2.5 bg-slate-50 rounded-lg">
                <span className={`text-[10px] font-medium px-2 py-0.5 rounded ${w.entry_type === 'domain_pattern' ? 'bg-indigo-50 text-indigo-600' : 'bg-slate-200 text-slate-600'}`}>
                  {w.entry_type === 'domain_pattern' ? t('domain') : t('emailType')}
                </span>
                <span className="text-[13px] text-slate-700 flex-1 truncate">{w.value}</span>
                <span className={`text-[10px] shrink-0 ${w.is_readonly ? 'text-slate-400' : 'text-slate-500'}`}>{sourceLabel(w)}</span>
                {w.id && !w.is_readonly && (
                  <button
                    onClick={() => handleDelete(w)}
                    disabled={delMut.isPending}
                    className="p-1.5 text-slate-400 hover:text-rose-500 hover:bg-rose-50 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {delMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                  </button>
                )}
              </div>
            ))}
          </div>}
      </SectionCard>
    </div>
  );
}
