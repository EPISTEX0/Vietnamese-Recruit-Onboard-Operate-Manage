'use client';

import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations, useLocale } from 'next-intl';
import {
  Settings as SettingsIcon, Bot, ShieldCheck, Activity, FileText, Users, Mail, Plus,
  Trash2, ChevronLeft, ChevronRight, Loader2, Check, X, AlertCircle, Cpu,
  RefreshCw, FlaskConical, Zap, ShieldAlert, Sparkles, Calendar,
} from 'lucide-react';
import { motion } from 'motion/react';
import * as admin from '@/lib/api/admin';
import type {
  OrganizationAIConfiguration, RuntimeHealthResponse, AuditLog, AdminUser,
  WhitelistEntry, AssistantToolConfig,
} from '@/lib/api/admin';
import { useSession } from '@/lib/auth/session';
import { STAFF_ROLES, USER_ROLES, type StaffRole, type UserRole } from '@/lib/auth/roles';
import { staffAccountCreateSchema } from '@/lib/api/admin-schemas';
import { ApiError } from '@/lib/api/types';
import { getErrorMessage } from '@/lib/api/error-codes';
import { AUDIT_ACTION_LABELS, AUDIT_ACTION_GROUPS, formatAuditDetails, SERVICE_LABELS, formatRuntimeDetail, formatLatency } from '@/components/shared-ui';

function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    // For validation errors, show field-level messages if available
    if (err.fieldErrors && Object.keys(err.fieldErrors).length > 0) {
      return Object.values(err.fieldErrors).join('; ');
    }
    // Use the specific message from the error, fall back to generic error code mapping
    return err.message || getErrorMessage(err.errorCode);
  }
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
function notif(set: (s: string) => void, err: unknown) { set(apiErrorText(err)); }

type TabId = 'ai' | 'tools' | 'health' | 'audit' | 'users' | 'whitelist' | 'domains';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const [tab, setTab] = useState<TabId>('ai');

  const TABS = [
    { id: 'ai' as const, label: t('aiConfig'), icon: Bot },
    { id: 'tools' as const, label: t('aiTools'), icon: Cpu },
    { id: 'health' as const, label: t('systemHealth'), icon: Activity },
    { id: 'audit' as const, label: t('auditLog'), icon: FileText },
    { id: 'users' as const, label: t('usersRoles'), icon: Users },
    { id: 'whitelist' as const, label: t('accessWhitelist'), icon: ShieldCheck },
    { id: 'domains' as const, label: t('emailDomains'), icon: Mail },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-xl bg-indigo-100 flex items-center justify-center">
          <SettingsIcon className="w-4 h-4 text-indigo-600" />
        </div>
        <h1 className="text-lg font-bold text-slate-900">{t('title')}</h1>
      </div>
      <div className="flex flex-wrap gap-1 pb-3 border-b border-slate-100">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`inline-flex items-center gap-1.5 text-[13px] font-medium px-3.5 py-2 rounded-lg transition-all ${
              tab === t.id
                ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-200'
                : 'text-slate-600 hover:bg-slate-100 hover:text-slate-800'
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'ai' && <AITab />}
      {tab === 'tools' && <ToolsTab />}
      {tab === 'health' && <HealthTab />}
      {tab === 'audit' && <AuditTab />}
      {tab === 'users' && <UsersTab />}
      {tab === 'whitelist' && <WhitelistTab />}
      {tab === 'domains' && <DomainsTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI Configuration
// ---------------------------------------------------------------------------
function AITab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data: cfg, isLoading, error } = useQuery<OrganizationAIConfiguration>({
    queryKey: ['ai-config'],
    queryFn: admin.getOrganizationAIConfiguration,
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
                <span>{t('updated')}: <strong className="text-slate-700 font-medium">{cfg.updated_at ? new Date(cfg.updated_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'}</strong></span>
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

function StatusBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">{label}</p>
      <p className="text-[12px] font-medium text-slate-700">{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Assistant tools registry
// ---------------------------------------------------------------------------
function ToolsTab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data, isLoading, error } = useQuery({ queryKey: ['assistant-tools'], queryFn: admin.listAssistantTools });
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (data?.tools) {
      const map: Record<string, boolean> = {};
      data.tools.forEach((t) => { map[t.tool_name] = t.enabled; });
      setDraft(map);
    }
  }, [data]);

  const saveMut = useMutation({
    mutationFn: () => admin.updateAssistantTools(draft),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['assistant-tools'] }); setMsg(null); },
    onError: (e) => notif(setMsg, e),
  });

  if (isLoading) return <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-10 block" />;
  if (error) return <ErrorBox text={apiErrorText(error)} onRetry={() => qc.invalidateQueries({ queryKey: ['assistant-tools'] })} />;

  const readTools = data?.tools.filter((t) => t.kind === 'read-tool' || t.kind === 'read') ?? [];
  const draftTools = data?.tools.filter((t) => t.kind === 'draft-tool' || t.kind === 'draft') ?? [];

  return (
    <SectionCard icon={<Cpu className="w-4 h-4 text-indigo-600" />} title={t('aiTools')} desc={t('toolsDesc')}>
      {msg && <div className="text-[13px] text-rose-600 mb-3 flex items-center gap-1.5"><AlertCircle className="w-3.5 h-3.5" />{msg}</div>}
      <div className="space-y-4">
        <div>
          <p className="text-[10px] font-semibold uppercase text-slate-400 tracking-wide mb-2">{t('readTools')}</p>
          <div className="space-y-1.5">
            {readTools.map((t) => <ToolRowCheckbox key={t.tool_name} t={t} draft={draft} setDraft={setDraft} />)}
            {readTools.length === 0 && <Empty text={t('noReadTools')} />}
          </div>
        </div>
        <div>
          <p className="text-[10px] font-semibold uppercase text-slate-400 tracking-wide mb-2">{t('draftTools')}</p>
          <div className="space-y-1.5">
            {draftTools.map((t) => <ToolRowCheckbox key={t.tool_name} t={t} draft={draft} setDraft={setDraft} />)}
            {draftTools.length === 0 && <Empty text={t('noDraftTools')} />}
          </div>
        </div>
      </div>
      <div className="mt-4">
        <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending} className="inline-flex items-center gap-2 h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-all shadow-sm shadow-indigo-200">
          {saveMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />} {t('saveChanges')}
        </button>
      </div>
    </SectionCard>
  );
}

function ToolRowCheckbox({ t, draft, setDraft }: { t: AssistantToolConfig; draft: Record<string, boolean>; setDraft: (d: Record<string, boolean>) => void }) {
      const ts = useTranslations('settings');
      const checked = draft[t.tool_name] ?? t.enabled;
      const nameKey = `tool_${t.tool_name}_name`;
      const descKey = `tool_${t.tool_name}_desc`;
      const displayName = ts.has(nameKey) ? ts(nameKey) : t.display_name;
      const description = ts.has(descKey) ? ts(descKey) : t.description;
      return (
        <label className="flex items-center gap-3 p-3 rounded-xl border border-slate-100 hover:bg-slate-50 cursor-pointer transition-colors">
          <input type="checkbox" checked={checked} onChange={(e) => setDraft({ ...draft, [t.tool_name]: e.target.checked })} className="w-4 h-4 rounded accent-indigo-600" />
          <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-1.5 flex-wrap">
                  <span className="text-[13px] font-medium text-slate-800">{displayName}</span>
                  <span className="text-[10px] text-slate-400 font-mono">{t.tool_name}</span>
                </div>
                {description && <p className="text-[11px] text-slate-500 mt-0.5">{description}</p>}
              </div>
            </label>
      );
    }

// ---------------------------------------------------------------------------
// Runtime health
// ---------------------------------------------------------------------------
function HealthTab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
      const locale = useLocale();
      const ts = useTranslations('system');
  const { data, isLoading, error, dataUpdatedAt } = useQuery<RuntimeHealthResponse>({
    queryKey: ['runtime-health'], queryFn: admin.getRuntimeHealth, staleTime: 30_000,
  });
  return (
    <SectionCard icon={<Activity className="w-4 h-4 text-indigo-600" />} title={t('systemHealth')} desc={t('healthDesc')} action={
      <button onClick={() => qc.invalidateQueries({ queryKey: ['runtime-health'] })} aria-label={t('refreshStatus')} title={t('refreshStatus')} className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors"><RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} /></button>
    }>
      {isLoading ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
        error ? <ErrorBox text={apiErrorText(error)} onRetry={() => qc.invalidateQueries({ queryKey: ['runtime-health'] })} /> :
        data ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[13px] text-slate-600">{t('overview')}:</span>
              <span className={`text-[12px] font-semibold px-2.5 py-0.5 rounded-full ${data.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : data.status === 'degraded' ? 'bg-amber-50 text-amber-700' : 'bg-rose-50 text-rose-700'}`}>
                {data.status === 'healthy' ? t('healthy') : data.status === 'degraded' ? t('degraded') : t('error')}
              </span>
              {dataUpdatedAt ? (
                <span className="text-[10px] text-slate-400">
                  · {t('updatedAt')} {formatRuntimeDetail(`last beat: ${dataUpdatedAt / 1000}`, locale)}
                </span>
              ) : null}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {data.services.map((s) => (
                <div key={s.name} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl" title={ts(s.name) ? `${ts(s.name)}: ${s.status === 'healthy' ? t('healthy') : s.status === 'unhealthy' ? t('error') : t('degraded')}` : undefined}>
                  <span>{s.status === 'healthy' ? '🟢' : s.status === 'unhealthy' ? '🔴' : '🟡'}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-medium text-slate-700">{ts(s.name) ?? s.name}</p>
                    {formatRuntimeDetail(s.detail, locale) && <p className="text-[11px] text-slate-400 truncate">{formatRuntimeDetail(s.detail, locale)}</p>}
                  </div>
                  {s.latency_ms !== null && <span className="text-[11px] text-slate-400">{formatLatency(s.latency_ms, locale)}</span>}
                </div>
              ))}
            </div>
          </div>
        ) : <Empty text={t('noData')} />}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Audit logs
// ---------------------------------------------------------------------------
function AuditTab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
      const locale = useLocale();
  const ta = useTranslations('audit');
  const [page, setPage] = useState(1);
  const [actionType, setActionType] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [datePreset, setDatePreset] = useState<string>('all');

  const applyDatePreset = (preset: string) => {
    setDatePreset(preset);
    const today = new Date();
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    if (preset === 'today') {
      setStartDate(fmt(today));
      setEndDate(fmt(today));
    } else if (preset === '7days') {
      const d = new Date(today); d.setDate(d.getDate() - 7);
      setStartDate(fmt(d));
      setEndDate(fmt(today));
    } else if (preset === '30days') {
      const d = new Date(today); d.setDate(d.getDate() - 30);
      setStartDate(fmt(d));
      setEndDate(fmt(today));
    } else {
      setStartDate('');
      setEndDate('');
    }
    setPage(1);
  };

  const params: admin.AuditLogQueryParams = { page, page_size: 15 };
  if (actionType) params.action_type = actionType;
  if (startDate) params.start_date = startDate;
  if (endDate) params.end_date = endDate;

  const { data, isLoading } = useQuery({ queryKey: ['audit-logs', params], queryFn: () => admin.getAuditLogs(params), staleTime: 30_000 });
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const datePresets = [
    { key: 'all', label: t('all') },
    { key: 'today', label: t('today') },
    { key: '7days', label: t('last7Days') },
    { key: '30days', label: t('last30Days') },
  ];

  return (
    <SectionCard icon={<FileText className="w-4 h-4 text-indigo-600" />} title={t('auditLog')} desc={t('auditLogDesc')}>
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select value={actionType} onChange={(e) => { setActionType(e.target.value); setPage(1); }} className="h-9 pl-3 pr-8 text-[13px] border border-slate-200 rounded-lg bg-slate-50 focus:bg-white focus:border-indigo-400 outline-none transition-all cursor-pointer max-w-[200px]">
          <option value="">{t('allActions')}</option>
          {AUDIT_ACTION_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.items.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
        <div className="flex items-center h-9 border border-slate-200 rounded-lg bg-slate-50 overflow-hidden">
          {datePresets.map((p) => (
            <button
              key={p.key}
              onClick={() => applyDatePreset(p.key)}
              className={`h-full px-2.5 text-[12px] font-medium transition-colors border-r border-slate-200 last:border-r-0 ${datePreset === p.key ? 'bg-indigo-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
            >
              {p.label}
            </button>
          ))}
        </div>
        {(startDate || endDate) && (
          <span className="text-[11px] text-slate-400">
            {startDate && `${t('from')} ${new Date(startDate).toLocaleDateString('vi-VN')}`}
            {startDate && endDate && ' → '}
            {endDate && `${t('to')} ${new Date(endDate).toLocaleDateString('vi-VN')}`}
          </span>
        )}
        <button onClick={() => qc.invalidateQueries({ queryKey: ['audit-logs'] })} className="h-9 px-3 text-[13px] font-medium rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors flex items-center gap-1.5 ml-auto"><RefreshCw className="w-3.5 h-3.5" /> {t('refresh')}</button>
      </div>
      {isLoading ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
        (data?.items?.length ?? 0) === 0 ? <Empty text={actionType || startDate || endDate ? t('noFilterResults') : t('noActivityYet')} /> :
        <div className="space-y-2">
          {data!.items.map((log: AuditLog) => (
            <div key={log.id} className="p-3 bg-slate-50 rounded-xl flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-indigo-100 text-indigo-600 flex items-center justify-center text-[11px] font-bold shrink-0">{log.admin_email?.[0]?.toUpperCase() ?? '?'}</div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-[13px] font-medium text-slate-700">{log.admin_email}</span>
                  <span className="text-[10px] bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded font-medium">{ta(log.action_type) ?? log.action_type}</span>
                </div>
                <p className="text-[12px] text-slate-500">{formatAuditDetails(log.details, locale)}</p>
              </div>
              <span className="text-[11px] text-slate-400 shrink-0">{new Date(log.created_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}</span>
            </div>
          ))}
          <div className="flex items-center justify-between pt-3">
            <span className="text-[12px] text-slate-500">{t('pageOf', { page, total: totalPages })} · {data!.total} {t('records')}</span>
            <div className="flex gap-1">
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className="p-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-30 transition-colors"><ChevronLeft className="w-4 h-4" /></button>
              <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-30 transition-colors"><ChevronRight className="w-4 h-4" /></button>
            </div>
          </div>
        </div>}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Users & role
// ---------------------------------------------------------------------------
function UsersTab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const tr = useTranslations('roles');
  const { user: currentUser } = useSession();
  const { data, isLoading } = useQuery<AdminUser[]>({ queryKey: ['admin-users'], queryFn: admin.listUsers });
  const [roleError, setRoleError] = useState<string | null>(null);
  const roleMut = useMutation({
    mutationFn: ({ id, role }: { id: string; role: UserRole }) => admin.updateUserRole(id, role),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-users'] }); setRoleError(null); },
    onError: (err: unknown) => { setRoleError(apiErrorText(err)); },
  });

  const handleRoleChange = (targetUser: AdminUser, newRole: UserRole) => {
    if (targetUser.id === currentUser?.id) return; // Self-change blocked at UI level
    if (newRole === targetUser.role) return;
    const action = t('assignRole', { role: tr(newRole) });
    if (!window.confirm(t('confirmRoleChange', { action, name: targetUser.name }))) return;
    roleMut.mutate({ id: targetUser.id, role: newRole });
  };

  const isSelf = (userId: string) => currentUser?.id === userId;

  return (
    <SectionCard icon={<Users className="w-4 h-4 text-indigo-600" />} title={t('usersRoles')} desc={t('usersRolesDesc')}>
      {roleError && (
        <div className="mb-3 p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{roleError}</span>
        </div>
      )}
      <CreateStaffAccount />
      {isLoading ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
        (data?.length ?? 0) === 0 ? <Empty text={t('noUsers')} /> :
        <div className="space-y-2">
          {data!.map((u) => (
            <div key={u.id} className={`flex items-center gap-3 p-3 rounded-xl transition-colors ${isSelf(u.id) ? 'bg-indigo-50 border border-indigo-100' : 'bg-slate-50'}`}>
              <div className={`w-9 h-9 rounded-xl flex items-center justify-center text-sm font-bold shrink-0 ${isSelf(u.id) ? 'bg-indigo-600 text-white' : 'bg-indigo-100 text-indigo-600'}`}>{u.name?.[0] ?? '?'}</div>
              <div className="flex-1 min-w-0">
                <p className="text-[13px] font-medium text-slate-800">
                  {u.name}
                  {isSelf(u.id) && <span className="ml-1.5 text-[10px] font-medium bg-indigo-600 text-white px-1.5 py-0.5 rounded">{t('you')}</span>}
                </p>
                <p className="text-[11px] text-slate-400">{u.email} · {t('created')} {new Date(u.created_at).toLocaleDateString('vi-VN')}</p>
              </div>
              <select
                value={u.role}
                onChange={(e) => handleRoleChange(u, e.target.value as UserRole)}
                disabled={roleMut.isPending || isSelf(u.id)}
                className={`h-9 px-3 text-[13px] border border-slate-200 rounded-lg bg-white outline-none transition-all ${isSelf(u.id) ? 'cursor-not-allowed opacity-60 text-slate-400' : 'focus:border-indigo-400 cursor-pointer'}`}
                title={isSelf(u.id) ? t('cannotSelfChange') : undefined}
              >
                {USER_ROLES.map((r) => (
                  <option key={r} value={r}>{tr(r)}</option>
                ))}
              </select>
            </div>
          ))}
        </div>}
    </SectionCard>
  );
}

/**
 * Provision an HR or System Admin account.
 *
 * First-run setup mints one System Admin and every other account-creation
 * route sits behind HR, so this form is the only way a fresh deployment gets
 * its first HR account. The temporary password is shown once and never again.
 */
function CreateStaffAccount() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const tr = useTranslations('roles');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<{ email: string; name: string; role: StaffRole }>({
    email: '', name: '', role: 'hr',
  });
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<admin.StaffAccountCreateResponse | null>(null);

  const parsed = staffAccountCreateSchema.safeParse(form);

  const createMut = useMutation({
    mutationFn: () => admin.createStaffAccount(staffAccountCreateSchema.parse(form)),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      setCreated(res);
      setForm({ email: '', name: '', role: 'hr' });
      setError(null);
    },
    onError: (e) => setError(apiErrorText(e)),
  });

  if (!open) {
    return (
      <div className="mb-4">
        <button
          onClick={() => { setOpen(true); setCreated(null); setError(null); }}
          className="inline-flex items-center gap-1.5 h-9 px-4 text-[13px] font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-all shadow-sm shadow-indigo-200"
        >
          <Plus className="w-4 h-4" /> {t('createStaffAccount')}
        </button>
      </div>
    );
  }

  return (
    <div className="mb-4 p-4 rounded-xl border border-indigo-100 bg-indigo-50/40 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-semibold text-slate-800">{t('createStaffAccount')}</p>
        <button onClick={() => setOpen(false)} className="p-1 text-slate-400 hover:text-slate-600" aria-label={t('close')}>
          <X className="w-4 h-4" />
        </button>
      </div>
      <p className="text-[12px] text-slate-500">{t('createStaffAccountDesc')}</p>

      {error && (
        <div className="p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg text-[12px] flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {created ? (
        <div className="p-3 bg-white rounded-lg border border-emerald-200 space-y-1.5">
          <p className="text-[13px] font-medium text-emerald-700 flex items-center gap-1.5">
            <Check className="w-4 h-4" /> {t('staffAccountCreated', { email: created.user.email })}
          </p>
          <p className="text-[12px] text-slate-600">
            {t('temporaryPassword')}: <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">{created.temporary_password}</code>
          </p>
          <p className="text-[11px] text-amber-600">{t('temporaryPasswordNotice')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder={t('staffName')}
            className="h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400"
          />
          <input
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder={t('staffEmail')}
            className="h-10 px-3.5 text-[13px] border border-slate-200 rounded-xl bg-white focus:border-indigo-400 outline-none transition-all placeholder:text-slate-400"
          />
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as StaffRole })}
            className="h-10 px-3 text-[13px] border border-slate-200 rounded-xl bg-white focus:border-indigo-400 outline-none transition-all cursor-pointer"
          >
            {STAFF_ROLES.map((r) => (
              <option key={r} value={r}>{tr(r)}</option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-center gap-2">
        {created ? (
          <button
            onClick={() => setCreated(null)}
            className="h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-all"
          >
            {t('createAnother')}
          </button>
        ) : (
          <button
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending || !parsed.success}
            className="inline-flex items-center gap-1.5 h-10 px-5 text-sm font-semibold rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-all"
          >
            {createMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            {t('create')}
          </button>
        )}
        {!created && !parsed.success && (form.email || form.name) && (
          <span className="text-[11px] text-slate-400">{parsed.error.issues[0]?.message}</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Whitelist
// ---------------------------------------------------------------------------
function WhitelistTab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data, isLoading } = useQuery({ queryKey: ['whitelist'], queryFn: admin.listWhitelist });
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
    <SectionCard icon={<ShieldCheck className="w-4 h-4 text-indigo-600" />} title={t('accessWhitelist')} desc={t('whitelistDesc')}>
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
      {isLoading ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
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
  );
}

// ---------------------------------------------------------------------------
// Domains
// ---------------------------------------------------------------------------
function DomainsTab() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data, isLoading } = useQuery({ queryKey: ['org-domains'], queryFn: admin.listDomains });
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
    <SectionCard icon={<Mail className="w-4 h-4 text-indigo-600" />} title={t('emailDomains')} desc={t('domainsDesc')}>
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
      {isLoading ? <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-5 block" /> :
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
  );
}

// ---------------------------------------------------------------------------
// Shared UI
// ---------------------------------------------------------------------------
function SectionCard({ icon, title, desc, action, children }: {
  icon: React.ReactNode; title: string; desc?: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center shrink-0">{icon}</div>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-bold text-slate-900">{title}</h2>
          {desc && <p className="text-[12px] text-slate-500">{desc}</p>}
        </div>
        {action}
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function ErrorBox({ text, onRetry }: { text: string; onRetry: () => void }) {
  const t = useTranslations('settings');
  return (
    <div className="flex items-center gap-3 p-4 bg-rose-50 border border-rose-200 rounded-xl text-[13px] text-rose-700">
      <AlertCircle className="w-5 h-5 shrink-0" /><span className="flex-1">{text}</span>
      <button onClick={onRetry} className="font-medium underline hover:text-rose-800">{t('retry')}</button>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-[13px] text-slate-400 py-10 text-center">{text}</p>;
}
