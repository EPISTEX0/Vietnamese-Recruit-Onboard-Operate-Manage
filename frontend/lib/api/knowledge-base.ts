/**
 * Knowledge Base API client — typed functions for KB document endpoints.
 *
 * Wired to NEXT_PUBLIC_API_URL via lib/api/client.
 * All requests send credentials: "include" for HttpOnly cookie auth.
 *
 * Supports both HR and Employee KB via kb_type parameter (Issue #260).
 * Adds metadata update, file replacement, and hard delete (Issue #261).
 */

import { apiFetch } from "./client";
import type { ApiError } from "./types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DocumentStatus = "pending" | "processing" | "ready" | "error";

export type KbType = "hr" | "employee";

export interface DocumentUploadResponse {
  document_id: string;
  display_name: string;
  status: DocumentStatus;
  category: string;
  file_name: string;
  file_size: number;
  mime_type: string;
  created_at: string;
}

export interface DocumentListItem {
  id: string;
  display_name: string;
  category: string;
  status: DocumentStatus;
  file_name: string;
  file_size: number;
  mime_type: string;
  chunk_count: number;
  description: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  items: DocumentListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentDetail {
  id: string;
  display_name: string;
  category: string;
  status: DocumentStatus;
  file_name: string;
  storage_path: string;
  file_size: number;
  mime_type: string;
  chunk_count: number;
  description: string | null;
  error_message: string | null;
  kb_type: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentUpdateRequest {
  display_name?: string;
  category?: string;
  description?: string;
}

export interface DocumentUpdateResponse {
  id: string;
  display_name: string;
  category: string;
  status: DocumentStatus;
  description: string | null;
  updated_at: string;
}

export interface MessageResponse {
  message: string;
  document_id: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

const BASE = "/api/knowledge-base";

/**
 * Upload a document to the Knowledge Base (HR or Employee).
 *
 * Sends a multipart/form-data request with the file, metadata, and kb_type.
 * Returns the created document with status "pending".
 */
export async function uploadDocument(
  file: File,
  displayName: string,
  category: string = "general",
  kbType: KbType = "hr",
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("display_name", displayName);
  formData.append("category", category);
  formData.append("kb_type", kbType);

  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}${BASE}/documents`,
    {
      method: "POST",
      credentials: "include",
      body: formData,
    },
  );

  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const message =
      payload?.detail?.message ??
      (typeof payload?.detail === "string" ? payload.detail : undefined) ??
      `Upload failed: ${res.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }

  return res.json();
}

/**
 * List documents in a Knowledge Base with pagination and optional filters.
 */
export async function listDocuments(
  kbType: string = "hr",
  page: number = 1,
  pageSize: number = 20,
  category?: string,
  status?: string,
): Promise<DocumentListResponse> {
  const params = new URLSearchParams({
    kb_type: kbType,
    page: String(page),
    page_size: String(pageSize),
  });
  if (category && category !== "all") {
    params.set("category", category);
  }
  if (status && status !== "all") {
    params.set("status", status);
  }
  return apiFetch<DocumentListResponse>(`${BASE}/documents?${params}`);
}

/**
 * Get full detail of a single document (includes error_message if any).
 */
export async function getDocumentDetail(
  documentId: string,
  kbType: string = "hr",
): Promise<DocumentDetail> {
  const params = new URLSearchParams({ kb_type: kbType });
  return apiFetch<DocumentDetail>(`${BASE}/documents/${documentId}?${params}`);
}

/**
 * Update document metadata (PATCH) — no re-indexing (Issue #261).
 */
export async function updateDocumentMetadata(
  documentId: string,
  body: DocumentUpdateRequest,
  kbType: string = "hr",
): Promise<DocumentUpdateResponse> {
  const params = new URLSearchParams({ kb_type: kbType });
  return apiFetch<DocumentUpdateResponse>(
    `${BASE}/documents/${documentId}?${params}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    },
  );
}

/**
 * Replace a document's file (PUT) — re-index after upload (Issue #261).
 */
export async function replaceDocumentFile(
  documentId: string,
  file: File,
  kbType: string = "hr",
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("kb_type", kbType);

  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}${BASE}/documents/${documentId}`,
    {
      method: "PUT",
      credentials: "include",
      body: formData,
    },
  );

  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const message =
      payload?.detail?.message ??
      (typeof payload?.detail === "string" ? payload.detail : undefined) ??
      `Replace failed: ${res.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }

  return res.json();
}

/**
 * Hard-delete a document (DELETE) — removes chunks, MinIO file, and DB row (Issue #261).
 */
export async function deleteDocument(
  documentId: string,
  kbType: string = "hr",
): Promise<MessageResponse> {
  const params = new URLSearchParams({ kb_type: kbType });
  return apiFetch<MessageResponse>(`${BASE}/documents/${documentId}?${params}`, {
    method: "DELETE",
  });
}

// Re-export ApiError type for consumers
export type { ApiError };
