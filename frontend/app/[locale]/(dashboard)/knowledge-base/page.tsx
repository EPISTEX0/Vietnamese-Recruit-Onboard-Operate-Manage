'use client';

import React, { useState, useCallback, useRef } from 'react';

import { useLocale, useTranslations } from 'next-intl';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen, Upload, FileText, Search, ChevronLeft, ChevronRight,
  CheckCircle, Clock, AlertTriangle, XCircle, Loader2,
  Pencil, UploadCloud, Trash2, Filter, Info,
} from 'lucide-react';
import {
  uploadDocument,
  listDocuments,
  getDocumentDetail,
  updateDocumentMetadata,
  replaceDocumentFile,
  deleteDocument,
} from '@/lib/api/knowledge-base';
import type {
  DocumentListItem,
  DocumentListResponse,
  DocumentDetail,
  DocumentStatus,
  KbType,
  DocumentUpdateRequest,
} from '@/lib/api/knowledge-base';
import {
  PageHeader, Card, SectionTitle, Loading, LoadingRows, EmptyState,
  Badge, ButtonPrimary, ButtonGhost, ButtonDanger, TextInput, Select,
  Field, Modal, ErrorBanner, formatDateTime, statusTone,
} from '@/components/shared-ui';

// ---------------------------------------------------------------------------
// Helpers (no translations needed)
// ---------------------------------------------------------------------------

const PAGE_SIZE = 10;

function DocumentIcon({ mimeType }: { mimeType: string }) {
  if (mimeType.includes('pdf')) return <FileText className="w-5 h-5 text-rose-500" />;
  if (mimeType.includes('word') || mimeType.includes('doc')) return <FileText className="w-5 h-5 text-blue-500" />;
  return <FileText className="w-5 h-5 text-slate-400" />;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function KnowledgeBasePage() {
  const locale = useLocale();
  const t = useTranslations('knowledgeBase');

  const [page, setPage] = useState(1);
  const [showUpload, setShowUpload] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<KbType>('hr');

  // Filters
  const [filterCategory, setFilterCategory] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');

  // Action modals
  const [editingDoc, setEditingDoc] = useState<DocumentListItem | null>(null);
  const [deletingDoc, setDeletingDoc] = useState<DocumentListItem | null>(null);
  const [reuploadDoc, setReuploadDoc] = useState<DocumentListItem | null>(null);
  const reuploadInputRef = useRef<HTMLInputElement>(null);

  const queryClient = useQueryClient();

  // ── Constants ──

  const CATEGORIES = [
    { value: 'general', label: t('general') },
    { value: 'policy', label: t('policy') },
    { value: 'procedure', label: t('procedure') },
    { value: 'form', label: t('form') },
    { value: 'training', label: t('training') },
    { value: 'legal', label: t('legal') },
    { value: 'other', label: t('other') },
  ];

  const CATEGORY_LABELS: Record<string, string> = Object.fromEntries(
    CATEGORIES.map((c) => [c.value, c.label]),
  );

  const KB_TABS: { value: KbType; label: string }[] = [
    { value: 'hr', label: t('hrTab') },
    { value: 'employee', label: t('employeeTab') },
  ];

  const STATUS_OPTIONS: { value: string; label: string }[] = [
    { value: 'all', label: t('allStatuses') },
    { value: 'ready', label: t('statusReady') },
    { value: 'pending', label: t('statusPending') },
    { value: 'processing', label: t('statusProcessing') },
    { value: 'error', label: t('statusError') },
  ];

  const CATEGORY_FILTER_OPTIONS: { value: string; label: string }[] = [
    { value: 'all', label: t('allCategories') },
    ...CATEGORIES,
  ];

  const STATUS_META: Record<DocumentStatus, { label: string; tone: 'slate' | 'amber' | 'emerald' | 'rose' | 'sky' }> = {
    pending: { label: t('statusPending'), tone: 'slate' },
    processing: { label: t('statusProcessing'), tone: 'sky' },
    ready: { label: t('statusReady'), tone: 'emerald' },
    error: { label: t('statusError'), tone: 'rose' },
  };

  function StatusBadge({ status }: { status: DocumentStatus }) {
    const meta = STATUS_META[status] ?? { label: status, tone: 'slate' as const };
    return <Badge tone={meta.tone}>{meta.label}</Badge>;
  }

  // ── Data ──

  const { data, isLoading, error } = useQuery<DocumentListResponse>({
    queryKey: ['kb-documents', activeTab, page, filterCategory, filterStatus],
    queryFn: () =>
      listDocuments(
        activeTab,
        page,
        PAGE_SIZE,
        filterCategory,
        filterStatus,
      ),
    staleTime: 10 * 1000,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const hasActiveFilters = filterCategory !== 'all' || filterStatus !== 'all';

  const handleTabChange = (tab: KbType) => {
    setActiveTab(tab);
    setPage(1);
    setFilterCategory('all');
    setFilterStatus('all');
  };

  const handleResetFilters = () => {
    setFilterCategory('all');
    setFilterStatus('all');
    setPage(1);
  };

  // ── Re-upload mutation ──
  const reuploadMutation = useMutation({
    mutationFn: async ({ docId, file }: { docId: string; file: File }) => {
      return replaceDocumentFile(docId, file, activeTab);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-documents', activeTab] });
      setReuploadDoc(null);
    },
    onError: () => {
      // Error handled in the UI via the inline input
    },
  });

  const handleReuploadClick = (doc: DocumentListItem) => {
    setReuploadDoc(doc);
    setTimeout(() => {
      reuploadInputRef.current?.click();
    }, 100);
  };

  const handleReuploadFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && reuploadDoc) {
      reuploadMutation.mutate({ docId: reuploadDoc.id, file });
    }
    if (e.target) e.target.value = '';
  };

  const kbLabel = activeTab === 'hr' ? t('hrTab') : t('employeeTab');

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <PageHeader
        icon={BookOpen}
        title={t('title')}
        subtitle={t('subtitle')}
        actions={
          <ButtonPrimary onClick={() => setShowUpload(true)}>
            <Upload className="w-4 h-4" />
            {t('uploadDoc')}
          </ButtonPrimary>
        }
      />

      {/* Tabs */}
      <div className="flex items-center gap-1 bg-slate-100 rounded-xl p-1 w-fit">
        {KB_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => handleTabChange(tab.value)}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition-all ${
              activeTab === tab.value
                ? 'bg-white text-indigo-700 shadow-sm'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400" />
          <Select
            value={filterCategory}
            onChange={(e) => { setFilterCategory(e.target.value); setPage(1); }}
            className="text-sm"
          >
            {CATEGORY_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </div>
        <Select
          value={filterStatus}
          onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}
          className="text-sm"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </Select>
        {hasActiveFilters && (
          <button
            onClick={handleResetFilters}
            className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
          >
            {t('clearFilters')}
          </button>
        )}
        {data && (
          <span className="text-xs text-slate-400 ml-auto">
            {t('totalDocs', { count: data.total })}
          </span>
        )}
      </div>

      {/* Document List */}
      <Card>
        <SectionTitle icon={FileText}>
          {t('docList', { kbType: kbLabel })}
        </SectionTitle>

        {isLoading ? (
          <LoadingRows count={5} />
        ) : error ? (
          <ErrorBanner error={error} />
        ) : data?.items.length ? (
          <div className="space-y-2">
            {data.items.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                labels={{
                  editInfo: t('editInfo'),
                  reuploadFile: t('reuploadFile'),
                  deleteDoc: t('deleteDoc'),
                  chunks: (count: number) => t('chunks', { count }),
                  processingError: t('processingError'),
                }}
                categoryLabels={CATEGORY_LABELS}
                onEdit={setEditingDoc}
                onReupload={handleReuploadClick}
                onDelete={setDeletingDoc}
                onDetail={setSelectedDocId}
              />
            ))}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between pt-3">
                <span className="text-xs text-slate-500">
                  {t('pageOf', { page, total: totalPages })}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setPage((p) => p + 1)}
                    disabled={page >= totalPages}
                    className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 transition-all"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : hasActiveFilters ? (
          <EmptyState
            filtered={true}
            onReset={handleResetFilters}
            emptyFiltered={t('noMatchTitle')}
            hintFiltered={t('noMatchHint')}
          />
        ) : (
          <EmptyState
            filtered={false}
            emptyData={t('emptyTitle', { kbType: kbLabel })}
            hintData={t('emptyHint')}
          />
        )}
      </Card>

      {/* Modals with translations passed down */}
      <UploadModal
        t={t}
        open={showUpload}
        onClose={() => setShowUpload(false)}
        kbType={activeTab}
        categories={CATEGORIES}
      />
      <DetailModal
        t={t}
        docId={selectedDocId}
        open={!!selectedDocId}
        onClose={() => setSelectedDocId(null)}
        kbType={activeTab}
        categoryLabels={CATEGORY_LABELS}
      />
      <EditMetadataModal
        t={t}
        doc={editingDoc}
        open={!!editingDoc}
        onClose={() => setEditingDoc(null)}
        kbType={activeTab}
        categories={CATEGORIES}
      />
      <DeleteConfirmModal
        t={t}
        doc={deletingDoc}
        open={!!deletingDoc}
        onClose={() => setDeletingDoc(null)}
        kbType={activeTab}
      />

      {/* Hidden file input for re-upload */}
      <input
        ref={reuploadInputRef}
        type="file"
        accept=".pdf,.docx,.doc,.txt"
        className="hidden"
        onChange={handleReuploadFileChange}
      />

      {/* Re-upload status indicator */}
      {reuploadMutation.isPending && reuploadDoc && (
        <div className="fixed bottom-4 right-4 z-50 p-3 bg-indigo-50 border border-indigo-200 rounded-xl shadow-lg flex items-center gap-3">
          <Loader2 className="w-4 h-4 animate-spin text-indigo-600" />
          <div>
            <p className="text-sm font-medium text-indigo-700">{t('reuploading')}</p>
            <p className="text-xs text-indigo-500">{reuploadDoc.display_name}</p>
          </div>
        </div>
      )}
      {reuploadMutation.isError && (
        <div className="fixed bottom-4 right-4 z-50 p-3 bg-rose-50 border border-rose-200 rounded-xl shadow-lg">
          <p className="text-sm font-medium text-rose-700">{t('reuploadFailed')}</p>
          <p className="text-xs text-rose-600">
            {reuploadMutation.error instanceof Error ? reuploadMutation.error.message : t('unknownError')}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Document Row
// ---------------------------------------------------------------------------

function DocumentRow({
  doc,
  labels,
  categoryLabels,
  onEdit,
  onReupload,
  onDelete,
  onDetail,
}: {
  doc: DocumentListItem;
  labels: {
    editInfo: string;
    reuploadFile: string;
    deleteDoc: string;
    chunks: (count: number) => string;
    processingError: string;
  };
  categoryLabels: Record<string, string>;
  onEdit: (doc: DocumentListItem) => void;
  onReupload: (doc: DocumentListItem) => void;
  onDelete: (doc: DocumentListItem) => void;
  onDetail: (id: string) => void;
}) {
  const [showErrorTooltip, setShowErrorTooltip] = useState(false);

  return (
    <div className="flex items-center gap-4 p-3 bg-slate-50 rounded-xl border border-slate-100 hover:border-indigo-200 transition-colors">
      <DocumentIcon mimeType={doc.mime_type} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <button
            className="text-sm font-semibold text-slate-900 truncate hover:text-indigo-600 transition-colors text-left"
            onClick={() => onDetail(doc.id)}
          >
            {doc.display_name}
          </button>
          <StatusBadge status={doc.status as DocumentStatus} />
          {doc.status === 'error' && doc.error_message && (
            <div className="relative">
              <button
                className="text-rose-400 hover:text-rose-600 transition-colors"
                onMouseEnter={() => setShowErrorTooltip(true)}
                onMouseLeave={() => setShowErrorTooltip(false)}
                onClick={() => setShowErrorTooltip(!showErrorTooltip)}
              >
                <Info className="w-4 h-4" />
              </button>
              {showErrorTooltip && (
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-3 bg-rose-50 border border-rose-200 rounded-xl shadow-lg z-10">
                  <p className="text-xs font-semibold text-rose-700 mb-1">{labels.processingError}</p>
                  <p className="text-xs text-rose-600 whitespace-pre-wrap line-clamp-6">
                    {doc.error_message}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className="text-[11px] text-slate-400">
            {doc.file_name}
          </span>
          <span className="text-[11px] text-slate-400">
            {formatFileSize(doc.file_size)}
          </span>
          {doc.chunk_count > 0 && (
            <span className="text-[11px] text-slate-400">
              {labels.chunks(doc.chunk_count)}
            </span>
          )}
        </div>
      </div>
      <div className="text-right shrink-0 flex items-center gap-1">
        <div className="mr-3">
          <span className="text-[11px] text-slate-400 block">
            {categoryLabels[doc.category] ?? doc.category}
          </span>
          <div className="text-[10px] text-slate-400 mt-0.5">
            {formatDateTime(doc.created_at)}
          </div>
        </div>

        {/* Action buttons */}
        <button
          onClick={(e) => { e.stopPropagation(); onEdit(doc); }}
          className="p-1.5 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all"
          title={labels.editInfo}
        >
          <Pencil className="w-4 h-4" />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onReupload(doc); }}
          className="p-1.5 rounded-lg text-slate-400 hover:text-amber-600 hover:bg-amber-50 transition-all"
          title={labels.reuploadFile}
        >
          <UploadCloud className="w-4 h-4" />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(doc); }}
          className="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-all"
          title={labels.deleteDoc}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status badge helper
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: DocumentStatus }) {
  const STATUS_META: Record<DocumentStatus, { label: string; tone: 'slate' | 'amber' | 'emerald' | 'rose' | 'sky' }> = {
    pending: { label: 'Pending', tone: 'slate' },
    processing: { label: 'Processing', tone: 'sky' },
    ready: { label: 'Ready', tone: 'emerald' },
    error: { label: 'Error', tone: 'rose' },
  };
  const meta = STATUS_META[status] ?? { label: status, tone: 'slate' as const };
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

// ---------------------------------------------------------------------------
// Upload Modal
// ---------------------------------------------------------------------------

function UploadModal({
  t, open, onClose, kbType, categories,
}: {
  t: (key: string, values?: Record<string, string | number | Date>) => string;
  open: boolean;
  onClose: () => void;
  kbType: KbType;
  categories: { value: string; label: string }[];
}) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [category, setCategory] = useState('general');
  const [error, setError] = useState<string | null>(null);

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error(t('selectFile'));
      if (!displayName.trim()) throw new Error(t('enterName'));
      return uploadDocument(file, displayName.trim(), category, kbType);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-documents', kbType] });
      setFile(null);
      setDisplayName('');
      setCategory('general');
      setError(null);
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    if (f && !displayName) {
      const name = f.name.replace(/\.[^/.]+$/, '');
      setDisplayName(name);
    }
  };

  const kbLabel = kbType === 'hr' ? t('hrTab') : t('employeeTab');

  return (
    <Modal open={open} onClose={onClose} title={t('uploadTitle', { kbLabel })}>
      <div className="space-y-4">
        {error && <ErrorBanner error={error} />}

        <Field label={t('fileField')} hint={t('fileHint')}>
          <input
            type="file"
            accept=".pdf,.docx,.doc,.txt"
            onChange={handleFileChange}
            className="w-full text-sm text-slate-600 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-xs file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
          />
        </Field>

        <Field label={t('displayName')}>
          <TextInput
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={t('displayNamePlaceholder')}
          />
        </Field>

        <Field label={t('category')}>
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </Select>
        </Field>

        <div className="flex items-center justify-end gap-2 pt-2">
          <ButtonGhost onClick={onClose}>{t('cancel')}</ButtonGhost>
          <ButtonPrimary
            onClick={() => uploadMutation.mutate()}
            disabled={uploadMutation.isPending || !file}
          >
            {uploadMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('uploading')}
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" />
                {t('uploadBtn')}
              </>
            )}
          </ButtonPrimary>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Edit Metadata Modal
// ---------------------------------------------------------------------------

function EditMetadataModal({
  t, doc, open, onClose, kbType, categories,
}: {
  t: (key: string, values?: Record<string, string | number | Date>) => string;
  doc: DocumentListItem | null;
  open: boolean;
  onClose: () => void;
  kbType: KbType;
  categories: { value: string; label: string }[];
}) {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState(doc?.display_name ?? '');
  const [category, setCategory] = useState(doc?.category ?? 'general');
  const [description, setDescription] = useState(doc?.description ?? '');
  const [error, setError] = useState<string | null>(null);

  React.useEffect(() => {
    if (doc) {
      setDisplayName(doc.display_name);
      setCategory(doc.category);
      setDescription(doc.description ?? '');
      setError(null);
    }
  }, [doc]);

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!doc) throw new Error(t('noDocToEdit'));
      if (!displayName.trim()) throw new Error(t('enterName'));
      const body: DocumentUpdateRequest = { display_name: displayName.trim(), category };
      if (description.trim()) {
        body.description = description.trim();
      }
      return updateDocumentMetadata(doc.id, body, kbType);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-documents', kbType] });
      setError(null);
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  return (
    <Modal open={open} onClose={onClose} title={t('editTitle')}>
      <div className="space-y-4">
        {error && <ErrorBanner error={error} />}
        <p className="text-xs text-slate-500">
          {t('editHint')}
        </p>

        <Field label={t('displayName')}>
          <TextInput
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={t('displayNamePlaceholder')}
          />
        </Field>

        <Field label={t('category')}>
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </Select>
        </Field>

        <Field label={t('description')} hint={t('descriptionHint')}>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400"
            placeholder={t('descriptionPlaceholder')}
          />
        </Field>

        <div className="flex items-center justify-end gap-2 pt-2">
          <ButtonGhost onClick={onClose}>{t('cancel')}</ButtonGhost>
          <ButtonPrimary
            onClick={() => updateMutation.mutate()}
            disabled={updateMutation.isPending || !displayName.trim()}
          >
            {updateMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('saving')}
              </>
            ) : (
              t('saveChanges')
            )}
          </ButtonPrimary>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Delete Confirm Modal
// ---------------------------------------------------------------------------

function DeleteConfirmModal({
  t, doc, open, onClose, kbType,
}: {
  t: (key: string, values?: Record<string, string | number | Date>) => string;
  doc: DocumentListItem | null;
  open: boolean;
  onClose: () => void;
  kbType: KbType;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const deleteMutation = useMutation({
    mutationFn: async () => {
      if (!doc) throw new Error(t('noDocToDelete'));
      return deleteDocument(doc.id, kbType);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-documents', kbType] });
      setError(null);
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  return (
    <Modal open={open} onClose={onClose} title={t('confirmDeleteTitle')}>
      <div className="space-y-4">
        {error && <ErrorBanner error={error} />}

        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-rose-500 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-rose-700">
                {t('confirmDeleteText')}
              </p>
            </div>
          </div>
        </div>

        {doc && (
          <div className="text-sm text-slate-700">
            <span className="font-medium">{doc.display_name}</span>
            <span className="text-slate-400"> — {formatFileSize(doc.file_size)}</span>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2">
          <ButtonGhost onClick={onClose}>{t('cancel')}</ButtonGhost>
          <ButtonDanger
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('deleting')}
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                {t('permanentlyDelete')}
              </>
            )}
          </ButtonDanger>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Document Detail Modal
// ---------------------------------------------------------------------------

function DetailModal({
  t, docId, open, onClose, kbType, categoryLabels,
}: {
  t: (key: string, values?: Record<string, string | number | Date>) => string;
  docId: string | null;
  open: boolean;
  onClose: () => void;
  kbType: KbType;
  categoryLabels: Record<string, string>;
}) {
  const { data: doc, isLoading, error } = useQuery<DocumentDetail>({
    queryKey: ['kb-document-detail', kbType, docId],
    queryFn: () => getDocumentDetail(docId!, kbType),
    enabled: !!docId && open,
  });

  return (
    <Modal open={open} onClose={onClose} title={t('docDetail')}>
      {isLoading ? (
        <Loading label={t('loadingDetail')} />
      ) : error ? (
        <ErrorBanner error={error} />
      ) : doc ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('displayName')}>
              <p className="text-sm text-slate-900 font-medium">{doc.display_name}</p>
            </Field>
            <Field label={t('status')}>
              <StatusBadge status={doc.status} />
            </Field>
            <Field label={t('category')}>
              <p className="text-sm text-slate-700">{categoryLabels[doc.category] ?? doc.category}</p>
            </Field>
            <Field label={t('fileNameField')}>
              <p className="text-sm text-slate-700 truncate">{doc.file_name}</p>
            </Field>
            <Field label={t('fileSize')}>
              <p className="text-sm text-slate-700">{formatFileSize(doc.file_size)}</p>
            </Field>
            <Field label={t('documentType')}>
              <p className="text-sm text-slate-700">{doc.mime_type}</p>
            </Field>
            <Field label={t('chunksCount')}>
              <p className="text-sm text-slate-700">{doc.chunk_count}</p>
            </Field>
            <Field label={t('uploadDate')}>
              <p className="text-sm text-slate-700">{formatDateTime(doc.created_at)}</p>
            </Field>
          </div>
          {doc.description && (
            <Field label={t('description')}>
              <p className="text-sm text-slate-600 whitespace-pre-wrap">{doc.description}</p>
            </Field>
          )}
          {doc.error_message && (
            <div className="p-3 bg-rose-50 border border-rose-200 rounded-xl">
              <p className="text-xs font-semibold text-rose-700 mb-1">{t('processingError')}:</p>
              <p className="text-xs text-rose-600 whitespace-pre-wrap">{doc.error_message}</p>
            </div>
          )}
        </div>
      ) : null}
    </Modal>
  );
}
