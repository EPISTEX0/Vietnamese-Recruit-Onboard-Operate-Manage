'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import {
  Mail, RefreshCw, Calendar as CalendarIcon, Sparkles, Loader2, Check, PenSquare,
} from 'lucide-react';
import * as gmailApi from '@/lib/api/gmail';
import type { OrganizationGoogleConnectionResponse } from '@/lib/api/types';
import { ApiError } from '@/lib/api/types';
import { useAuthGuard } from '@/lib/auth/session';


import { ToastProvider, useToast } from './toast';
import { apiErrorText } from './helpers';
import ConnectionPanel from './connection-panel';
import HistoricalImportPanel from './historical-import';
import MessageList from './message-list';
import MessageDetail from './message-detail';
import OutboundSection from './outbound-section';
import ComposeDialog from './compose-dialog';

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function GmailPage() {
  return (
    <ToastProvider>
      <GmailPageInner />
    </ToastProvider>
  );
}

function GmailPageInner() {
  useAuthGuard({ requireAuth: true, requireAdmin: true });
  const t = useTranslations('gmail');
  const { push } = useToast();
  const qc = useQueryClient();

  // --- connection status ---
  const conn = useQuery<OrganizationGoogleConnectionResponse, Error>({
    queryKey: ['gmail-connection'],
    queryFn: gmailApi.getConnectionStatus,
    staleTime: 30_000,
  });

  const isConnected = conn.data?.status === 'connected';
  const needsReauth = conn.data?.status === 'reauthorization_required';


  // --- calendars ---
  const calendars = useQuery({
    queryKey: ['gmail-calendars', conn.data?.status],
    queryFn: gmailApi.getCalendars,
    enabled: isConnected,
    staleTime: 60_000,
  });

  const selectCalMut = useMutation({
    mutationFn: (id: string) => gmailApi.selectCalendar(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gmail-calendars'] }); push({ kind: 'success', text: t('calendarSelected') }); },
    onError: (e) => push({ kind: 'error', text: t('calendarSelectError', { error: apiErrorText(e) }) }),
  });

  // --- messages ---
  const PAGE_SIZE = 20;
  const [category, setCategory] = useState<string>('');
  const [page, setPage] = useState(1);

  // Reset page when category changes
  useEffect(() => { setPage(1); }, [category]);

  const messages = useQuery({
    queryKey: ['gmail-messages', category, page],
    queryFn: () => gmailApi.listMessages(category || undefined, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    enabled: isConnected,
    staleTime: 30_000,
  });

  const totalPages = messages.data ? Math.ceil(messages.data.total / PAGE_SIZE) : 0;
  const hasMore = page < totalPages;

  const categories = useMemo(() => {
    const set = new Set<string>();
    messages.data?.messages?.forEach((m) => { if (m.category) set.add(m.category); });
    return Array.from(set).sort();
  }, [messages.data]);

  // --- selected message detail + attachments ---
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(
    () => messages.data?.messages?.find((m) => m.id === selectedId) ?? null,
    [messages.data, selectedId],
  );
  const body = useQuery({
    queryKey: ['gmail-body', selectedId],
    queryFn: () => gmailApi.getMessageBody(selectedId!),
    enabled: !!selectedId,
    staleTime: 60_000,
  });
  const attachments = useMutation({
    mutationFn: (id: string) => gmailApi.getAttachments(id),
    onError: (e) => push({ kind: 'error', text: t('attachFetchError', { error: apiErrorText(e) }) }),
  });
  const processAttachmentsMut = useMutation({
    mutationFn: (id: string) => gmailApi.processAttachments(id),
    onSuccess: (r) => { push({ kind: 'success', text: r.message || t('cvProcessed', { count: r.processed_count }) }); qc.invalidateQueries({ queryKey: ['gmail-messages'] }); },
    onError: (e) => push({ kind: 'error', text: t('cvProcessError', { error: apiErrorText(e) }) }),
  });

  // --- connection actions ---
  const connectMut = useMutation({
    mutationFn: () => (needsReauth ? gmailApi.reconnectConnection() : gmailApi.getAuthorizeUrl()),
    onSuccess: (res) => { if (res.redirect_url) window.location.href = res.redirect_url; else { qc.invalidateQueries({ queryKey: ['gmail-connection'] }); } },
    onError: (e) => push({ kind: 'error', text: t('connectFail', { error: apiErrorText(e) }) }),
  });
  const disconnectMut = useMutation({
    mutationFn: () => gmailApi.disconnectConnection(),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gmail-connection'] }); push({ kind: 'info', text: t('disconnectSuccess') }); },
    onError: (e) => push({ kind: 'error', text: t('disconnectFail', { error: apiErrorText(e) }) }),
  });

  // --- sync ---
  const syncMut = useMutation({
    mutationFn: () => gmailApi.syncEmails(),
    onSuccess: (r) => { push({ kind: 'success', text: t('syncResult', { count: r.synced_count }) }); qc.invalidateQueries({ queryKey: ['gmail-messages'] }); },
    onError: (e) => {
      if (e instanceof ApiError && e.errorCode === 'GMAIL_NOT_CONNECTED') {
        qc.invalidateQueries({ queryKey: ['gmail-connection'] });
      }
      push({ kind: 'error', text: apiErrorText(e) });
    },
  });

  // --- classify ---
  const [classifying, setClassifying] = useState(false);
  const [classifyProgress, setClassifyProgress] = useState<string | null>(null);
  const handleClassify = useCallback(async () => {
    setClassifying(true);
    setClassifyProgress(t('classifying'));
    let total = 0, remaining = 1, totalToClassify: number | null = null, cv = 0;
    try {
      while (remaining > 0) {
        const r = await gmailApi.classifyBatch(5);
        if (totalToClassify === null) totalToClassify = r.remaining + r.classified_count;
        total += r.classified_count;
        cv += r.cv_processed_count ?? 0;
        remaining = r.remaining;
        setClassifyProgress(t('classifyProgress', { current: total, total: totalToClassify }));
        if (r.classified_count === 0) break;
      }
      setClassifyProgress(null);
      const extra = cv > 0 ? t('classifyResultCv', { count: cv }) : '';
      push({ kind: 'success', text: t('classifyResult', { count: total, extra }) });
      qc.invalidateQueries({ queryKey: ['gmail-messages'] });
    } catch (e) {
      setClassifyProgress(null);
      push({ kind: 'error', text: t('classifyFail', { error: apiErrorText(e) }) });
    } finally {
      setClassifying(false);
    }
  }, [push, qc, t]);

  // --- compose / outbound ---
  const [composeOpen, setComposeOpen] = useState(false);
  const [replyTo, setReplyTo] = useState<import('@/lib/api/types').EmailMessage | null>(null);

  const outbound = useQuery({
    queryKey: ['gmail-outbound'],
    queryFn: gmailApi.listOutboundEmails,
    enabled: isConnected,
    staleTime: 15_000,
  });
  const createOutboundMut = useMutation({
    mutationFn: (data: { to: string[]; cc?: string[]; subject: string; body_text?: string; body_html?: string; reply_to_message_id?: string }) =>
      gmailApi.createOutboundEmail(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gmail-outbound'] }); push({ kind: 'success', text: t('draftCreated') }); setComposeOpen(false); },
    onError: (e) => push({ kind: 'error', text: t('draftFail', { error: apiErrorText(e) }) }),
  });
  const sendOutboundMut = useMutation({
    mutationFn: (id: string) => gmailApi.sendOutboundEmail(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gmail-outbound'] }); push({ kind: 'success', text: t('emailSent') }); },
    onError: (e) => { qc.invalidateQueries({ queryKey: ['gmail-outbound'] }); push({ kind: 'error', text: t('sendFail', { error: apiErrorText(e) }) }); },
  });
  const deleteOutboundMut = useMutation({
    mutationFn: (id: string) => gmailApi.deleteOutboundEmail(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['gmail-outbound'] }),
    onError: (e) => push({ kind: 'error', text: t('deleteDraftFail', { error: apiErrorText(e) }) }),
  });

  // --- GMAIL_NOT_CONNECTED banner ---
  const connError = conn.error instanceof ApiError ? conn.error : null;
  const showNotConnectedBanner = !isConnected || connError?.errorCode === 'GMAIL_NOT_CONNECTED';

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-indigo-600">
          <Mail className="w-5 h-5" />
          <h1 className="text-xl font-bold text-slate-900">{t('title')}</h1>
        </div>
        {isConnected && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleClassify}
              disabled={classifying}
              className="inline-flex items-center gap-1.5 text-xs font-medium bg-white border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 disabled:opacity-50"
            >
              <Sparkles className="w-3.5 h-3.5" /> {classifying ? t('classifying') : t('classifyBtn')}
            </button>
            <button
              onClick={() => syncMut.mutate()}
              disabled={syncMut.isPending}
              className="inline-flex items-center gap-1.5 text-xs font-medium bg-indigo-600 text-white rounded-lg px-3 py-1.5 hover:bg-indigo-700 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncMut.isPending ? 'animate-spin' : ''}`} /> {t('syncBtn')}
            </button>
            <button
              onClick={() => { setReplyTo(null); setComposeOpen(true); }}
              className="inline-flex items-center gap-1.5 text-xs font-medium bg-white border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50"
            >
              <PenSquare className="w-3.5 h-3.5" /> {t('composeBtn')}
            </button>
          </div>
        )}
      </div>

      {/* Connection panel */}
      <ConnectionPanel
        status={conn.data?.status ?? null}
        email={conn.data?.email ?? null}
        loading={conn.isLoading}
        error={connError ? apiErrorText(conn.error) : conn.error ? t('connectError', { error: apiErrorText(conn.error) }) : null}
        notConnectedCode={connError?.errorCode === 'GMAIL_NOT_CONNECTED' ? 'GMAIL_NOT_CONNECTED' : null}
        onConnect={() => connectMut.mutate()}
        onDisconnect={() => disconnectMut.mutate()}
        connectLoading={connectMut.isPending}
        disconnectLoading={disconnectMut.isPending}
      />

      {isConnected && (
        <>
          {/* Calendars + selected calendar */}
          <div className="p-4 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
            <div className="flex items-center gap-2 mb-3">
              <CalendarIcon className="w-4 h-4 text-indigo-600" />
              <h2 className="font-bold text-sm text-slate-900">{t('calendarTitle')}</h2>
              <span className="ml-auto text-[10px] text-slate-400 font-mono">{t('calendarHint')}</span>
            </div>
            {calendars.isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
            ) : calendars.error ? (
              <p className="text-xs text-rose-500">{t('calendarLoadError', { error: apiErrorText(calendars.error) })}</p>
            ) : (
              <div className="space-y-1.5">
                {calendars.data?.calendars?.length ? (
                  calendars.data.calendars.map((c) => {
                    const calSelected = calendars.data.selected_calendar_id === c.id;
                    return (
                      <label key={c.id} className="flex items-center gap-2 p-2 rounded-lg border border-slate-100 hover:bg-slate-50 cursor-pointer">
                        <input
                          type="radio"
                          name="calendar"
                          checked={calSelected}
                          disabled={selectCalMut.isPending}
                          onChange={() => selectCalMut.mutate(c.id)}
                          className="accent-indigo-600"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium text-slate-800 truncate">{c.summary}{c.primary && <span className="text-[10px] text-slate-400"> · primary</span>}</div>
                          <div className="text-[10px] text-slate-400 font-mono truncate">{c.id}</div>
                        </div>
                        {calSelected && <Check className="w-3.5 h-3.5 text-emerald-500" />}
                      </label>
                    );
                  })
                ) : (
                  <p className="text-xs text-slate-400">{t('noCalendar')}</p>
                )}
              </div>
            )}
          </div>

          {/* Historical import */}
          <HistoricalImportPanel />

          {/* Classification progress overlay */}
          {classifying && classifyProgress && (
            <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-50/80">
              <div className="flex flex-col items-center gap-3 rounded-xl border border-slate-200 bg-white px-8 py-6 shadow-md">
                <Sparkles className="w-6 h-6 text-indigo-600 animate-pulse" />
                <p className="text-sm font-medium text-slate-800">{t('classifyOverlay')}</p>
                <p className="text-xs text-slate-500">{classifyProgress}</p>
              </div>
            </div>
          )}

          {/* Messages + detail */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <MessageList
              category={category}
              onCategoryChange={setCategory}
              categories={categories}
              messages={messages.data?.messages ?? []}
              total={messages.data?.total ?? 0}
              isLoading={messages.isLoading}
              isFetching={messages.isFetching}
              error={messages.error}
              selectedId={selectedId}
              onSelect={setSelectedId}
              page={page}
              pageSize={PAGE_SIZE}
              hasMore={hasMore}
              onNextPage={() => setPage((p) => p + 1)}
              onRefetch={() => messages.refetch()}
              onAttachmentsReset={() => attachments.reset()}
            />
            <MessageDetail
              selected={selected}
              onDeselect={() => setSelectedId(null)}
              bodyQuery={{
                isLoading: body.isLoading,
                error: body.error,
                data: body.data,
                refetch: body.refetch,
              }}
              attachmentsMut={{
                isPending: attachments.isPending,
                mutate: (id) => attachments.mutate(id),
                data: attachments.data,
                reset: attachments.reset,
              }}
              processAttachmentsMut={{
                isPending: processAttachmentsMut.isPending,
                mutate: (id) => processAttachmentsMut.mutate(id),
                data: processAttachmentsMut.data,
              }}
              onReply={() => { setReplyTo(selected); setComposeOpen(true); }}
            />
          </div>

          {/* Outbound emails (pending -> sending -> sent/failed) */}
          <OutboundSection
            items={outbound.data?.items ?? []}
            loading={outbound.isLoading}
            onSend={(id) => sendOutboundMut.mutate(id)}
            onDelete={(id) => deleteOutboundMut.mutate(id)}
            sending={sendOutboundMut.isPending}
          />
        </>
      )}

      {/* Compose dialog */}
      <ComposeDialog
        open={composeOpen}
        onClose={() => setComposeOpen(false)}
        replyTo={replyTo}
        replyBodyText={body.data?.plain_text ?? null}
        onSend={(d) => createOutboundMut.mutate(d)}
        sending={createOutboundMut.isPending}
      />
    </div>
  );
}
