/**
 * Shared UI primitives — single source of truth for Vroom HR frontend.
 *
 * Merged from lib/dashboard-ui.tsx + components/operate.tsx (2025-Q2 restructure).
 * Follows AI Studio design system: slate/indigo, rounded-2xl cards, rounded-full
 * pill buttons, Be Vietnam Pro + JetBrains Mono. Vietnamese labels default.
 *
 * v3: i18n — defaults use useTranslations for locale-aware fallback text.
 */

'use client';

import React from 'react';
import { AlertTriangle, Inbox, Loader2 } from 'lucide-react';
import type { ApiError } from '@/lib/api/types';
import { getErrorMessage } from '@/lib/api/error-codes';
import { useTranslations } from 'next-intl';

// ---------------------------------------------------------------------------
// Error banner — renders BE error_code via registry when available
// ---------------------------------------------------------------------------

/**
 * Render a BE error_code with the mapped message (never self-fabricated).
 * Accepts optional `title` override (default: translated error text).
 */
export function ErrorBanner({
      error,
      title: titleProp,
    }: {
      error: unknown;
      title?: string;
    }) {
      const t = useTranslations('common');
      const title = titleProp ?? t('error');
      if (!error) return null;
      let msg = title;
      let code: string | undefined;
      let fieldErrors: Record<string, string> | undefined;
      if (typeof error === 'string') {
        msg = error;
      } else if (error instanceof Error) {
        const apiErr = error as ApiError;
        code = apiErr.errorCode;
        fieldErrors = apiErr.fieldErrors;
        if (fieldErrors && Object.keys(fieldErrors).length > 0) {
          // Validation error: use the detailed per-field message from the client
          msg = apiErr.message || title;
        } else if (code) {
          const mapped = getErrorMessage(code);
          // BUG 2: fallback to raw BE message when error_code is unmapped
          msg = mapped.startsWith('Lỗi hệ thống') && apiErr.message
            ? apiErr.message
            : mapped;
        } else {
          msg = apiErr.message || msg;
        }
      }
      const hasFields = fieldErrors && Object.keys(fieldErrors).length > 0;
      return (
        <div className="flex items-start gap-2 p-3 bg-rose-50 border border-rose-200 rounded-xl text-rose-700">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="min-w-0 text-sm">
            <p className="break-words font-semibold">{msg}</p>
            {hasFields && (
              <ul className="mt-1.5 space-y-0.5 list-disc list-inside">
                {Object.values(fieldErrors!).map((fieldMsg, i) => (
                  <li key={i} className="text-xs text-rose-600">{fieldMsg}</li>
                ))}
              </ul>
            )}
            {code && code !== 'UNKNOWN_ERROR' && code !== 'VALIDATION_ERROR' && (
              <code className="text-[11px] font-mono text-rose-400 block mt-0.5">{code}</code>
            )}
          </div>
        </div>
      );
    }

/** @deprecated Alias for ErrorBanner — kept for backward compat. Use ErrorBanner directly. */
export const ErrorAlert = ErrorBanner;

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

/** Centered loading spinner with optional label. */
export function Loading({ label: labelProp }: { label?: string }) {
  const t = useTranslations('common');
  const label = labelProp ?? t('loading');
  return (
    <div className="flex items-center justify-center py-12">
      <div className="animate-spin w-6 h-6 border-2 border-indigo-600 border-t-transparent rounded-full" />
      <span className="ml-3 text-sm text-slate-500">{label}</span>
    </div>
  );
}

/** Skeleton rows for table/list loading placeholders. */
export function LoadingRows({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="animate-pulse h-12 bg-slate-100 rounded-lg" />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state — distinguishes "filter matched nothing" vs "no data yet"
// ---------------------------------------------------------------------------

export function EmptyState({
  filtered,
  onReset,
  emptyFiltered: ef,
  emptyData: ed,
  hintFiltered: hf,
  hintData: hd,
}: {
  filtered: boolean;
  onReset?: () => void;
  emptyFiltered?: string;
  emptyData?: string;
  hintFiltered?: string;
  hintData?: string;
}) {
  const t = useTranslations('common');
  const emptyFiltered = ef ?? t('noResults');
  const emptyData = ed ?? t('noData');
  const hintFiltered = hf ?? t('clear');
  const hintData = hd ?? t('noData');
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <Inbox className="w-10 h-10 text-slate-300 mb-3" />
      <p className="text-sm font-medium text-slate-600">
        {filtered ? emptyFiltered : emptyData}
      </p>
      <p className="text-xs text-slate-400 mt-1">
        {filtered ? hintFiltered : hintData}
      </p>
      {filtered && onReset && (
        <button
          onClick={onReset}
          className="mt-4 px-4 py-2 text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-600 rounded-full transition-all"
        >
          {t('clear')}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status pill + Badge (two distinct primitives)
// ---------------------------------------------------------------------------

/** Status pill — color by group; label Vietnamese. Used in pipeline dashboards. */
export function StatusPill({
  status,
  label,
  tone = 'slate',
}: {
  status: string;
  label: string;
  tone?: 'slate' | 'indigo' | 'amber' | 'emerald' | 'rose' | 'violet';
}) {
  const tones: Record<string, string> = {
    slate: 'bg-slate-100 text-slate-700 border-slate-200',
    indigo: 'bg-indigo-50 text-indigo-700 border-indigo-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    rose: 'bg-rose-50 text-rose-700 border-rose-200',
    violet: 'bg-violet-50 text-violet-700 border-violet-200',
  };
  return (
    <span className={`inline-flex items-center text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold border ${tones[tone]}`}>
      {label}
    </span>
  );
}

type BadgeTone = 'slate' | 'indigo' | 'emerald' | 'amber' | 'rose' | 'sky';

const BADGE_TONES: Record<BadgeTone, string> = {
  slate: 'bg-slate-100 text-slate-600 border-slate-200',
  indigo: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  amber: 'bg-amber-50 text-amber-700 border-amber-200',
  rose: 'bg-rose-50 text-rose-700 border-rose-200',
  sky: 'bg-sky-50 text-sky-700 border-sky-200',
};

/** Simple badge — children-driven. Use StatusPill for pipeline status with predefined labels. */
export function Badge({
  tone = 'slate',
  children,
}: {
  tone?: BadgeTone;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-mono font-bold border ${BADGE_TONES[tone]}`}
    >
      {children}
    </span>
  );
}

/** Map a status string to a tone. */
export function statusTone(status: string): BadgeTone {
  switch (status) {
    case 'active':
    case 'approved':
    case 'published':
    case 'completed':
    case 'healthy':
      return 'emerald';
    case 'submitted':
    case 'draft':
    case 'checked_in':
      return 'amber';
    case 'inactive':
    case 'rejected':
    case 'cancelled':
    case 'unhealthy':
      return 'rose';
    default:
      return 'slate';
  }
}

// ---------------------------------------------------------------------------
// Status metadata maps (Vietnamese, per CONTEXT.md)
// ---------------------------------------------------------------------------

export const CANDIDATE_STATUS_META: Record<
  string,
  { label: string; tone: 'slate' | 'indigo' | 'amber' | 'emerald' | 'rose' | 'violet'; labelKey: string }
> = {
  new: { label: 'Mới', tone: 'slate', labelKey: 'candidateNew' },
  reviewing: { label: 'Đang review', tone: 'indigo', labelKey: 'candidateReviewing' },
  interview_scheduled: { label: 'Đã lên lịch PV', tone: 'violet', labelKey: 'candidateInterviewScheduled' },
  accepted: { label: 'Đã nhận', tone: 'emerald', labelKey: 'candidateAccepted' },
  rejected: { label: 'Từ chối', tone: 'rose', labelKey: 'candidateRejected' },
  archived: { label: 'Lưu trữ', tone: 'slate', labelKey: 'candidateArchived' },
};

export const INBOX_STATUS_META: Record<
  string,
  { label: string; tone: 'amber' | 'rose' | 'indigo' | 'slate'; labelKey: string }
> = {
  needs_classification: { label: 'Cần xác nhận phân loại', tone: 'amber', labelKey: 'inboxNeedsClassification' },
  needs_information: { label: 'Cần bổ sung thông tin', tone: 'rose', labelKey: 'inboxNeedsInfo' },
  ready_for_review: { label: 'Sẵn sàng review', tone: 'indigo', labelKey: 'inboxReadyForReview' },
  resolved: { label: 'Đã xử lý', tone: 'slate', labelKey: 'inboxResolved' },
};

export const JOB_STATUS_META: Record<
  string,
  { label: string; tone: 'slate' | 'emerald' | 'indigo' | 'rose'; labelKey: string }
> = {
  draft: { label: 'Bản nháp', tone: 'slate', labelKey: 'jobDraft' },
  open: { label: 'Đang tuyển', tone: 'emerald', labelKey: 'jobOpen' },
  closed: { label: 'Đã đóng', tone: 'indigo', labelKey: 'jobClosed' },
  cancelled: { label: 'Đã hủy', tone: 'rose', labelKey: 'jobCancelled' },
};

export const CONFLICT_STATUS_META: Record<
  string,
  { label: string; tone: 'amber' | 'emerald' | 'rose' | 'slate'; labelKey: string }
> = {
  pending: { label: 'Chờ xử lý', tone: 'amber', labelKey: 'conflictPending' },
  resolved: { label: 'Đã giải quyết', tone: 'emerald', labelKey: 'conflictResolved' },
};

// Audit action labels — Vietnamese values kept for backward compat;
// new code should use t('audit.{action}') from the 'audit' namespace.
export const AUDIT_ACTION_LABELS: Record<string, string> = {
  role_change: 'Thay đổi quyền',
  user_create: 'Tạo tài khoản',
  user_update: 'Cập nhật tài khoản',
  user_delete: 'Xóa tài khoản',
  permission_grant: 'Cấp quyền',
  permission_revoke: 'Thu hồi quyền',
  login: 'Đăng nhập',
  settings_update: 'Cập nhật cài đặt',
  employee_create: 'Tạo nhân viên',
  employee_update: 'Cập nhật nhân viên',
  whitelist_add: 'Thêm whitelist',
  whitelist_remove: 'Xóa whitelist',
  oauth_update: 'Cập nhật OAuth',
  org_domain_update: 'Cập nhật domain',
  assistant_tool_config: 'Cấu hình tool assistant',
  assistant_chat: 'Chat với trợ lý AI',
  org_google_connect: 'Kết nối Google',
  org_google_reconnect: 'Kết nối lại Google',
  org_google_switch_account: 'Chuyển tài khoản Google',
  org_google_disconnect: 'Ngắt kết nối Google',
  org_google_calendar_select: 'Chọn lịch Google',
  org_ai_config_update: 'Cập nhật cấu hình AI',
  org_ai_config_rotate: 'Xoay API key AI',
  org_ai_config_revoke: 'Thu hồi cấu hình AI',
  org_ai_config_source: 'Thay đổi nguồn credential AI',
  org_ai_classification_rollout: 'Triển khai phân loại AI',
  org_ai_toggle_assistant: 'Bật/tắt trợ lý AI',
  org_ai_consent: 'Đồng ý chính sách AI',
  org_ai_toggle_automation: 'Bật/tắt AI tự động',
  payslip_create: 'Tạo phiếu lương',
  payslip_update: 'Cập nhật phiếu lương',
  payslip_publish: 'Xuất bản phiếu lương',
  payslip_unpublish: 'Ẩn phiếu lương',
  payslip_delete: 'Xóa phiếu lương',
  request_approve: 'Phê duyệt yêu cầu',
  request_reject: 'Từ chối yêu cầu',
  attendance_network_update: 'Cập nhật IP chấm công',
  attendance_network_add: 'Thêm IP chấm công',
      ai_policy_preset_update: 'Cập nhật chính sách AI',
  attendance_network_remove: 'Xóa IP chấm công',
  attendance_correction: 'Sửa chấm công',
  outbound_email_created: 'Tạo email gửi đi',
  outbound_email_sent: 'Đã gửi email',
  outbound_email_failed: 'Gửi email thất bại',
  outbound_email_retry: 'Thử lại gửi email',
};

export const AUDIT_ACTION_GROUPS: { label: string; items: { value: string; label: string }[] }[] = [
  {
    label: '👤 Quyền & Tài khoản',
    items: [
      { value: 'login', label: 'Đăng nhập' },
      { value: 'role_change', label: 'Thay đổi quyền' },
      { value: 'user_create', label: 'Tạo tài khoản' },
      { value: 'user_update', label: 'Cập nhật tài khoản' },
      { value: 'user_delete', label: 'Xóa tài khoản' },
      { value: 'permission_grant', label: 'Cấp quyền' },
      { value: 'permission_revoke', label: 'Thu hồi quyền' },
      { value: 'settings_update', label: 'Cập nhật cài đặt' },
    ],
  },
  {
    label: '👥 Nhân sự',
    items: [
      { value: 'employee_create', label: 'Tạo nhân viên' },
      { value: 'employee_update', label: 'Cập nhật nhân viên' },
    ],
  },
  {
    label: '🤖 AI & Hệ thống',
    items: [
      { value: 'org_ai_config_update', label: 'Cập nhật cấu hình AI' },
      { value: 'org_ai_config_rotate', label: 'Xoay API key AI' },
      { value: 'org_ai_config_revoke', label: 'Thu hồi cấu hình AI' },
      { value: 'org_ai_config_source', label: 'Thay đổi nguồn credential AI' },
      { value: 'org_ai_consent', label: 'Đồng ý chính sách AI' },
      { value: 'org_ai_toggle_automation', label: 'Bật/tắt AI tự động' },
      { value: 'org_ai_toggle_assistant', label: 'Bật/tắt trợ lý AI' },
      { value: 'org_ai_classification_rollout', label: 'Triển khai phân loại AI' },
      { value: 'assistant_tool_config', label: 'Cấu hình tool assistant' },
      { value: 'assistant_chat', label: 'Chat với trợ lý AI' },
    ],
  },
  {
    label: '📧 Email',
    items: [
      { value: 'outbound_email_created', label: 'Tạo email gửi đi' },
      { value: 'outbound_email_sent', label: 'Đã gửi email' },
      { value: 'outbound_email_failed', label: 'Gửi email thất bại' },
      { value: 'outbound_email_retry', label: 'Thử lại gửi email' },
    ],
  },
  {
    label: '🔗 Google',
    items: [
      { value: 'org_google_connect', label: 'Kết nối Google' },
      { value: 'org_google_reconnect', label: 'Kết nối lại Google' },
      { value: 'org_google_switch_account', label: 'Chuyển tài khoản Google' },
      { value: 'org_google_disconnect', label: 'Ngắt kết nối Google' },
      { value: 'org_google_calendar_select', label: 'Chọn lịch Google' },
    ],
  },
  {
    label: '💰 Phiếu lương',
    items: [
      { value: 'payslip_create', label: 'Tạo phiếu lương' },
      { value: 'payslip_update', label: 'Cập nhật phiếu lương' },
      { value: 'payslip_publish', label: 'Xuất bản phiếu lương' },
      { value: 'payslip_unpublish', label: 'Ẩn phiếu lương' },
      { value: 'payslip_delete', label: 'Xóa phiếu lương' },
    ],
  },
  {
    label: '⏱️ Chấm công',
    items: [
      { value: 'attendance_network_update', label: 'Cập nhật IP chấm công' },
      { value: 'attendance_network_add', label: 'Thêm IP chấm công' },
      { value: 'attendance_network_remove', label: 'Xóa IP chấm công' },
      { value: 'attendance_correction', label: 'Sửa chấm công' },
    ],
  },
  {
    label: '📋 Yêu cầu & Phê duyệt',
    items: [
      { value: 'request_approve', label: 'Phê duyệt yêu cầu' },
      { value: 'request_reject', label: 'Từ chối yêu cầu' },
    ],
  },
  {
    label: '🛡️ Bảo mật & Domain',
    items: [
      { value: 'whitelist_add', label: 'Thêm whitelist' },
      { value: 'whitelist_remove', label: 'Xóa whitelist' },
      { value: 'oauth_update', label: 'Cập nhật OAuth' },
      { value: 'org_domain_update', label: 'Cập nhật domain' },
    ],
  },
];

export const MIME_TYPE_LABELS: Record<string, string> = {
  'application/pdf': 'PDF',
  'application/msword': 'Word',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word (DOCX)',
  'text/plain': 'Văn bản',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
};

// Service labels — Vietnamese values kept for backward compat;
// new code should use t('system.{name}') from the 'system' namespace.
export const SERVICE_LABELS: Record<string, string> = {
  redis: 'Bộ nhớ đệm',
  postgresql: 'Cơ sở dữ liệu',
  minio: 'Lưu trữ tài liệu',
  'gmail-worker': 'Đồng bộ email',
  'onboarding-worker': 'Xử lý onboarding',
};

// ---------------------------------------------------------------------------
// Page header
// ---------------------------------------------------------------------------

export function PageHeader({
  icon: Icon,
  title,
  subtitle,
  actions,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2 text-indigo-600 mb-1">
          <Icon className="w-5 h-5" />
          <h1 className="text-xl font-bold text-slate-900">{title}</h1>
        </div>
        {subtitle && <p className="text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="shrink-0">{actions}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card / section / field primitives
// ---------------------------------------------------------------------------

export function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100 ${className}`}>
      {children}
    </div>
  );
}

export function SectionTitle({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <Icon className="w-5 h-5 text-indigo-600" />
      <h2 className="font-bold text-slate-900">{children}</h2>
    </div>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-600 mb-1 block">{label}</span>
      {children}
      {hint && <span className="text-[10px] text-slate-400 mt-1 block">{hint}</span>}
    </label>
  );
}

const inputCls =
  'w-full px-3 py-2 rounded-lg border border-slate-200 bg-white text-sm text-slate-900 placeholder-slate-300 focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 transition-all';

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputCls} ${props.className ?? ''}`} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputCls} ${props.className ?? ''}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputCls} ${props.className ?? ''}`} />;
}

// ---------------------------------------------------------------------------
// Buttons — rounded-full per AI Studio DESIGN.md (pill: 9999px)
// ---------------------------------------------------------------------------

const BTN =
  'inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-full text-xs font-semibold transition-all disabled:opacity-50 disabled:cursor-not-allowed';

export function ButtonPrimary(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`${BTN} bg-indigo-600 text-white hover:bg-indigo-500 shadow-sm shadow-indigo-100 ${props.className ?? ''}`}
    />
  );
}

export function ButtonGhost(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`${BTN} bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 ${props.className ?? ''}`}
    />
  );
}

export function ButtonDanger(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`${BTN} bg-rose-50 text-rose-600 border border-rose-200 hover:bg-rose-100 ${props.className ?? ''}`}
    />
  );
}

// ---------------------------------------------------------------------------
// Modal (lightweight)
// ---------------------------------------------------------------------------

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  const t = useTranslations('common');
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-lg bg-white rounded-2xl border border-slate-200 shadow-xl p-5 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-slate-900 text-sm">{title}</h3>
          <button
            onClick={onClose}
            aria-label={t('close')}
            className="text-slate-400 hover:text-slate-600 text-lg leading-none"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/** Format a string Decimal/number as VND currency. */
export function formatVND(value: string | number | null | undefined, locale = 'vi-VN'): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(n)) return String(value);
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'VND',
    maximumFractionDigits: 0,
  }).format(n);
}

/** Format an ISO date/time into a Vietnamese localized string. */
export function formatDateTime(iso: string | null | undefined, locale = 'vi-VN'): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(locale);
}

export function formatDate(iso: string | null | undefined, locale = 'vi-VN'): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(locale);
}

/**
 * Format runtime detail string for HR users.
 * - null/undefined → empty string
 * - "last beat:" prefix → relative time in Vietnamese
 * - heartbeat-related → "Không hoạt động"
 * - fallback: return detail as-is
 */
export function formatRuntimeDetail(detail: string | null, locale = 'vi-VN'): string {
      if (detail == null) return '';
      if (detail.startsWith('last beat:')) {
        const raw = detail.slice('last beat:'.length).trim();
        const ts = parseFloat(raw);
        if (Number.isNaN(ts)) return detail;
        const now = Date.now() / 1000;
        const diff = Math.max(0, now - ts);
        if (diff < 60) return locale === 'vi-VN' ? 'Vừa xong' : 'Just now';
        if (diff < 3600) {
          const m = Math.floor(diff / 60);
          return locale === 'vi-VN' ? `${m} phút trước` : `${m} min ago`;
        }
        if (diff < 86400) {
          const h = Math.floor(diff / 3600);
          return locale === 'vi-VN' ? `${h} giờ trước` : `${h} hr ago`;
        }
        const d = Math.floor(diff / 86400);
        return locale === 'vi-VN' ? `${d} ngày trước` : `${d} day ago`;
      }
      if (detail === 'no heartbeat' || detail.includes('heartbeat'))
        return locale === 'vi-VN' ? 'Không hoạt động' : 'Inactive';
      return detail;
    }

/** Format latency in ms → Vietnamese qualitative label. */
export function formatLatency(latencyMs: number | null, locale = 'vi-VN'): string {
      if (latencyMs == null) return '';
      if (latencyMs < 100) return locale === 'vi-VN' ? 'Nhanh' : 'Fast';
      if (latencyMs < 500) return locale === 'vi-VN' ? 'Bình thường' : 'Normal';
      return locale === 'vi-VN' ? 'Chậm' : 'Slow';
    }

    /** Format audit details as a Vietnamese-readable string (never raw JSON). */
    export function formatAuditDetails(details: unknown, locale = 'vi-VN'): string {
      if (!details) return '—';
      if (typeof details !== 'object' || details === null) return String(details);

      // Field labels — locale-aware
      const fieldMap: Record<string, string> = locale === 'vi-VN'
        ? {
          role: 'Quyền', email: 'Email', name: 'Tên',
          employee_id: 'Mã NV', permissions: 'Quyền hạn',
          result: 'Kết quả', kết_quả: 'Kết quả',
          calendar_id: 'Lịch', lịch: 'Lịch',
          // Domain / whitelist
          value: 'Giá trị', entry_type: 'Loại', domain: 'Tên miền',
          domains: 'Tên miền', action: 'Hành động',
          // AI config
          model: 'Model', provider: 'Nhà cung cấp', base_url: 'API URL',
          capability: 'Tính năng', credential_source: 'Nguồn xác thực',
          // Payslip
          payslip_id: 'Mã phiếu lương', period_month: 'Kỳ lương',
          gross_salary: 'Lương gross', net_salary: 'Lương net',
          published_at: 'Ngày xuất bản',
          // Request
          request_id: 'Mã yêu cầu', request_type: 'Loại yêu cầu',
          // Feedback / chat
          event: 'Sự kiện', feedback_type: 'Đánh giá',
          message_index: 'Tin nhắn #', optional_text: 'Ghi chú',
          // OAuth / Google
          client_id: 'Client ID', redirect_uri: 'Redirect URI',
          // Attendance
          network: 'IP/CIDR', cidr: 'IP/CIDR', correction_date: 'Ngày sửa',
          // General
          old_role: 'Quyền cũ', new_role: 'Quyền mới',
          target_user_email: 'Người dùng', target_user_id: 'Người dùng',
          policy_version: 'Phiên bản chính sách',
        }
        : {
          role: 'Role', email: 'Email', name: 'Name',
          employee_id: 'Employee ID', permissions: 'Permissions',
          result: 'Result', kết_quả: 'Result',
          calendar_id: 'Calendar', lịch: 'Calendar',
          // Domain / whitelist
          value: 'Value', entry_type: 'Type', domain: 'Domain',
          domains: 'Domains', action: 'Action',
          // AI config
          model: 'Model', provider: 'Provider', base_url: 'API URL',
          capability: 'Capability', credential_source: 'Credential Source',
          // Payslip
          payslip_id: 'Payslip ID', period_month: 'Period',
          gross_salary: 'Gross Salary', net_salary: 'Net Salary',
          published_at: 'Published At',
          // Request
          request_id: 'Request ID', request_type: 'Request Type',
          // Feedback / chat
          event: 'Event', feedback_type: 'Feedback',
          message_index: 'Message #', optional_text: 'Note',
          // OAuth / Google
          client_id: 'Client ID', redirect_uri: 'Redirect URI',
          // Attendance
          network: 'IP/CIDR', cidr: 'IP/CIDR', correction_date: 'Correction Date',
          // General
          old_role: 'Old Role', new_role: 'New Role',
          target_user_email: 'User', target_user_id: 'User',
          policy_version: 'Policy Version',
        };

      // Value translations — locale-aware
      const valueMap: Record<string, string> = locale === 'vi-VN'
        ? {
          calendar_selected: 'đã chọn lịch',
          connected: 'đã kết nối', disconnected: 'đã ngắt kết nối',
          enable: 'Bật', disable: 'Tắt',
          update: 'Cập nhật', add: 'Thêm', remove: 'Xóa',
          org_api_key: 'API Key tổ chức', deployment_key: 'Khóa triển khai',
          automation: 'Tự động hóa', assistant: 'Trợ lý AI',
          capability_consent_accept: 'Đồng ý',
          data_policy_accept: 'Đồng ý chính sách dữ liệu',
          exact_email: 'Email cụ thể', domain_pattern: 'Tên miền',
          message_feedback: 'Phản hồi tin nhắn',
          up: '👍 Hữu ích', down: '👎 Không hữu ích',
          admin: 'Quản trị (HR)', user: 'Nhân viên',
          database: 'Thêm thủ công', file: 'File cấu hình',
          published: 'Đã xuất bản', draft: 'Bản nháp',
        }
        : {
          calendar_selected: 'calendar selected',
          connected: 'connected', disconnected: 'disconnected',
          enable: 'Enable', disable: 'Disable',
          update: 'Update', add: 'Add', remove: 'Remove',
          org_api_key: 'Org API Key', deployment_key: 'Deployment Key',
          automation: 'Automation', assistant: 'AI Assistant',
          capability_consent_accept: 'Accepted',
          data_policy_accept: 'Data Policy Accepted',
          exact_email: 'Exact Email', domain_pattern: 'Domain Pattern',
          message_feedback: 'Message Feedback',
          system_admin: 'System Admin', hr: 'HR Manager', user: 'Employee',
          admin: 'System Admin', user_legacy: 'Employee',
        };

  // Keys that are internal UUIDs or IDs — skip them for readability
  const skipKeys = new Set([
    'entry_id', 'session_id',
  ]);

  try {
    const entries = Object.entries(details as Record<string, unknown>);
    if (entries.length === 0) return '—';
    const parts: string[] = [];
    for (const [key, value] of entries) {
      if (skipKeys.has(key)) continue;
      const label = fieldMap[key] ?? key;
      if (value === null || value === undefined || value === '') continue;
      let val: string;
      if (Array.isArray(value)) {
        if (value.length === 0) continue;
        val = value.map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(', ');
      } else if (typeof value === 'object') {
        val = JSON.stringify(value);
      } else {
        val = String(value);
      }
      if (!val || val === 'null' || val === 'undefined') continue;
      val = valueMap[val] ?? val;
      parts.push(`${label}: ${val}`);
    }
    return parts.join(' · ');
  } catch {
    return JSON.stringify(details);
  }
}

/** Confidence as percent. */
export function confidencePct(c: number | null | undefined): string {
  if (c == null) return '—';
  return `${Math.round(c * 100)}%`;
}
