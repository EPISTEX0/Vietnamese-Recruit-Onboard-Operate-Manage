export interface Employee {
  id: string;
  employee_code: string;
  full_name: string;
  email: string;
  phone: string | null;
  date_of_birth: string | null;
  gender: string | null;
  address: string | null;
  department_id: string | null;
  position_id: string | null;
  start_date: string | null;
  id_number: string | null;
  tax_code: string | null;
  contract_type: string | null;
  candidate_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface EmployeeListResponse {
  items: Employee[];
  total: number;
  page: number;
  page_size: number;
}

export interface Department {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
}

export interface Position {
  id: string;
  name: string;
  department_id: string | null;
  created_at: string;
}

export interface EmployeeDocument {
  id: string;
  employee_id: string;
  document_type: string;
  file_name: string;
  file_size: number;
  mime_type: string;
  description: string | null;
  uploaded_at: string;
}

export interface ImportResult {
  total_rows: number;
  success_count: number;
  error_count: number;
  errors: Array<{ row: number; message: string }>;
  departments_created?: number;
  positions_created?: number;
}

export interface EmployeeCreateData {
  full_name: string;
  email: string;
  phone?: string;
  date_of_birth?: string;
  gender?: string;
  address?: string;
  department_id?: string;
  position_id?: string;
  start_date?: string;
  id_number?: string;
  tax_code?: string;
  contract_type?: string;
}

export type EmployeeUpdateData = Partial<EmployeeCreateData>;

export interface DepartmentCreateData {
  name: string;
  description?: string;
}

export interface PositionCreateData {
  name: string;
  department_id?: string;
}

// ---------------------------------------------------------------------------
// Organization Google Connection Types (identity router)
// ---------------------------------------------------------------------------

export interface OrganizationGoogleConnectionResponse {
  status: OrganizationGoogleConnectionStatus;
  email: string | null;
  has_secret: boolean;
  redirect_url?: string | null;
}

export type CapabilityHealthState =
  | "healthy"
  | "unhealthy"
  | "unknown"
  | "unavailable";

export interface CapabilityHealth {
  capability: string;
  health: CapabilityHealthState;
  label: string;
  description?: string;
}

// ---------------------------------------------------------------------------
// Gmail Integration Types
// ---------------------------------------------------------------------------

// Renamed from `ConnectionStatus` (#363): the old name collided with
// backend's `gmail/domain/enums.py::ConnectionStatus`, a *different* entity
// (`connected | disconnected | token_expired`) that this type never described.
//
// `degraded` is not a theoretical value — it is the sanitize fallback that
// runs on every read:
// `backend/src/modules/identity/application/organization_google_connection_service.py:134`
// (`VALID_CONNECTION_STATUSES`, same file:42 and
// `connection_state_repository.py:12`). An unrecognized DB value becomes
// `"degraded"` before it ever reaches the client.
export type OrganizationGoogleConnectionStatus =
  | "connected"
  | "disconnected"
  | "degraded"
  | "reauthorization_required";

// `EmailMessageEntity.processing_status` (backend `gmail/domain/entities.py:58`)
// is a raw `str` column with no backend StrEnum. Closed set confirmed by
// grepping every literal assignment site (#363):
// `gmail/domain/entities.py:58` (default "unprocessed"),
// `gmail/application/classification_service.py:144,282,400,404,430,435,444,466,471`,
// `gmail/api/router.py:669,693,703,712,714,1345`.
export type EmailProcessingStatus =
  | "unprocessed"
  | "classification_failed"
  | "needs_classification"
  | "classified"
  | "cv_processing"
  | "needs_review"
  | "ai_unavailable"
  | "permanently_failed";

export interface EmailMessage {
  id: string;
  gmail_message_id: string;
  gmail_thread_id: string;
  subject: string;
  sender_email: string;
  sender_name: string;
  recipient_emails: string[];
  cc_emails: string[];
  received_at: string; // ISO datetime
  snippet: string;
  label_ids: string[];
  has_attachments: boolean;
  category: string | null;
  processing_status?: EmailProcessingStatus;
}

export interface MessageBodyResponse {
  plain_text: string | null;
  html: string | null;
}

export interface SendEmailRequest {
  to: string[];
  cc?: string[];
  subject: string;
  body_html?: string;
  body_text?: string;
  reply_to_message_id?: string;
}

export interface SendEmailResponse {
  message_id: string;
  thread_id: string;
}

export interface LabelRemoveRequest {
  label_name: string;
}

export interface SyncResponse {
  synced_count: number;
  // Backend has one construction site (`gmail/api/router.py:144`), always
  // literal `"ok"` — not a documented multi-value status.
  status: "ok";
}

export interface AttachmentMetadata {
  attachment_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

export interface AttachmentsResponse {
  fetched_count: number;
  skipped_count: number;
  total_count: number;
  attachments: AttachmentMetadata[];
}

export class ApiError extends Error {
  public statusCode: number;
  public errorCode: string;
  public details?: Record<string, unknown>;
  /** Per-field Vietnamese error messages from Pydantic validation (422) detail arrays. */
  public fieldErrors?: Record<string, string>;

  constructor(
    statusCode: number,
    errorCode: string,
    message: string,
    details?: Record<string, unknown>,
    fieldErrors?: Record<string, string>,
  ) {
    super(message);
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.errorCode = errorCode;
    this.details = details;
    this.fieldErrors = fieldErrors;
  }

  /** True when this error carries field-level validation messages. */
  isValidationError(): boolean {
    return this.fieldErrors != null && Object.keys(this.fieldErrors).length > 0;
  }
}
