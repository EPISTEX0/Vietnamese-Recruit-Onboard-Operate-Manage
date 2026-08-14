"""Định nghĩa tool cho Employee Assistant.

Employee Assistant tools chỉ giới hạn trong dữ liệu của chính nhân viên đã xác thực.
Mọi Read-Tool đều lọc theo employee_id từ session — LLM
không thể yêu cầu dữ liệu của nhân viên khác vì employee_id không bao giờ được
exposed dưới dạng tham số.

Draft-Tool trả về Draft Action — chúng không bao giờ ghi vào database.
Nhân viên xem xét bản nháp; khi xác nhận, frontend gọi endpoint write thực tế
trực tiếp (human-in-the-loop — xem `CONTEXT.md` § "Nội bộ AI Assistant",
mục **Draft-Tool** và **Draft Action**).

Danh sách tool:
- Read-Tool:    get_my_profile
- Read-Tool:    list_my_documents
- Read-Tool:    get_today_attendance
- Read-Tool:    list_my_attendance_records
- Read-Tool:    list_my_employee_requests
- Read-Tool:    get_my_leave_balance
- Read-Tool:    list_my_payslips
- Draft-Tool:   draft_leave_request
- Draft-Tool:   draft_overtime_request
"""

from __future__ import annotations

from src.modules.assistant.domain.tools import ToolDefinition, ToolKind

# Date regex: YYYY-MM-DD
_DATE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
# Time regex: HH:MM (24-hour)
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
# Max length for reason field
_REASON_MAX_LENGTH = 2000

EMPLOYEE_TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_my_profile",
        display_name="Hồ sơ của tôi",
        kind=ToolKind.READ,
        description=(
            "Xem thông tin hồ sơ của chính nhân viên hiện tại. "
            "Trả về họ tên, email, số điện thoại, ngày sinh, giới tính, địa chỉ, "
            "phòng ban, chức vụ, mã nhân viên, ngày bắt đầu làm việc, và loại hợp đồng. "
            "Sử dụng khi nhân viên hỏi về hồ sơ hoặc thông tin cá nhân của chính họ."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    ToolDefinition(
        name="list_my_documents",
        display_name="Tài liệu của tôi",
        kind=ToolKind.READ,
        description=(
            "Liệt kê các tài liệu đã tải lên của chính nhân viên hiện tại trong kho tài liệu. "
            "Trả về tên file, loại tài liệu, dung lượng, và ngày tải lên cho mỗi tài liệu. "
            "Sử dụng khi nhân viên hỏi về tài liệu, file đã tải lên, hoặc các "
            "file trong kho tài liệu của họ."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    ToolDefinition(
        name="get_today_attendance",
        display_name="Chấm công hôm nay",
        kind=ToolKind.READ,
        description=(
            "Xem thông tin check-in và check-out chấm công hôm nay của nhân viên hiện tại. "
            "Trả về giờ check-in, giờ check-out, và trạng thái cho hôm nay. "
            "Sử dụng khi nhân viên hỏi về chấm công hôm nay, đã check-in chưa, "
            "hoặc muốn xem trạng thái check-in của ngày hiện tại."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    ToolDefinition(
        name="list_my_attendance_records",
        display_name="Lịch sử chấm công",
        kind=ToolKind.READ,
        description=(
            "Liệt kê lịch sử chấm công của nhân viên hiện tại. "
            "Có thể lọc theo tháng và năm. "
            "Trả về giờ check-in và check-out cho mỗi ngày làm việc. "
            "Sử dụng khi nhân viên hỏi về lịch sử chấm công, "
            "lịch sử check-in, hoặc các ngày đã làm việc."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "month": {
                    "type": "integer",
                    "description": "Tháng (1-12). Nếu bỏ qua, trả về các bản ghi gần đây.",
                    "minimum": 1,
                    "maximum": 12,
                },
                "year": {
                    "type": "integer",
                    "description": "Năm (ví dụ: 2026). Nếu bỏ qua, sử dụng năm hiện tại.",
                    "minimum": 2020,
                    "maximum": 2099,
                },
            },
        },
    ),
    ToolDefinition(
        name="list_my_employee_requests",
        display_name="Yêu cầu của tôi",
        kind=ToolKind.READ,
        description=(
            "Liệt kê các yêu cầu (đơn nghỉ phép và tăng ca) của chính nhân viên hiện tại. "
            "Có thể lọc theo loại yêu cầu (leave hoặc overtime). "
            "Trả về trạng thái, ngày tháng, lý do, và thời gian gửi. "
            "Sử dụng khi nhân viên hỏi về đơn nghỉ phép hoặc tăng ca của họ, "
            "lịch sử gửi đơn, hoặc trạng thái yêu cầu."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "request_type": {
                    "type": "string",
                    "description": (
                        "Lọc theo loại yêu cầu (không bắt buộc). "
                        "Một trong: leave, overtime. Nếu bỏ qua, trả về tất cả."
                    ),
                    "enum": ["leave", "overtime"],
                },
            },
        },
    ),
    ToolDefinition(
        name="get_my_leave_balance",
        display_name="Số dư nghỉ phép",
        kind=ToolKind.READ,
        description=(
            "Xem số dư ngày nghỉ phép năm của nhân viên hiện tại. "
            "Trả về tổng số ngày phép được cấp, số ngày đã được duyệt, số ngày đang chờ xử lý, "
            "và số ngày còn lại. Đây chỉ là số dư của nhân viên hiện tại."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    ToolDefinition(
        name="list_my_payslips",
        display_name="Bảng lương của tôi",
        kind=ToolKind.READ,
        description=(
            "Liệt kê các bảng lương đã được phát hành của nhân viên hiện tại. "
            "Trả về kỳ lương, lương gross, lương net, và chi tiết "
            "cho từng bảng lương. "
            "Sử dụng khi nhân viên hỏi về bảng lương, lịch sử lương, "
            "hoặc các bản ghi thanh toán."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    ),
    ToolDefinition(
        name="draft_leave_request",
        display_name="Soạn đơn nghỉ phép",
        kind=ToolKind.DRAFT,
        description=(
            "Soạn thảo đơn nghỉ phép cho nhân viên hiện tại. Trả về một Draft Action "
            "có bản xem trước để nhân viên xem xét và xác nhận. "
            "Nhân viên xác nhận trên UI; chỉ sau đó đơn nghỉ phép mới được gửi đi. "
            "Sử dụng khi nhân viên muốn xin nghỉ phép, đăng ký nghỉ phép, hoặc gửi "
            "đơn nghỉ phép."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "leave_type": {
                    "type": "string",
                    "description": (
                        "Loại nghỉ phép. Một trong: annual (nghỉ phép năm), "
                        "sick (nghỉ ốm), unpaid (nghỉ không lương), "
                        "other (khác)."
                    ),
                    "enum": ["annual", "sick", "unpaid", "other"],
                },
                "start_date": {
                    "type": "string",
                    "description": "Ngày bắt đầu nghỉ. Định dạng: YYYY-MM-DD.",
                    "pattern": _DATE_PATTERN,
                },
                "end_date": {
                    "type": "string",
                    "description": "Ngày kết thúc nghỉ (>= start_date, YYYY-MM-DD).",
                    "pattern": _DATE_PATTERN,
                },
                "reason": {
                    "type": "string",
                    "description": "Lý do xin nghỉ phép (tối đa 2000 ký tự).",
                    "maxLength": _REASON_MAX_LENGTH,
                },
            },
            "required": ["leave_type", "start_date", "end_date", "reason"],
        },
    ),
    ToolDefinition(
        name="draft_overtime_request",
        display_name="Soạn đơn tăng ca",
        kind=ToolKind.DRAFT,
        description=(
            "Soạn thảo đơn tăng ca cho nhân viên hiện tại. Trả về một Draft Action "
            "có bản xem trước để nhân viên xem xét và xác nhận. "
            "Nhân viên xác nhận trên UI; chỉ sau đó đơn tăng ca mới được gửi đi. "
            "Sử dụng khi nhân viên muốn đăng ký tăng ca, gửi đơn tăng ca, "
            "hoặc khai báo giờ làm thêm."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "work_date": {
                    "type": "string",
                    "description": "Ngày làm tăng ca. Định dạng: YYYY-MM-DD.",
                    "pattern": _DATE_PATTERN,
                },
                "start_time": {
                    "type": "string",
                    "description": "Giờ bắt đầu. Định dạng: HH:MM (24-hour).",
                    "pattern": _TIME_PATTERN,
                },
                "end_time": {
                    "type": "string",
                    "description": "Giờ kết thúc, sau start_time, định dạng HH:MM (24h).",
                    "pattern": _TIME_PATTERN,
                },
                "reason": {
                    "type": "string",
                    "description": "Lý do tăng ca (tối đa 2000 ký tự).",
                    "maxLength": _REASON_MAX_LENGTH,
                },
                "project_or_task": {
                    "type": "string",
                    "description": "Tên dự án/công việc (không bắt buộc, <= 255 ký tự).",
                    "maxLength": 255,
                },
            },
            "required": ["work_date", "start_time", "end_time", "reason"],
        },
    ),
]

EMPLOYEE_TOOL_NAMES: set[str] = {t.name for t in EMPLOYEE_TOOL_DEFINITIONS}
