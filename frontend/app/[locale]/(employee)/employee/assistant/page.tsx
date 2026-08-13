'use client';
import { useTranslations } from 'next-intl';

import AiChat from '@/components/AiChat';
import type { AiChatApi } from '@/components/AiChat';
import {
  sendEmployeeChatMessage,
  sendEmployeeStreamMessage,
  startEmployeeSession,
  endEmployeeSession,
  sendEmployeeFeedback,
  confirmEmployeeDraftAction,
} from '@/lib/api/employee-assistant';
import { Sparkles } from 'lucide-react';

/**
 * Employee AI Assistant (ESS) page.
 *
 * employee_id is taken from the session on the backend — never from the LLM.
 * The assistant only READs the employee's own data and DRAFTs the employee's
 * own requests (leave / overtime). Confirming a draft fires the real
 * `/api/employee-requests/me/*` write endpoint directly (the employee is the
 * human-in-the-loop). The LLM never writes.
 */
const employeeAssistantApi: AiChatApi = {
  sendMessage: sendEmployeeChatMessage,
  sendStreamMessage: sendEmployeeStreamMessage,
  confirmAction: confirmEmployeeDraftAction,
  startSession: startEmployeeSession,
  endSession: endEmployeeSession,
  sendFeedback: sendEmployeeFeedback,
  // ESS assistant has no separate decision-audit endpoint; recordDecision unused.
};

export default function EmployeeAssistantPage() {
 const t = useTranslations('employee');

  return (
    <div className="space-y-4 h-full">
      <div className="flex items-center gap-2 text-indigo-600">
        <Sparkles className="w-5 h-5" />
        <h1 className="text-xl font-bold text-slate-900">{t('aiAssistant')}</h1>
      </div>
      <p className="text-sm text-slate-500 -mt-2">
        {t('aiAssistantDesc')}
      </p>

      <div className="h-[calc(100vh-13rem)]">
        <AiChat assistantType="employee" api={employeeAssistantApi} className="h-full" />
      </div>
    </div>
  );
}