'use client';

/** Email domains — the organization's own mail domains. */

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { Mail, Loader2, Plus, X, AlertCircle } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import { PageHeader } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function EmailDomainsPage() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ['org-domains'], queryFn: admin.listDomains });
  const [value, setValue] = useState('');
  const [domError, setDomError] = useState<string | null>(null);
  const addMut = useMutation({
    mutationFn: () => admin.addDomains(value.split(',').map((s) => s.trim()).filter(Boolean)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['org-domains'] }); setValue(''); setDomError(null); },
    onError: (e) => setDomError(apiErrorText(e)),
  });
  const rmMut = useMutation({
    mutationFn: (d: string) => admin.removeDomain(d),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['org-domains'] }); setDomError(null); },
    onError: (e) => setDomError(apiErrorText(e)),
  });

  const handleRemove = (domain: string) => {
    if (!window.confirm(t('confirmRemoveDomain', { domain }))) return;
    rmMut.mutate(domain);
  };

  return (
    <div className="space-y-5">
      <PageHeader icon={Mail} title={t('emailDomains')} subtitle={t('domainsDesc')} />
      <SectionCard>
        {domError && (
          <div className="mb-3 p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>{domError}</span>
          </div>
        )}
        <div className="flex gap-2 mb-4">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && value.trim()) addMut.mutate(); }}
            className="flex-1 h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400"
            placeholder={t('domainsPlaceholder')}
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
          (data?.allowed_domains?.length ?? 0) === 0 ? <Empty text={t('noDomains')} /> :
          <div className="flex flex-wrap gap-2">
            {data!.allowed_domains.map((d) => (
              <span key={d} className="inline-flex items-center gap-1.5 text-[13px] bg-indigo-50 text-indigo-700 border border-indigo-100 px-3 py-1.5 rounded-lg">
                @{d}
                <button
                  onClick={() => handleRemove(d)}
                  disabled={rmMut.isPending}
                  className="text-indigo-400 hover:text-rose-500 ml-0.5 disabled:opacity-50"
                >
                  {rmMut.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <X className="w-3 h-3" />}
                </button>
              </span>
            ))}
          </div>}
      </SectionCard>
    </div>
  );
}
