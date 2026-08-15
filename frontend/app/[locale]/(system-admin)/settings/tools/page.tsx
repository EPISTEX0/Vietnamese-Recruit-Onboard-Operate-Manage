'use client';

/**
 * Assistant tools — the on/off registry for the AI Assistant's read-tools and
 * draft-tools. Nothing here touches AI Automation; that lives in AI
 * configuration.
 */

import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { Cpu, Loader2, Check, AlertCircle } from 'lucide-react';
import * as admin from '@/lib/api/admin';
import type { AssistantToolConfig } from '@/lib/api/admin';
import { PageHeader } from '@/components/shared-ui';
import { SectionCard, ErrorBox, Empty } from '../_components/console-ui';
import { apiErrorText } from '../_components/api-error-text';

export default function AssistantToolsPage() {
  const t = useTranslations('settings');
  return (
    <div className="space-y-5">
      <PageHeader icon={Cpu} title={t('aiTools')} subtitle={t('toolsDesc')} />
      <AssistantToolsRegistry />
    </div>
  );
}

function AssistantToolsRegistry() {
  const qc = useQueryClient();
  const t = useTranslations('settings');
  const { data, isLoading, error } = useQuery({ queryKey: ['assistant-tools'], queryFn: admin.listAssistantTools });
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (data?.tools) {
      const map: Record<string, boolean> = {};
      data.tools.forEach((tool) => { map[tool.tool_name] = tool.enabled; });
      setDraft(map);
    }
  }, [data]);

  const saveMut = useMutation({
    mutationFn: () => admin.updateAssistantTools(draft),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['assistant-tools'] }); setMsg(null); },
    onError: (e) => setMsg(apiErrorText(e)),
  });

  if (isLoading) return <Loader2 className="w-6 h-6 animate-spin text-indigo-400 mx-auto mt-10 block" />;
  if (error) return <ErrorBox text={apiErrorText(error)} onRetry={() => qc.invalidateQueries({ queryKey: ['assistant-tools'] })} />;

  const readTools = data?.tools.filter((tool) => tool.kind === 'read-tool' || tool.kind === 'read') ?? [];
  const draftTools = data?.tools.filter((tool) => tool.kind === 'draft-tool' || tool.kind === 'draft') ?? [];

  return (
    <SectionCard>
      {msg && <div className="text-[13px] text-rose-600 mb-3 flex items-center gap-1.5"><AlertCircle className="w-3.5 h-3.5" />{msg}</div>}
      <div className="space-y-4">
        <div>
          <p className="text-[10px] font-semibold uppercase text-slate-400 tracking-wide mb-2">{t('readTools')}</p>
          <div className="space-y-1.5">
            {readTools.map((tool) => <ToolRowCheckbox key={tool.tool_name} t={tool} draft={draft} setDraft={setDraft} />)}
            {readTools.length === 0 && <Empty text={t('noReadTools')} />}
          </div>
        </div>
        <div>
          <p className="text-[10px] font-semibold uppercase text-slate-400 tracking-wide mb-2">{t('draftTools')}</p>
          <div className="space-y-1.5">
            {draftTools.map((tool) => <ToolRowCheckbox key={tool.tool_name} t={tool} draft={draft} setDraft={setDraft} />)}
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
