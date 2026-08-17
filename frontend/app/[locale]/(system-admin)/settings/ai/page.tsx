'use client';

/**
 * AI configuration — provider credentials, data policy, automation level, and
 * the two AI feature switches.
 *
 * The only console section built from several blocks, so it keeps the two-level
 * structure: `PageHeader` names the section, and each block keeps its own
 * header strip. The six single-card sections drop theirs instead.
 */

import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useFormatter } from 'next-intl';
import {
  Bot, Loader2, Check, AlertCircle, Cpu, FlaskConical, Zap, ShieldAlert, Sparkles,
} from 'lucide-react';
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
  const { data: policy } = useQuery({
    queryKey: ['ai-data-policy'],
    queryFn: admin.getDataPolicy,
    enabled: !!cfg && !cfg.data_policy_accepted,
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

  const acceptPolicyMut = useMutation({
        mutationFn: async () => {
          await admin.acceptDataPolicy();
          await admin.acceptAutomationConsent();
          await admin.acceptAssistantConsent();
        },
        onSuccess: () => {
          qc.invalidateQueries({ queryKey: ['ai-config'] });
          qc.invalidateQueries({ queryKey: ['ai-data-policy'] });
          setMsg({ kind: 'success', text: t('policyAccepted') });
        },
        onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
      });

  const presetMut = useMutation({
    mutationFn: (preset: 'conservative' | 'balanced' | 'high_recall') => admin.setAIPolicyPreset(preset),
    onMutate: (preset) => {
      // Optimistic update — apply immediately so UI responds on first click
      qc.setQueryData<OrganizationAIConfiguration>(['ai-config'], (old) =>
        old ? { ...old, ai_policy_preset: preset } : old
      );
    },
    onSuccess: (data) => {
      // Use returned data directly — no refetch needed, instant update
      qc.setQueryData<OrganizationAIConfiguration>(['ai-config'], data);
    },
    onError: (e) => {
      // Revert on error — invalidate to restore true server state
      qc.invalidateQueries({ queryKey: ['ai-config'] });
      setMsg({ kind: 'error', text: apiErrorText(e) });
    },
  });
  const toggleAutomation = useMutation({
    mutationFn: (enable: boolean) => (enable ? admin.enableAutomation() : admin.disableAutomation()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-config'] }),
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });
  const toggleAssistant = useMutation({
    mutationFn: (enable: boolean) => (enable ? admin.enableAssistant() : admin.disableAssistant()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-config'] }),
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });

  if (isLoading) return (
    <div className="flex items-center justify-center py-20">
      <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
    </div>
  );
  if (error) return <ErrorBox text={apiErrorText(error)} onRetry={() => qc.invalidateQueries({ queryKey: ['ai-config'] })} />;
  if (!cfg) return null;

  const CONNECTION_STATUS = cfg.configured
    ? { label: t('connected'), color: 'text-emerald-600 bg-emerald-50', dot: 'bg-emerald-500' }
    : { label: t('notConnected'), color: 'text-slate-500 bg-slate-100', dot: 'bg-slate-300' };

  const PRESETS = [
    {
      value: 'conservative' as const,
      title: t('presetConservative'),
      icon: '🛡️',
      desc: t('presetConservativeDesc'),
      useCases: [
        t('conservativeUse1'),
        t('conservativeUse2'),
        t('conservativeUse3'),
      ],
    },
    {
      value: 'balanced' as const,
      title: t('presetBalanced'),
      icon: '⚖️',
      desc: t('presetBalancedDesc'),
      useCases: [
        t('balancedUse1'),
        t('balancedUse2'),
        t('balancedUse3'),
      ],
    },
    {
      value: 'high_recall' as const,
      title: t('presetHighRecall'),
      icon: '🔍',
      desc: t('presetHighRecallDesc'),
      useCases: [
        t('highRecallUse1'),
        t('highRecallUse2'),
        t('highRecallUse3'),
      ],
    },
  ];

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
            <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full ${CONNECTION_STATUS.color}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${CONNECTION_STATUS.dot}`} />
              {CONNECTION_STATUS.label}
            </span>
          </div>
        </div>
        <div className="p-5">
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
                <span>{t('status')}: <strong className={`font-medium ${cfg.configured ? 'text-emerald-600' : 'text-slate-500'}`}>{CONNECTION_STATUS.label}</strong></span>
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

      {/* ── Section 2: Data policy (only if not accepted) ── */}
      {!cfg.data_policy_accepted && (
        <section className="bg-amber-50/50 rounded-2xl border border-amber-200 overflow-hidden">
          <div className="px-5 py-4 border-b border-amber-100 flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-amber-100 flex items-center justify-center">
              <ShieldAlert className="w-4 h-4 text-amber-600" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-amber-800">{t('dataPolicy')}</h2>
              <p className="text-[12px] text-amber-600">{t('dataPolicyRequired')}</p>
            </div>
          </div>
          <div className="p-5 space-y-4">
            <div className="flex gap-3 p-4 bg-white rounded-xl border border-amber-100">
              <span className="text-xl shrink-0">⚠️</span>
              <div className="text-sm text-amber-800">
                <p className="font-semibold mb-1">{t('policyNotice')}</p>
                <p className="text-amber-700">{t('policyDescription')}</p>
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-[12px] font-medium text-slate-700">{t('dataSent')}</p>
              {policy?.items?.map((item: { category: string; purpose: string }, i: number) => (
                <div key={i} className="flex items-start gap-2.5 p-3 bg-white rounded-xl border border-slate-100">
                  <div className="w-6 h-6 rounded-lg bg-slate-100 flex items-center justify-center text-xs font-mono text-slate-500 shrink-0 mt-0.5">{i + 1}</div>
                  <div>
                    <p className="text-[13px] font-medium text-slate-800">{item.category}</p>
                    <p className="text-[12px] text-slate-500">{item.purpose}</p>
                  </div>
                </div>
              ))}
            </div>
            <button onClick={() => acceptPolicyMut.mutate()} disabled={acceptPolicyMut.isPending}
              className="w-full h-11 text-sm font-semibold rounded-xl bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50 transition-all shadow-sm shadow-amber-200">
              {acceptPolicyMut.isPending ? (
                <span className="flex items-center justify-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> {t('processing')}</span>
              ) : t('acceptAndActivate')}
            </button>
          </div>
        </section>
      )}

      {/* ── Section 3: Automation level ── */}
      <section className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center">
            <Zap className="w-4 h-4 text-indigo-600" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-slate-900">{t('automationLevel')}</h2>
            <p className="text-[12px] text-slate-500">{t('automationLevelDesc')}</p>
          </div>
        </div>
        <div className="p-5">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {PRESETS.map((p) => {
              const active = cfg.ai_policy_preset === p.value;
              const isPending = presetMut.isPending && presetMut.variables === p.value;
              return (
                <button key={p.value} onClick={() => presetMut.mutate(p.value)} disabled={presetMut.isPending}
                  className={`relative text-left p-4 rounded-xl border-2 transition-all ${
                    active
                      ? 'border-indigo-500 bg-indigo-50 shadow-sm shadow-indigo-100'
                      : 'border-slate-100 hover:border-slate-200 hover:bg-slate-50'
                  } ${presetMut.isPending ? 'opacity-70' : ''}`}>
                  {isPending && (
                    <div className="absolute inset-0 bg-white/60 rounded-xl flex items-center justify-center z-10">
                      <Loader2 className="w-5 h-5 animate-spin text-indigo-500" />
                    </div>
                  )}
                  {active && !isPending && (
                    <div className="absolute top-3 right-3 w-5 h-5 rounded-full bg-indigo-600 flex items-center justify-center">
                      <Check className="w-3 h-3 text-white" />
                    </div>
                  )}
                  <div className="text-2xl mb-2">{p.icon}</div>
                  <h3 className={`text-sm font-bold mb-1 ${active ? 'text-indigo-700' : 'text-slate-900'}`}>
                    {p.title}
                    {active && <span className="ml-1.5 text-[10px] font-medium bg-indigo-600 text-white px-1.5 py-0.5 rounded-full">{t('inUse')}</span>}
                  </h3>
                  <p className="text-[11px] text-slate-500 leading-relaxed mb-2">{p.desc}</p>
                  <ul className="space-y-1">
                    {p.useCases.map((uc, i) => (
                      <li key={i} className="text-[10px] text-slate-400 flex items-start gap-1">
                        <span className="text-indigo-400 mt-0.5 shrink-0">•</span>
                        <span>{uc}</span>
                      </li>
                    ))}
                  </ul>
                </button>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Section 4: AI features ── */}
      <section className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-indigo-600" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-slate-900">{t('aiFeatures')}</h2>
            <p className="text-[12px] text-slate-500">{t('aiFeaturesDesc')}</p>
          </div>
        </div>
        <div className="divide-y divide-slate-50">
          <ToggleFeature icon="📧" title={t('featureEmailClassify')}
            desc={t('featureEmailClassifyDesc')}
            enabled={cfg.automation_enabled} state={stateLabel(t, cfg.automation_state)}
            loading={toggleAutomation.isPending} onToggle={() => toggleAutomation.mutate(!cfg.automation_enabled)} />
          <ToggleFeature icon="💬" title={t('featureAssistant')}
            desc={t('featureAssistantDesc')}
            enabled={cfg.assistant_enabled} state={stateLabel(t, cfg.assistant_state)}
            loading={toggleAssistant.isPending} onToggle={() => toggleAssistant.mutate(!cfg.assistant_enabled)} />
        </div>
      </section>
    </div>
  );
}

function stateLabel(t: (key: string) => string, s: string) {
      switch (s) {
        case 'enabled': return { text: t('enabled'), color: 'bg-emerald-100 text-emerald-700' };
        case 'disabled': return { text: t('disabled'), color: 'bg-slate-100 text-slate-500' };
        case 'not_configured': return { text: t('notConfigured'), color: 'bg-slate-100 text-slate-400' };
        case 'ready': return { text: t('ready'), color: 'bg-blue-100 text-blue-700' };
        default: return { text: s, color: 'bg-slate-100 text-slate-500' };
      }
    }

function ToggleFeature({ icon, title, desc, enabled, state, loading, onToggle }: {
  icon: string; title: string; desc: string; enabled: boolean;
  state: { text: string; color: string }; loading: boolean; onToggle: () => void;
}) {
  return (
    <div className="flex items-center gap-4 px-5 py-4 hover:bg-slate-50/50 transition-colors">
      <span className="text-xl shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
          <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${state.color}`}>{state.text}</span>
        </div>
        <p className="text-[12px] text-slate-500 leading-relaxed">{desc}</p>
      </div>
      <button onClick={onToggle} disabled={loading}
        className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-all ${
          loading ? 'opacity-50' : ''} ${enabled ? 'bg-indigo-600' : 'bg-slate-200'}`}>
        {loading && <Loader2 className="absolute inset-0 m-auto w-4 h-4 animate-spin text-white z-10" />}
        <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-all ${
          enabled ? 'translate-x-6' : 'translate-x-1'}`} />
      </button>
    </div>
  );
}
