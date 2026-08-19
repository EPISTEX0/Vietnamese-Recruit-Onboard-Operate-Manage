/**
 * Pydantic validation error → Vietnamese translator.
 *
 * Maps Pydantic V2 error types and backend field names to human-readable
 * Vietnamese labels. Used by the shared API client to convert 422 detail
 * arrays into field-level error messages that never leak raw JSON to the UI.
 *
 * Fallback: if a type or field is unmapped, the raw Pydantic msg is
 * combined with the field name so the user still sees something readable.
 */

// ---------------------------------------------------------------------------
// Pydantic error type → Vietnamese message template
// ---------------------------------------------------------------------------

type ErrorCtx = Record<string, unknown> | undefined;

/** Returns a Vietnamese message for a Pydantic error type + optional ctx. */
function translateType(type: string, ctx: ErrorCtx): string {
  // Strip subtype suffix (e.g. "value_error.missing" → try both)
  const base = type.includes(".") ? type.split(".")[0] : type;

  switch (type) {
    // ── string constraints ──
    case "string_too_short":
      if (ctx?.min_length !== undefined) {
        const min = Number(ctx.min_length);
        if (min <= 1) return "không được để trống";
        return `phải có ít nhất ${min} ký tự`;
      }
      return "quá ngắn";
    case "string_too_long":
      if (ctx?.max_length !== undefined) {
        return `không được vượt quá ${ctx.max_length} ký tự`;
      }
      return "quá dài";
    case "string_pattern_mismatch":
      return "không đúng định dạng yêu cầu";

    // ── value errors (subtypes) ──
    case "value_error.missing":
      return "không được để trống";
    case "value_error.email":
      return "không đúng định dạng email";
    case "value_error.url":
      return "không đúng định dạng URL";
    case "value_error.number.not_gt":
      if (ctx?.gt !== undefined) return `phải lớn hơn ${ctx.gt}`;
      return "giá trị không hợp lệ";
    case "value_error.number.not_ge":
      if (ctx?.ge !== undefined) return `phải lớn hơn hoặc bằng ${ctx.ge}`;
      return "giá trị không hợp lệ";
    case "value_error.number.not_lt":
      if (ctx?.lt !== undefined) return `phải nhỏ hơn ${ctx.lt}`;
      return "giá trị không hợp lệ";
    case "value_error.number.not_le":
      if (ctx?.le !== undefined) return `phải nhỏ hơn hoặc bằng ${ctx.le}`;
      return "giá trị không hợp lệ";
    case "value_error":
      return "giá trị không hợp lệ";

    // ── type errors ──
    case "type_error.integer":
      return "phải là số nguyên";
    case "type_error.float":
      return "phải là số thực";
    case "type_error.bool":
      return "phải là đúng/sai";
    case "type_error.str":
      return "phải là chuỗi";
    case "type_error.list":
      return "phải là danh sách";
    case "type_error.dict":
      return "phải là đối tượng";
    case "type_error":
      return "sai kiểu dữ liệu";

    // ── numeric constraints ──
    case "greater_than":
      if (ctx?.gt !== undefined) return `phải lớn hơn ${ctx.gt}`;
      return "giá trị quá nhỏ";
    case "greater_than_equal":
      if (ctx?.ge !== undefined) return `phải lớn hơn hoặc bằng ${ctx.ge}`;
      return "giá trị quá nhỏ";
    case "less_than":
      if (ctx?.lt !== undefined) return `phải nhỏ hơn ${ctx.lt}`;
      return "giá trị quá lớn";
    case "less_than_equal":
      if (ctx?.le !== undefined) return `phải nhỏ hơn hoặc bằng ${ctx.le}`;
      return "giá trị quá lớn";
    case "multiple_of":
      if (ctx?.multiple_of !== undefined) return `phải là bội số của ${ctx.multiple_of}`;
      return "giá trị không hợp lệ";

    // ── collection constraints ──
    case "too_short":
      if (ctx?.min_length !== undefined) return `phải có ít nhất ${ctx.min_length} phần tử`;
      if (ctx?.min_items !== undefined) return `phải có ít nhất ${ctx.min_items} phần tử`;
      return "danh sách quá ngắn";
    case "too_long":
      if (ctx?.max_length !== undefined) return `không được vượt quá ${ctx.max_length} phần tử`;
      if (ctx?.max_items !== undefined) return `không được vượt quá ${ctx.max_items} phần tử`;
      return "danh sách quá dài";

    // ── URL constraints ──
    case "url_parsing":
      return "không phải URL hợp lệ";
    case "url_scheme":
      return "URL phải bắt đầu bằng http:// hoặc https://";

    // ── enum / choices ──
    case "enum":
      return "giá trị không nằm trong danh sách cho phép";

    // ── missing required ──
    case "missing":
      return "không được để trống";

    // ── extra fields ──
    case "extra_forbidden":
      return "chứa trường không được phép";

    // ── base type (stripped subtypes try base) ──
    default:
      // fall through to base type check below
      break;
  }

  // Try base type for subtypes we didn't match explicitly
  if (base !== type) {
    switch (base) {
      case "value_error":
        return "giá trị không hợp lệ";
      case "type_error":
        return "sai kiểu dữ liệu";
      case "string_type":
        return "phải là chuỗi";
      case "int_type":
        return "phải là số nguyên";
      case "float_type":
        return "phải là số thực";
      case "bool_type":
        return "phải là đúng/sai";
      case "list_type":
        return "phải là danh sách";
      case "dict_type":
        return "phải là đối tượng";
      case "date_type":
        return "phải là ngày hợp lệ";
      case "datetime_type":
        return "phải là ngày giờ hợp lệ";
      default:
        break;
    }
  }

  return "giá trị không hợp lệ";
}

// ---------------------------------------------------------------------------
// Field name → Vietnamese label
// ---------------------------------------------------------------------------

const FIELD_LABELS: Record<string, string> = {
  // ── AI Configuration ──
  provider: "Nhà cung cấp AI",
  base_url: "Base URL",
  model: "Model",
  api_key: "API Key",

  // ── Auth ──
  email: "Email",
  password: "Mật khẩu",
  password_confirmation: "Xác nhận mật khẩu",
  current_password: "Mật khẩu hiện tại",
  new_password: "Mật khẩu mới",
  name: "Tên",
  organization_name: "Tên tổ chức",

  // ── Employee ──
  full_name: "Họ và tên",
  phone: "Số điện thoại",
  date_of_birth: "Ngày sinh",
  gender: "Giới tính",
  address: "Địa chỉ",
  department_id: "Phòng ban",
  position_id: "Chức vụ",
  start_date: "Ngày bắt đầu",
  id_number: "Số CMND/CCCD",
  tax_code: "Mã số thuế",
  contract_type: "Loại hợp đồng",
  employee_code: "Mã nhân viên",

  // ── Department / Position ──
  description: "Mô tả",

  // ── Gmail ──
  to: "Người nhận",
  cc: "CC",
  subject: "Tiêu đề",
  body_html: "Nội dung HTML",
  body_text: "Nội dung văn bản",
  reply_to_message_id: "ID thư trả lời",
  label_name: "Tên nhãn",

  // ── Domains ──
  domain: "Domain",

  // ── OAuth ──
  client_id: "Client ID",
  client_secret: "Client Secret",
  redirect_uri: "Redirect URI",

  // ── Attendance ──
  check_in_time: "Giờ check-in",
  check_out_time: "Giờ check-out",
  date: "Ngày",
  reason: "Lý do",
  leave_type_id: "Loại nghỉ phép",
  hours: "Số giờ",

  // ── Leave / Overtime ──
      end_date: "Ngày kết thúc",
  start_time: "Giờ bắt đầu",
  end_time: "Giờ kết thúc",
  note: "Ghi chú",

  // ── Payroll ──
  period_id: "Kỳ lương",
  salary: "Lương",
  allowance: "Phụ cấp",
  deduction: "Khấu trừ",

  // ── Recruitment ──
  title: "Tiêu đề",
  job_opening_id: "Tin tuyển dụng",
  cv_file: "File CV",
  source: "Nguồn",
  status: "Trạng thái",

  // ── Role / Permission ──
  role: "Vai trò",

  // ── Import ──
  file: "File",

  // ── Setup ──
  company_name: "Tên công ty",
  admin_email: "Email quản trị",
  admin_password: "Mật khẩu quản trị",
};

/** Get Vietnamese label for a field name. Falls back to raw key. */
function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface FormattedFieldError {
  field: string;
  label: string;
  message: string;
}

/**
 * Format a single Pydantic validation error item into a Vietnamese field error.
 *
 * Input example:
 *   { type: "string_too_short", loc: ["body","provider"], msg: "String should have at least 1 character", ctx: { min_length: 1 } }
 *
 * Output:
 *   { field: "provider", label: "Nhà cung cấp AI", message: "không được để trống" }
 */
export function formatSingleError(item: {
  type?: string;
  loc?: unknown;
  msg?: string;
  ctx?: Record<string, unknown>;
}): FormattedFieldError {
  const locArray = Array.isArray(item.loc) ? (item.loc as string[]) : [];
  // Use last segment of loc (usually the field name); skip "body"/"query"/"path" prefixes
  const field = locArray.length > 0 ? String(locArray[locArray.length - 1]) : "unknown";
  const label = fieldLabel(field);
  const type = item.type ?? "value_error";
  const vietMsg = translateType(type, item.ctx);
  const message = `${label}: ${vietMsg}`;
  return { field, label, message };
}

/**
 * Format a Pydantic validation error array (raw detail from 422 response)
 * into Vietnamese field-level errors.
 *
 * Returns:
 * - fieldErrors: Record<fieldName, fullMessage> for direct use in ApiError
 * - fields: array of FormattedFieldError for richer rendering
 * - summary: a one-line Vietnamese summary suitable as ApiError.message
 */
export function formatValidationErrors(
  detail: unknown,
): {
  fieldErrors: Record<string, string>;
  fields: FormattedFieldError[];
  summary: string;
} {
  if (!Array.isArray(detail) || detail.length === 0) {
    return {
      fieldErrors: {},
      fields: [],
      summary: "Vui lòng kiểm tra lại thông tin",
    };
  }

  const items: FormattedFieldError[] = [];
  const fieldErrors: Record<string, string> = {};

  for (const item of detail) {
    if (typeof item !== "object" || item === null) continue;
    const formatted = formatSingleError(item as Record<string, unknown>);
    items.push(formatted);
    // Deduplicate: first error per field wins
    if (!(formatted.field in fieldErrors)) {
      fieldErrors[formatted.field] = formatted.message;
    }
  }

  // Build a summary from all field messages (short enough for a single line)
  const summary =
    items.length === 1
      ? items[0].message
      : `Vui lòng kiểm tra lại: ${items.map((i) => i.message).join("; ")}`;

  return { fieldErrors, fields: items, summary };
}

/**
 * Detect whether a payload detail looks like a Pydantic validation error array.
 */
export function isValidationErrorDetail(
  detail: unknown,
): detail is Array<Record<string, unknown>> {
  if (!Array.isArray(detail) || detail.length === 0) return false;
  // Pydantic errors have { type, loc, msg } shape
  return detail.every(
    (item) =>
      typeof item === "object" &&
      item !== null &&
      "type" in item &&
      "loc" in item &&
      "msg" in item,
  );
}
