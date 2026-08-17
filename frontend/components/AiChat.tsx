'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useFormatter } from 'next-intl';
import {
  Sparkles, Send, Bot, User, Check, AlertCircle, RefreshCw,
  ChevronDown, ChevronUp, Loader2, ThumbsUp, ThumbsDown, X, FileEdit,
  Plus, Clock, CheckCircle, Square, Zap, Search, Mail, Calendar,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import type {
  ChatMessage, DraftAction, ChatResponse, AssistantFeedbackRequest,
  SessionStartResponse, SSEEvent,
} from '@/lib/api/assistant';
import { sendStreamMessage } from '@/lib/api/assistant';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface AiChatApi {
  sendMessage: (messages: ChatMessage[], sessionId?: string) => Promise<ChatResponse>;
  sendStreamMessage?: (messages: ChatMessage[], onEvent: (event: SSEEvent) => void, onError: (error: Error) => void, onDone: () => void, sessionId?: string) => () => void;
  confirmAction: (draft: DraftAction) => Promise<unknown>;
  startSession: (assistantType: 'hr' | 'employee') => Promise<SessionStartResponse>;
  endSession: (sessionId: string) => Promise<void>;
  sendFeedback?: (feedback: AssistantFeedbackRequest) => Promise<void>;
  recordDecision?: (draft: DraftAction, decision: 'confirm' | 'reject') => Promise<void>;
}

export interface AiChatProps {
  assistantType: 'hr' | 'employee';
  api: AiChatApi;
  title?: string;
  description?: string;
  suggestions?: string[];
  onOpenRequestDialog?: (values: {
    leave?: Record<string, string>;
    overtime?: Record<string, string>;
  }) => void | Promise<void>;
  defaultOpen?: boolean;
  className?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function nowTime(format: ReturnType<typeof useFormatter>): string {
  return format.dateTime(new Date(), 'time');
}

function toUserError(err: unknown): string {
  if (err instanceof Error) {
    const msg = err.message;
    if (msg.includes('TIMEOUT') || msg.includes('hết thời gian'))
      return 'Yêu cầu đã hết thời gian chờ — vui lòng thử lại.';
    if (msg.includes('NETWORK') || msg.includes('mạng'))
      return 'Lỗi kết nối mạng — vui lòng kiểm tra kết nối và thử lại.';
    if (msg.includes('500') || msg.includes('Internal Server'))
      return 'Lỗi máy chủ — vui lòng thử lại sau.';
    if (msg.includes('503'))
      return 'Dịch vụ AI chưa được cấu hình — vào Cấu hình AI & Hệ thống để thiết lập.';
    if (msg.includes('404'))
      return 'Không tìm thấy tài nguyên — vui lòng thử lại.';
    if (msg.includes('403'))
      return 'Bạn không có quyền thực hiện thao tác này.';
    return msg;
  }
  return 'Không thể kết nối trợ lý — vui lòng thử lại.';
}

    /** Simple markdown-like rendering: **bold**, *italic*, `code`, newlines */
    function renderMarkdown(text: string): React.ReactNode {
      const parts = text.split(/(\*\*.*?\*\*|\*.*?\*|`.*?`)/g);
      return parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**'))
          return <strong key={i} className="text-slate-900 font-semibold">{part.slice(2, -2)}</strong>;
        if (part.startsWith('*') && part.endsWith('*') && !part.startsWith('**'))
          return <em key={i} className="italic text-slate-600">{part.slice(1, -1)}</em>;
        if (part.startsWith('`') && part.endsWith('`'))
          return <code key={i} className="bg-slate-200 text-slate-800 px-1 py-0.5 rounded text-[11px] font-mono">{part.slice(1, -1)}</code>;
        return <span key={i}>{part}</span>;
      });
    }

    /** Extract citation sources from assistant text (📎 Nguồn: ...) */
    function extractCitation(text: string): { body: string; sources: string[] } | null {
      const match = text.match(/^([\s\S]*?)\n*📎\s*Nguồn:\s*(.+?)\s*$/);
      if (!match) return null;
      const sources = match[2].split(/,\s*/).map(s => s.trim()).filter(Boolean);
      if (sources.length === 0) return null;
      return { body: match[1].trim(), sources };
    }

const TOOL_ICONS: Record<string, React.ReactNode> = {
  search_candidates: <Search className="w-3 h-3" />,
  count_candidates_by_status: <Search className="w-3 h-3" />,
  get_candidate_parsed_cv: <FileEdit className="w-3 h-3" />,
  list_job_openings: <Zap className="w-3 h-3" />,
  get_department_info: <Zap className="w-3 h-3" />,
  list_interviews_for_candidate: <Calendar className="w-3 h-3" />,
  list_in_progress_onboarding: <RefreshCw className="w-3 h-3" />,
  get_onboarding_task_details: <Check className="w-3 h-3" />,
  draft_interview_invitation: <Mail className="w-3 h-3" />,
      draft_congratulations_email: <Mail className="w-3 h-3" />,
      // Employee tools
      get_my_profile: <User className="w-3 h-3" />,
      list_my_documents: <FileEdit className="w-3 h-3" />,
      get_today_attendance: <Clock className="w-3 h-3" />,
      list_my_attendance_records: <Clock className="w-3 h-3" />,
      list_my_employee_requests: <FileEdit className="w-3 h-3" />,
      get_my_leave_balance: <Calendar className="w-3 h-3" />,
      list_my_payslips: <FileEdit className="w-3 h-3" />,
      draft_leave_request: <Calendar className="w-3 h-3" />,
      draft_overtime_request: <Clock className="w-3 h-3" />,
    };

const TOOL_LABELS: Record<string, string> = {
  search_candidates: 'Tìm ứng viên',
  count_candidates_by_status: 'Thống kê pipeline',
  get_candidate_parsed_cv: 'Đọc CV',
  list_job_openings: 'Danh sách vị trí',
  get_department_info: 'Thông tin phòng ban',
  list_interviews_for_candidate: 'Lịch phỏng vấn',
  list_in_progress_onboarding: 'Onboarding',
  get_onboarding_task_details: 'Chi tiết onboarding',
  draft_interview_invitation: 'Soạn thư mời',
      draft_congratulations_email: 'Soạn thư chúc mừng',
      // Employee tools
      get_my_profile: 'Hồ sơ của tôi',
      list_my_documents: 'Tài liệu của tôi',
      get_today_attendance: 'Chấm công hôm nay',
      list_my_attendance_records: 'Lịch sử chấm công',
      list_my_employee_requests: 'Yêu cầu của tôi',
      get_my_leave_balance: 'Số dư nghỉ phép',
      list_my_payslips: 'Bảng lương của tôi',
      draft_leave_request: 'Soạn đơn nghỉ phép',
      draft_overtime_request: 'Soạn đơn tăng ca',
    };

const LOADING_STEPS = [
  'Đang phân tích câu hỏi...',
  'Đang truy vấn dữ liệu...',
  'Đang xử lý kết quả...',
  'Đang soạn câu trả lời...',
];

// ---------------------------------------------------------------------------
// Draft Action card
// ---------------------------------------------------------------------------

const ACTION_LABELS: Record<string, string> = {
  draft_interview_invitation: 'Thư mời phỏng vấn',
  draft_congratulations_email: 'Email chúc mừng (Offer)',
  submit_leave_request: 'Đơn nghỉ phép',
  submit_overtime_request: 'Đơn tăng ca',
};

function friendlyParams(draft: DraftAction): { label: string; value: string }[] {
  const p = draft.parameters ?? {};
  const out: { label: string; value: string }[] = [];
  const push = (label: string, value: unknown) => {
    if (value === undefined || value === null || value === '') return;
    out.push({ label, value: String(value) });
  };
  push('Ứng viên', p.candidate_name ?? p.candidateName);
  push('Vị trí', p.job_title ?? p.jobTitle);
  push('Thời gian', (p.date_time ?? p.dateTime)?.toString()?.replace('T', ' '));
  push('Nhân viên', p.employee_name ?? p.employeeName);
  push('Từ ngày', p.start_date ?? p.startDate);
  push('Đến ngày', p.end_date ?? p.endDate);
  push('Ngày làm thêm', p.work_date ?? p.workDate);
  push('Lý do', p.reason);
  push('Dự án/Công việc', p.project_or_task ?? p.projectOrTask);
  return out;
}

function DraftActionCard({
  draft, onConfirm, onReject, onOpenRequest,
}: {
  draft: DraftAction;
  onConfirm: () => void;
  onReject: () => void;
  onOpenRequest?: () => void;
}) {
  const [state, setState] = useState<'idle' | 'pending' | 'done' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    setState('pending');
    setError(null);
    try { await onConfirm(); setState('done'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Lỗi không xác định'); setState('error'); }
  };

  const handleReject = async () => {
    setState('pending');
    try { await onReject(); setState('idle'); }
    catch { setState('idle'); }
  };

  if (state === 'done') {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="flex items-center gap-2 px-3 py-2.5 bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs rounded-xl font-medium"
      >
        <CheckCircle className="w-4 h-4 text-emerald-500" />
        Đã xác nhận và ghi thành công.
      </motion.div>
    );
  }

  const label = ACTION_LABELS[draft.action_type] ?? `Draft Action — ${draft.action_type}`;
  const params = friendlyParams(draft);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className="p-4 bg-white rounded-xl border border-dashed border-indigo-300/80 space-y-3 shadow-sm"
    >
      <div className="flex items-center justify-between border-b border-slate-100 pb-2">
        <span className="inline-flex items-center gap-1.5 text-[10px] font-mono text-indigo-600 uppercase tracking-wider font-semibold">
          <FileEdit className="w-3 h-3" />
          Đề xuất từ AI
        </span>
        <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold border bg-amber-50 text-amber-700 border-amber-100">
          {label}
        </span>
      </div>

      {params.length > 0 && (
        <div className="text-xs space-y-1.5 text-slate-700">
          {params.map((row) => (
            <div key={row.label} className="flex gap-2">
              <span className="text-slate-400 shrink-0 min-w-[60px]">{row.label}</span>
              <span className="text-slate-900 font-semibold break-words">{row.value}</span>
            </div>
          ))}
        </div>
      )}

      {draft.preview && (
        <div className="text-xs text-slate-600 max-h-40 overflow-y-auto p-3 bg-slate-50 rounded-lg border border-slate-200 whitespace-pre-wrap leading-relaxed">
          {draft.preview}
        </div>
      )}

      {draft.provenance && Object.keys(draft.provenance).length > 0 && (
        <details className="text-[10px] text-slate-400">
          <summary className="cursor-pointer hover:text-slate-600 select-none">Nguồn dữ liệu</summary>
          <pre className="mt-1 p-2 bg-slate-50 rounded overflow-x-auto font-mono text-[9px] leading-relaxed">
            {JSON.stringify(draft.provenance, null, 2)}
          </pre>
        </details>
      )}

      {error && (
        <div className="text-[11px] text-rose-600 bg-rose-50 border border-rose-100 p-2 rounded-lg flex items-start gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span className="break-words">{error}</span>
        </div>
      )}

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={handleConfirm}
          disabled={state === 'pending'}
          className="flex-1 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold text-xs rounded-lg transition-all shadow-sm flex items-center justify-center gap-1.5"
        >
          {state === 'pending'
            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Đang xử lý...</>
            : <><Check className="w-3.5 h-3.5" /> {onOpenRequest ? 'Mở form' : 'Xác nhận & Ghi dữ liệu'}</>
          }
        </button>
        <button
          type="button"
          onClick={handleReject}
          disabled={state === 'pending'}
          className="px-4 py-2.5 bg-white hover:bg-slate-50 border border-slate-200 text-slate-600 font-medium text-xs rounded-lg transition-all flex items-center gap-1"
        >
          <X className="w-3.5 h-3.5" /> Hủy
        </button>
      </div>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Feedback row
// ---------------------------------------------------------------------------

function FeedbackRow({
  messageIndex, sessionId, sendFeedback, onToast,
}: {
  messageIndex: number;
  sessionId?: string;
  sendFeedback?: AiChatApi['sendFeedback'];
  onToast?: (msg: string) => void;
}) {
  const [sent, setSent] = useState<'up' | 'down' | null>(null);
  if (!sendFeedback || !sessionId) return null;

  const submit = (type: 'up' | 'down') => {
    setSent(type);
    sendFeedback({ session_id: sessionId, message_index: messageIndex, feedback_type: type })
      .then(() => onToast?.('Cảm ơn phản hồi của bạn!'))
      .catch(() => onToast?.('Không thể gửi phản hồi.'));
  };

  return (
    <div className="flex items-center gap-0.5 mt-1.5">
      <button
        type="button"
        onClick={() => submit('up')}
        className={`p-1 rounded-md transition-all ${sent === 'up' ? 'text-indigo-600 scale-110' : 'text-slate-300 hover:text-indigo-400 hover:bg-indigo-50'}`}
        aria-label="Hữu ích"
      >
        <ThumbsUp className="w-3 h-3" />
      </button>
      <button
        type="button"
        onClick={() => submit('down')}
        className={`p-1 rounded-md transition-all ${sent === 'down' ? 'text-rose-500 scale-110' : 'text-slate-300 hover:text-rose-400 hover:bg-rose-50'}`}
        aria-label="Không hữu ích"
      >
        <ThumbsDown className="w-3 h-3" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main AiChat panel
// ---------------------------------------------------------------------------

export default function AiChat({
  assistantType, api, title, description, suggestions,
  onOpenRequestDialog, defaultOpen = true, className,
}: AiChatProps) {
  const format = useFormatter();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [draftAction, setDraftAction] = useState<DraftAction | null>(null);
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [toast, setToast] = useState<string | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [toolStatus, setToolStatus] = useState<{ name: string; label: string; done: boolean } | null>(null);

  const sessionIdRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const loadingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelStreamRef = useRef<(() => void) | null>(null);
  const finalizedRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const titleText = title ?? (assistantType === 'hr' ? 'Trợ lý AI Vroom HR' : 'Trợ lý Nhân viên (ESS)');
  const descText = description ?? (assistantType === 'hr'
    ? 'Hỏi về dữ liệu tuyển dụng / onboarding; soạn nháp action cho HR xác nhận.'
    : 'Hỏi về chấm công, phép; soạn nháp yêu cầu của bạn.');
  const chipSuggestions = suggestions ?? (assistantType === 'hr'
    ? ['Có bao nhiêu candidate đang reviewing?', 'Onboarding nào đang chạy?', 'Soạn email chúc mừng cho Nguyễn Văn A']
    : ['Số dư phép của tôi?', 'Soạn đơn nghỉ phép 2 ngày', 'Lịch sử chấm công tháng này']);

  // Toast auto-dismiss
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  // Session lifecycle
  useEffect(() => {
    mountedRef.current = true;
    setSessionReady(false);
    api.startSession(assistantType)
      .then((res) => { if (mountedRef.current) { sessionIdRef.current = res.session_id; setSessionReady(true); } })
      .catch(() => { if (mountedRef.current) setSessionReady(true); });
    return () => {
      mountedRef.current = false;
      const sid = sessionIdRef.current;
      if (sid) api.endSession(sid).catch(() => {});
    };
  }, [api, assistantType]);

  // Loading progress animation
  useEffect(() => {
    if (loading) {
      loadingIntervalRef.current = setInterval(() => {
        setLoadingStep((prev) => (prev + 1) % LOADING_STEPS.length);
      }, 2000);
    } else {
      if (loadingIntervalRef.current) { clearInterval(loadingIntervalRef.current); loadingIntervalRef.current = null; }
      setLoadingStep(0);
    }
    return () => { if (loadingIntervalRef.current) { clearInterval(loadingIntervalRef.current); loadingIntervalRef.current = null; } };
  }, [loading]);

  // Smooth auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingText, loading]);

  // Keyboard shortcut: Ctrl+J
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'j') {
        e.preventDefault();
        textareaRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // Auto-resize textarea
  const handleTextareaChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
  }, []);

  // New chat
  const handleNewChat = useCallback(async () => {
    cancelStreamRef.current?.();
    const sid = sessionIdRef.current;
    if (sid) await api.endSession(sid).catch(() => {});
    setMessages([]);
    setInput('');
    setError(null);
    setDraftAction(null);
    setStreamingText('');
    setToolStatus(null);
    setLoading(false);
    finalizedRef.current = false;
            setSessionReady(false);
    api.startSession(assistantType)
      .then((res) => { if (mountedRef.current) { sessionIdRef.current = res.session_id; setSessionReady(true); } })
      .catch(() => { if (mountedRef.current) setSessionReady(true); });
    textareaRef.current?.focus();
  }, [api, assistantType]);

  // Send with SSE streaming
  const handleSend = useCallback(
    (retryText?: string) => {
      const text = (retryText ?? input).trim();
      if (!text || loading) return;
      cancelStreamRef.current?.();

      const userMessage: ChatMessage = { role: 'user', content: text };
      const history = [...messages, userMessage];
      setMessages(history);
      setInput('');
      if (textareaRef.current) textareaRef.current.style.height = 'auto';
      setError(null);
      setDraftAction(null);
      setStreamingText('');
      setToolStatus(null);
      setLoading(true);
      finalizedRef.current = false;

          const streamFn = api.sendStreamMessage ?? sendStreamMessage;
          const cancel = streamFn(
            history,
            // onEvent — SSE events
            (event: SSEEvent) => {
          if (finalizedRef.current) return;
          switch (event.event) {
            case 'text_delta':
              setStreamingText((prev) => prev + (event.data.content as string || ''));
              break;
            case 'tool_start': {
              const name = event.data.name as string;
              setToolStatus({ name, label: TOOL_LABELS[name] || name, done: false });
              break;
            }
            case 'tool_end': {
              const name = event.data.name as string;
              setToolStatus({ name, label: TOOL_LABELS[name] || name, done: true });
              break;
            }
            case 'draft_action':
              setDraftAction(event.data as unknown as DraftAction);
              break;
            case 'done':
              if (finalizedRef.current) break;
              finalizedRef.current = true;
              setLoading(false);
              setToolStatus(null);
              // Read streamingText directly via ref trick: capture in closure
              setStreamingText((current) => {
                if (current) {
                  // Use flushSync-like pattern: append directly in same microtask
                  setTimeout(() => {
                    setMessages((prev) => {
                      // Dedup: only append if not already present
                      const last = prev[prev.length - 1];
                      if (last?.role === 'assistant' && last.content === current) return prev;
                      return [...prev, { role: 'assistant', content: current }];
                    });
                  }, 0);
                }
                return '';
              });
              break;
            case 'error':
              finalizedRef.current = true;
              setLoading(false);
              setToolStatus(null);
              setStreamingText('');
              setError(toUserError(event.data.message || 'Lỗi không xác định'));
              setInput(text);
              break;
          }
        },
        // onError — network/fatal error
        (err) => {
          if (finalizedRef.current) return;
          finalizedRef.current = true;
          setLoading(false);
          setToolStatus(null);
          setStreamingText('');
          setError(toUserError(err));
          setInput(text);
        },
        // onDone — cleanup only, finalization handled by SSE events
        () => {
          setLoading(false);
          setToolStatus(null);
        },
        sessionIdRef.current ?? undefined,
      );
      cancelStreamRef.current = cancel;
    },
    [input, loading, messages],
  );

  // Stop generation
  const handleStop = useCallback(() => {
    cancelStreamRef.current?.();
    finalizedRef.current = true;
    setLoading(false);
    setToolStatus(null);
    setStreamingText((current) => {
      if (current) {
        setMessages((prev) => [...prev, { role: 'assistant', content: current + ' [Đã dừng]' }]);
      }
      return '';
    });
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const handleDraftConfirm = useCallback(async () => {
    if (!draftAction) return;
    if (onOpenRequestDialog && (draftAction.action_type === 'submit_leave_request' || draftAction.action_type === 'submit_overtime_request')) {
      const b = draftAction.confirm_body as Record<string, unknown>;
      const values: { leave?: Record<string, string>; overtime?: Record<string, string> } = {};
      if (draftAction.action_type === 'submit_leave_request') {
        values.leave = { leave_type: String(b.leave_type ?? ''), start_date: String(b.start_date ?? ''), end_date: String(b.end_date ?? ''), reason: String(b.reason ?? '') };
      } else {
        values.overtime = { work_date: String(b.work_date ?? ''), start_time: String(b.start_time ?? ''), end_time: String(b.end_time ?? ''), reason: String(b.reason ?? ''), project_or_task: String(b.project_or_task ?? '') };
      }
      await Promise.resolve(onOpenRequestDialog(values));
      setDraftAction(null);
      return;
    }
        const actionLabel = ACTION_LABELS[draftAction.action_type] ?? draftAction.action_type;
        // DraftActionCard already provides confirm/cancel UI — confirm proceeds directly
        try {
      await api.recordDecision?.(draftAction, 'confirm');
      await api.confirmAction(draftAction);
      setMessages((prev) => [...prev, { role: 'assistant', content: `✅ Đã thực hiện thành công: ${actionLabel} — ${draftAction.preview ?? ''}` }]);
      setToast('Thao tác đã được thực hiện thành công!');
    } catch (err) {
      setToast(`Thất bại: ${toUserError(err)}`);
      throw err;
    }
    setDraftAction(null);
  }, [api, draftAction, onOpenRequestDialog]);

  const handleDraftReject = useCallback(async () => {
    if (!draftAction) return;
    try { await api.recordDecision?.(draftAction, 'reject'); } catch {}
    setDraftAction(null);
    setToast('Đã hủy đề xuất.');
  }, [api, draftAction]);

  return (
    <div
      id="ai-assistant-chat-panel"
      className={`flex flex-col bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-lg shadow-slate-100/50 transition-all duration-300 ${className ?? 'h-full'}`}
    >
      {/* ── Header ── */}
      <div className="flex items-center justify-between px-4 py-3 bg-gradient-to-r from-slate-900 to-slate-800 border-b border-slate-700 select-none shrink-0">
        <div onClick={() => setIsOpen((o) => !o)} className="flex items-center gap-2.5 cursor-pointer flex-1 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-indigo-500/20 flex items-center justify-center shrink-0">
            <Sparkles className="w-4 h-4 text-indigo-400" />
          </div>
          <h3 className="font-sans font-semibold text-sm text-white flex items-center gap-2 truncate">
            {titleText}
            <span className="text-[9px] bg-indigo-500/25 text-indigo-200 px-1.5 py-0.5 rounded-full font-mono hidden sm:inline-block tracking-wide">
              Human-in-the-loop
            </span>
          </h3>
        </div>
        <div className="flex items-center gap-0.5">
          {loading && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); handleStop(); }}
              className="p-1.5 rounded-lg text-rose-400 hover:text-white hover:bg-rose-500/30 transition-all"
              title="Dừng tạo"
            >
              <Square className="w-4 h-4" />
            </button>
          )}
          {messages.length > 0 && !loading && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); handleNewChat(); }}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition-all"
              title="Cuộc trò chuyện mới"
            >
              <Plus className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={() => setIsOpen((o) => !o)}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white transition-all"
            aria-label={isOpen ? 'Thu gọn' : 'Mở rộng'}
          >
            {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* ── Toast ── */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            className="absolute top-14 left-1/2 -translate-x-1/2 z-20 px-4 py-2.5 bg-slate-900 text-white text-xs rounded-xl flex items-center gap-2 shadow-xl border border-slate-700"
          >
            <CheckCircle className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
            <span className="flex-1 whitespace-nowrap">{toast}</span>
            <button onClick={() => setToast(null)} className="text-slate-500 hover:text-white shrink-0"><X className="w-3 h-3" /></button>
          </motion.div>
        )}
      </AnimatePresence>

      {isOpen && (
        <>
          {/* ── Chat Body ── */}
          <div ref={scrollRef} className="flex-1 p-4 overflow-y-auto space-y-4 h-[350px] md:h-[450px]">
            {/* Empty state */}
            {messages.length === 0 && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex flex-col items-center text-center py-10"
              >
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-200 flex items-center justify-center mb-4 shadow-sm">
                  <Bot className="w-7 h-7 text-indigo-600" />
                </div>
                <p className="text-sm text-slate-800 font-semibold mb-1">{titleText}</p>
                <p className="text-xs text-slate-500 max-w-xs leading-relaxed mb-5">{descText}</p>
                <div className="flex flex-wrap justify-center gap-2">
                  {chipSuggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => { if (!loading) { handleSend(s); } }}
                      className="text-[11px] px-3 py-1.5 rounded-full border border-slate-200 text-slate-600 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-600 transition-all shadow-sm"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </motion.div>
            )}

            {/* Messages */}
            <AnimatePresence mode="popLayout">
              {/* eslint-disable-next-line react-hooks/refs -- sessionIdRef in FeedbackRow */}
              {messages.map((m, i) => (
                <motion.div
                  key={`msg-${i}`}
                  initial={{ opacity: 0, y: 12, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  transition={{ duration: 0.2, ease: 'easeOut' }}
                  className={`flex gap-2.5 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                      {m.role === 'assistant' && (
                        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-200 flex items-center justify-center shrink-0 mt-0.5">
                          <Bot className="w-3.5 h-3.5 text-indigo-600" />
                        </div>
                      )}
                      <div className="max-w-[85%] space-y-1">
                        <div className={`text-[9px] text-slate-400 flex items-center gap-1 ${m.role === 'user' ? 'justify-end' : ''}`}>
                          <Clock className="w-2.5 h-2.5" />
                          {nowTime(format)}
                        </div>
                        {(() => {
                          const citation = m.role === 'assistant' && m.content ? extractCitation(m.content) : null;
                          const displayContent = citation ? citation.body : (m.content ?? '');
                          return (
                            <>
                              <div
                                className={`p-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                                  m.role === 'user'
                                    ? 'bg-indigo-600 text-white font-medium rounded-tr-none shadow-sm'
                                    : 'bg-slate-50 text-slate-800 rounded-tl-none border border-slate-200/80 shadow-sm'
                                }`}
                              >
                                {m.role === 'assistant' ? renderMarkdown(displayContent) : (m.content ?? '')}
                              </div>
                              {citation && (
                                <div className="flex flex-wrap gap-1.5 mt-1">
                                  <span className="text-[9px] text-slate-400 flex items-center gap-0.5">
                                    📎 Nguồn:
                                  </span>
                                  {citation.sources.map((src, si) => (
                                    <span
                                      key={si}
                                      className="inline-flex items-center px-2 py-0.5 rounded-full text-[9px] font-medium bg-indigo-50 text-indigo-600 border border-indigo-200"
                                    >
                                      {src}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </>
                          );
                        })()}
                        {m.role === 'assistant' && m.content && (
                          <FeedbackRow
                            messageIndex={i}
                            sessionId={sessionIdRef.current ?? undefined}
                            sendFeedback={api.sendFeedback}
                            onToast={setToast}
                          />
                        )}
                      </div>
                  {m.role === 'user' && (
                    <div className="w-7 h-7 rounded-lg bg-indigo-600 text-white flex items-center justify-center shrink-0 shadow-sm mt-0.5">
                      <User className="w-3.5 h-3.5" />
                    </div>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>

            {/* Streaming indicator */}
            {loading && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex gap-2.5 justify-start"
              >
                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-200 flex items-center justify-center shrink-0 mt-0.5">
                  <Bot className="w-3.5 h-3.5 text-indigo-600" />
                </div>
                <div className="max-w-[85%] space-y-2">
                  {/* Tool status chip */}
                  <AnimatePresence mode="wait">
                    {toolStatus && (
                      <motion.div
                        key={toolStatus.name}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 8 }}
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium border ${
                          toolStatus.done
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : 'bg-amber-50 text-amber-700 border-amber-200'
                        }`}
                      >
                        {toolStatus.done
                          ? <Check className="w-3 h-3" />
                          : (TOOL_ICONS[toolStatus.name] ?? <RefreshCw className="w-3 h-3 animate-spin" />)
                        }
                        {toolStatus.label}
                      </motion.div>
                    )}
                  </AnimatePresence>
                  {/* Streaming text bubble */}
                  <div className="bg-slate-50 text-slate-800 border border-slate-200/80 p-3.5 rounded-2xl rounded-tl-none text-sm leading-relaxed whitespace-pre-wrap shadow-sm">
                    {streamingText ? (
                      <>
                        {renderMarkdown(streamingText)}
                        <span className="inline-block w-1.5 h-4 bg-indigo-500 ml-0.5 animate-pulse rounded-sm align-middle" />
                      </>
                    ) : (
                      <span className="text-slate-400 text-xs flex items-center gap-2">
                        <span className="flex gap-1">
                          <span className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                          <span className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                          <span className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                        </span>
                        {LOADING_STEPS[loadingStep]}
                      </span>
                    )}
                  </div>
                </div>
              </motion.div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Draft Action */}
          <AnimatePresence>
            {draftAction && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="px-4 pb-3"
              >
                <DraftActionCard
                  draft={draftAction}
                  onConfirm={handleDraftConfirm}
                  onReject={handleDraftReject}
                  onOpenRequest={
                    onOpenRequestDialog &&
                    (draftAction.action_type === 'submit_leave_request' || draftAction.action_type === 'submit_overtime_request')
                      ? () => handleDraftConfirm()
                      : undefined
                  }
                />
              </motion.div>
            )}
          </AnimatePresence>

          {/* Error bar */}
          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="mx-4 mb-2 flex items-center justify-between gap-2 border border-rose-200 bg-rose-50 px-3 py-2.5 rounded-xl text-xs text-rose-600"
              >
                <span className="flex items-center gap-1.5 min-w-0">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  <span className="break-words truncate">{error}</span>
                </span>
                <button
                  type="button"
                  onClick={() => { setError(null); handleSend(); }}
                  className="text-rose-600 underline hover:text-rose-700 shrink-0 font-medium"
                >
                  Thử lại
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {/* ── Input form ── */}
          <form
            onSubmit={(e) => { e.preventDefault(); if (!loading) handleSend(); }}
            className="p-3 bg-slate-50 border-t border-slate-200 flex gap-2 shrink-0"
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              rows={1}
              placeholder={assistantType === 'hr' ? 'Hỏi về ứng viên, onboarding, hoặc yêu cầu soạn email...  (Ctrl+J để focus)' : 'Hỏi về chấm công, phép hoặc soạn nháp yêu cầu...  (Ctrl+J để focus)'}
              className="flex-1 bg-white border border-slate-200 rounded-xl px-3.5 py-2.5 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 resize-none overflow-y-auto transition-all"
              disabled={loading || !sessionReady}
            />
            <button
              type="submit"
              aria-label={loading ? 'Dừng' : 'Gửi'}
              disabled={(!loading && (!input.trim() || !sessionReady))}
              onClick={loading ? (e) => { e.preventDefault(); handleStop(); } : undefined}
              className={`p-2.5 rounded-xl transition-all shrink-0 shadow-sm ${
                loading
                  ? 'bg-rose-500 hover:bg-rose-600 text-white'
                  : 'bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-200 text-white disabled:text-slate-400'
              }`}
            >
              {loading ? <Square className="w-4 h-4" /> : <Send className="w-4 h-4" />}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
