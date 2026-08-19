'use client';

/**
 * HR's AI configuration — data policy consent, automation level, and the two
 * AI feature switches. Provider credentials (base_url, model, API key) are
 * System Admin's own scope and never appear here; the only signal about the
 * provider on this page is the read-only connected/not-connected card (#420).
 */

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import {
  Bot, Loader2, Check, AlertCircle, Plug, ShieldAlert, Zap, Sparkles,
} from 'lucide-react';
import { motion } from 'motion/react';
import * as hrAiConfig from '@/lib/api/hr-ai-config';
import type { AICapabilityState, HRAIConfiguration } from '@/lib/api/hr-ai-config';
import { PageHeader, ErrorBanner, Loading } from '@/components/shared-ui';
import { apiErrorText } from '../_lib/api-error-text';

export default function HRAIConfigPage() {
  const t = useTranslations('settings');
  return (
    <div className="space-y-5">
      <PageHeader icon={Bot} title={t('aiConfig')} subtitle={t('aiConfigHrDesc')} />
      <HRAIConfigSections />
    </div>
  );
}

function HRAIConfigSections() {
  const qc = useQueryClient();
  const t = useTranslations('settings');

  // Same 30s staleTime convention as the rest of the console — see
  // `shared-query-staleness.test.tsx` for the guard this mirrors (#310).
  const { data: cfg, isLoading, error } = useQuery<HRAIConfiguration>({
    queryKey: ['hr-ai-config'],
    queryFn: hrAiConfig.getConfiguration,
    staleTime: 30_000,
  });
  const { data: status } = useQuery({
    queryKey: ['hr-ai-provider-status'],
    queryFn: hrAiConfig.getProviderStatus,
    staleTime: 30_000,
  });
  const { data: policy } = useQuery({
    queryKey: ['hr-ai-data-policy'],
    queryFn: hrAiConfig.getDataPolicy,
    enabled: !!cfg && !cfg.data_policy_accepted,
  });
  const [msg, setMsg] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const acceptPolicyMut = useMutation({
    mutationFn: async () => {
      await hrAiConfig.acceptDataPolicy();
      await hrAiConfig.acceptAutomationConsent();
      await hrAiConfig.acceptAssistantConsent();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['hr-ai-config'] });
      qc.invalidateQueries({ queryKey: ['hr-ai-data-policy'] });
      setMsg({ kind: 'success', text: t('policyAccepted') });
    },
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });

  const presetMut = useMutation({
    mutationFn: (preset: 'conservative' | 'balanced' | 'high_recall') => hrAiConfig.setAIPolicyPreset(preset),
    onMutate: (preset) => {
      qc.setQueryData<HRAIConfiguration>(['hr-ai-config'], (old) =>
        old ? { ...old, ai_policy_preset: preset } : old
      );
    },
    onSuccess: (data) => {
      qc.setQueryData<HRAIConfiguration>(['hr-ai-config'], data);
    },
    onError: (e) => {
      qc.invalidateQueries({ queryKey: ['hr-ai-config'] });
      setMsg({ kind: 'error', text: apiErrorText(e) });
    },
  });
  const toggleAutomation = useMutation({
    mutationFn: (enable: boolean) => (enable ? hrAiConfig.enableAutomation() : hrAiConfig.disableAutomation()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['hr-ai-config'] }),
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });
  const toggleAssistant = useMutation({
    mutationFn: (enable: boolean) => (enable ? hrAiConfig.enableAssistant() : hrAiConfig.disableAssistant()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['hr-ai-config'] }),
    onError: (e) => setMsg({ kind: 'error', text: apiErrorText(e) }),
  });

  if (isLoading) return <Loading />;
  if (error) return <ErrorBanner error={error} />;
  if (!cfg) return null;

  const PRESETS = [
    {
      value: 'conservative' as const,
      title: t('presetConservative'),
      icon: '🛡️',
      desc: t('presetConservativeDesc'),
      useCases: [t('conservativeUse1'), t('conservativeUse2'), t('conservativeUse3')],
    },
    {
      value: 'balanced' as const,
      title: t('presetBalanced'),
      icon: '⚖️',
      desc: t('presetBalancedDesc'),
      useCases: [t('balancedUse1'), t('balancedUse2'), t('balancedUse3')],
    },
    {
      value: 'high_recall' as const,
      title: t('presetHighRecall'),
      icon: '🔍',
      desc: t('presetHighRecallDesc'),
      useCases: [t('highRecallUse1'), t('highRecallUse2'), t('highRecallUse3')],
    },
  ];

  const PROVIDER_STATUS = status?.connected
    ? { label: t('connected'), text: 'text-emerald-600', bg: 'bg-emerald-50', dot: 'bg-emerald-500' }
    : { label: t('notConnected'), text: 'text-slate-500', bg: 'bg-slate-100', dot: 'bg-slate-300' };

  return (
    <div className="space-y-5">
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

      {/* ── Provider status (read-only) ── */}
      <section className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-4 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center">
            <Plug className="w-4 h-4 text-indigo-600" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-bold text-slate-900">{t('providerStatusTitle')}</h2>
            <p className="text-[12px] text-slate-500">{t('providerStatusDesc')}</p>
          </div>
          <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full ${PROVIDER_STATUS.text} ${PROVIDER_STATUS.bg}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${PROVIDER_STATUS.dot}`} />
            {PROVIDER_STATUS.label}
          </span>
        </div>
        {!status?.connected && (
          <div className="px-5 pb-4">
            <div className="flex gap-3 p-4 bg-slate-50 rounded-xl border border-slate-200">
              <span className="text-xl shrink-0">ℹ️</span>
              <p className="text-sm text-slate-600">{t('contactSystemAdminForAI')}</p>
            </div>
          </div>
        )}
      </section>

      {/* ── Data policy (only if not accepted) ── */}
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

      {/* ── Automation level ── */}
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

      {/* ── AI features ── */}
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

function stateLabel(t: (key: string) => string, s: AICapabilityState) {
  switch (s) {
    case 'disabled': return { text: t('disabled'), color: 'bg-slate-100 text-slate-500' };
    case 'not_configured': return { text: t('notConfigured'), color: 'bg-slate-100 text-slate-400' };
    case 'ready': return { text: t('ready'), color: 'bg-blue-100 text-blue-700' };
    case 'unavailable': return { text: t('unavailable'), color: 'bg-amber-100 text-amber-700' };
    default: {
      // Exhaustive at compile time, same rationale as the System Admin page's
      // own copy of this switch (#414) — see `lib/api/hr-ai-config.ts`'s
      // `AICapabilityState` docstring for why this union is duplicated
      // rather than shared.
      const unhandled: never = s;
      return { text: unhandled, color: 'bg-slate-100 text-slate-500' };
    }
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
