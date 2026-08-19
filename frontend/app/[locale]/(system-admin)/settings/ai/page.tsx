'use client';

/**
 * AI provider credentials — base_url, model, and API key. Data policy
 * consent, automation level, and the AI feature switches moved to HR's own
 * `(dashboard)/settings/ai` (#420): sending recruitment data to an external
 * provider is HR's business decision, not System Admin's. This page keeps
 * only what System Admin still owns — *how* the provider is wired, not
 * *whether* it may be used.
 */

import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useFormatter } from 'next-intl';
import { Bot, Loader2, Check, AlertCircle, Cpu, FlaskConical } from 'lucide-react';
import { motion } from 'motion/react';
import * as admin from '@/lib/api/admin';
import type { OrganizationAIConfiguration } from '@/lib/api/admin';
import { PageHeader } from '@/components/shared-ui';
import { ErrorBox } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function AIConfigPage() {
  const t = useTranslations('settings');
  return (
    <div className="space-y-5">
      <PageHeader icon={Bot} title={t('aiConfig')} subtitle={t('aiConfigDesc')} />
      <AIConfigSections />
    </div>
  );
}

function AIConfigSections() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const format = useFormatter();
  // 30 seconds, same as every other console observer on a shared key — see
  // `users/page.tsx` for why the number has to be repeated rather than
  // inherited, and `shared-query-staleness.test.tsx` for the guard (#310).
  const { data: cfg, isLoading, error } = useQuery<OrganizationAIConfiguration>({
    queryKey: ['ai-config'],
    queryFn: admin.getOrganizationAIConfiguration,
    staleTime: 30_000,
  });
  const [form, setForm] = useState({ provider: '', base_url: '', model: '', api_key: '' });
  const [msg, setMsg] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    if (cfg) setForm({ provider: cfg.provider ?? '', base_url: cfg.base_url ?? '', model: cfg.model ?? '', api_key: '' });
  }, [cfg]);

  const updateMut = useMutation({
    mutationFn: () => admin.updateOrganizationAIConfiguration({
      provider: form.provider, base_url: form.base_url, model: form.model, api_key: form.api_key,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ai-config'] }); setMsg({ kind: 'success', text: t('configSaved') }); },
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });
  const testMut = useMutation({
    mutationFn: () => admin.testOrganizationAIConfiguration({
      provider: form.provider, base_url: form.base_url, model: form.model, api_key: form.api_key,
    }),
    onSuccess: (r) => setMsg({ kind: r.success ? 'success' : 'error', text: r.message }),
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });

  if (isLoading) return (
    <div className="flex items-center justify-center py-20">
      <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
    </div>
  );
  if (error) return <ErrorBox text={apiErrorText(error)} onRetry={() => qc.invalidateQueries({ queryKey: ['ai-config'] })} />;
  if (!cfg) return null;

  // Distinct from `stateLabel()` below: that one speaks per-capability state
  // (gated on automation/assistant `enabled`), this badge speaks credential
  // health regardless of whether either capability is toggled on — the same
  // signal the decrypt-failed banner already keys off (#411, #394).
  const CONNECTION_STATUS = !cfg.configured
    ? { label: t('notConnected'), text: 'text-slate-500', bg: 'bg-slate-100', dot: 'bg-slate-300' }
    : cfg.api_key_decrypt_failed
    ? { label: t('connectionUnavailable'), text: 'text-amber-600', bg: 'bg-amber-50', dot: 'bg-amber-500' }
    : { label: t('connected'), text: 'text-emerald-600', bg: 'bg-emerald-50', dot: 'bg-emerald-500' };

  return (
    <div className="space-y-5">
      {/* Notification */}
      {msg && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className={`flex items-start gap-2.5 px-4 py-3 rounded-xl text-sm border ${
            msg.kind === 'success'
              ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
              : 'bg-rose-50 border-rose-200 text-rose-800'
          }`}
        >
          {msg.kind === 'success' ? <Check className="w-4 h-4 mt-0.5 shrink-0" /> : <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />}
          <span>{msg.text}</span>
        </motion.div>
      )}

      {/* ── Section 1: AI Provider ── */}
      <section className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center">
            <Cpu className="w-4 h-4 text-indigo-600" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-slate-900">{t('aiProvider')}</h2>
            <p className="text-[12px] text-slate-500">{t('aiProviderDesc')}</p>
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full ${CONNECTION_STATUS.text} ${CONNECTION_STATUS.bg}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${CONNECTION_STATUS.dot}`} />
              {CONNECTION_STATUS.label}
            </span>
          </div>
        </div>
        <div className="p-5">
          {cfg.api_key_decrypt_failed && (
            <div className="flex gap-3 p-4 mb-4 bg-amber-50 rounded-xl border border-amber-200">
              <span className="text-xl shrink-0">⚠️</span>
              <div className="text-sm text-amber-800">
                <p className="font-semibold mb-1">{t('apiKeyDecryptFailedTitle')}</p>
                <p className="text-amber-700">{t('apiKeyDecryptFailedDesc')}</p>
              </div>
            </div>
          )}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left: Config form */}
            <div className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <label className="block">
                  <span className="text-[12px] font-medium text-slate-700 mb-1.5 block">{t('aiProvider')}</span>
                  <input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}
                    className="w-full h-10 px-3.5 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 focus:ring-2 focus:ring-indigo-50 outline-none transition-all placeholder:text-slate-400"
                    placeholder={t('providerPlaceholder')} />
                </label>
                <label className="block">
                  <span className="text-[12px] font-medium text-slate-700 mb-1.5 block">{t('modelName')}</span>
                  <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}
                    className="w-full h-10 px-3.5 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 focus:ring-2 focus:ring-indigo-50 outline-none transition-all placeholder:text-slate-400"
                    placeholder={t('modelPlaceholder')} />
                </label>
                <label className="block sm:col-span-2">
                  <span className="text-[12px] font-medium text-slate-700 mb-1.5 block">{t('apiServerUrl')}</span>
                  <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    className="w-full h-10 px-3.5 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 focus:ring-2 focus:ring-indigo-50 outline-none transition-all placeholder:text-slate-400 font-mono"
                    placeholder="https://api.openai.com/v1" />
                </label>
                <label className="block sm:col-span-2">
                  <span className="text-[12px] font-medium text-slate-700 mb-1.5 block">{t('apiKey')}</span>
                  <input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                    className="w-full h-10 px-3.5 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-indigo-400 focus:ring-2 focus:ring-indigo-50 outline-none transition-all placeholder:text-slate-400 font-mono"
                    placeholder={cfg.api_key_masked ? t('apiKeyMasked') : t('apiKeyPlaceholder')} />
                </label>
              </div>
              <div className="flex flex-wrap items-center gap-2.5 pt-1">
                <button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}
                  className="inline-flex items-center gap-2 h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-all shadow-sm shadow-indigo-200">
                  {updateMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                  {t('saveConfig')}
                </button>
                <button onClick={() => testMut.mutate()} disabled={testMut.isPending}
                  className="inline-flex items-center gap-2 h-10 px-5 text-sm font-medium rounded-xl border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-all">
                  {testMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />}
                  {t('testConnection')}
                </button>
              </div>
              <div className="flex items-center gap-4 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
                <span>{t('credentialSource')}: <strong className="text-slate-700 font-medium">{cfg.credential_source === 'org_api_key' ? t('apiKey') : cfg.credential_source ?? '—'}</strong></span>
                <span className="text-slate-300">|</span>
                <span>{t('status')}: <strong className={`font-medium ${CONNECTION_STATUS.text}`}>{CONNECTION_STATUS.label}</strong></span>
                <span className="text-slate-300">|</span>
                <span>{t('updated')}: <strong className="text-slate-700 font-medium">{cfg.updated_at ? format.dateTime(new Date(cfg.updated_at), 'shortWithYear') : '—'}</strong></span>
              </div>
            </div>
                        {/* Right: Guide */}
                        <div className="bg-gradient-to-br from-indigo-50/30 to-slate-50 rounded-xl border border-indigo-100 p-4 self-start">
                          <div className="flex items-center gap-2 pb-2 mb-3 border-b border-indigo-100">
                            <span className="text-base">📖</span>
                            <h3 className="text-sm font-bold text-indigo-800">{t('connectionGuide')}</h3>
                          </div>
                          <div className="space-y-2.5 text-[12px] text-slate-600">
                            <div className="flex gap-2">
                              <span className="text-indigo-400 font-bold shrink-0 mt-0.5">①</span>
                              <p>{t.rich('guideStep1', { strong: (chunks) => <strong>{chunks}</strong> })}</p>
                            </div>
                            <div className="flex gap-2">
                              <span className="text-indigo-400 font-bold shrink-0 mt-0.5">②</span>
                              <div><p>{t.rich('guideStep2', { strong: (chunks) => <strong>{chunks}</strong> })}</p></div>
                            </div>
                            <div className="flex gap-2">
                              <span className="text-indigo-400 font-bold shrink-0 mt-0.5">③</span>
                              <p>{t.rich('guideStep3', { strong: (chunks) => <strong>{chunks}</strong> })}</p>
                            </div>
                          </div>
                          <div className="mt-3 pt-3 border-t border-indigo-100">
                            <p className="text-[10px] font-semibold text-indigo-500 mb-1.5">💡 {t('commonUrls')}</p>
                            <div className="space-y-1 text-[10px] font-mono">
                              <div className="flex justify-between gap-2"><span className="text-slate-400 shrink-0">OpenAI</span><code className="text-indigo-500 truncate">api.openai.com/v1</code></div>
                              <div className="flex justify-between gap-2"><span className="text-slate-400 shrink-0">Gemini</span><code className="text-indigo-500 truncate">generativelanguage.googleapis.com/v1beta/openai</code></div>
                              <div className="flex justify-between gap-2"><span className="text-slate-400 shrink-0">Cline</span><code className="text-indigo-500 truncate">api.cline.bot/api/v1</code></div>
                            </div>
                          </div>
                        </div>
              </div>
        </div>
      </section>
    </div>
  );
}
