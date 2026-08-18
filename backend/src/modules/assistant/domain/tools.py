"""Định nghĩa các tool cho AI Assistant.

Định nghĩa 10 tool có sẵn cho LLM:
- Read-Tool: count_candidates_by_status
- Read-Tool: list_interviews_for_candidate
- Read-Tool: get_onboarding_task_details
- Read-Tool: list_in_progress_onboarding
- Read-Tool: search_candidates
- Read-Tool: get_candidate_parsed_cv
- Read-Tool: list_job_openings
- Read-Tool: get_department_info
- Draft-Tool: draft_interview_invitation
- Draft-Tool: draft_congratulations_email

LLM KHÔNG BAO GIỜ được cấp tool ghi vào database
(xem `CONTEXT.md` § "Nội bộ AI Assistant", mục **Tool**).
Backend thực thi tool trực tiếp — LLM chỉ định nghĩa tool cần gọi.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolKind(StrEnum):
    """Two kinds of tools, and no others (CONTEXT.md)."""

    READ = "read"
    DRAFT = "draft"


@dataclass(frozen=True)
class ToolDefinition:
    """Một tool mà LLM có thể gọi.

    Attributes:
        name: Tên tool (dạng máy đọc được).
        display_name: Tên hiển thị thân thiện (tiếng Việt).
        kind: Read-Tool hoặc Draft-Tool.
        description: Mô tả cho LLM (ngôn ngữ tự nhiên).
        parameters: JSON Schema cho các tham số của tool.
    """

    name: str
    display_name: str
    kind: ToolKind
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class DraftAction:
    """Structured proposal returned by a Draft-Tool (CONTEXT.md).

    HR reviews it; on confirm, the frontend calls the real write endpoint
    directly — never the LLM. This is the human-in-the-loop mechanism.

    Action attributes (from HR's perspective):
        action_type: Loại action (ví dụ: send_email).
        parameters: Tham số của action.
        preview: Bản xem trước để HR review.
        confirm_endpoint: Endpoint API thực tế sẽ gọi khi xác nhận.
        confirm_method: HTTP method cho endpoint xác nhận.
        confirm_body: Nội dung request body cho endpoint xác nhận.
    """

    action_type: str
    parameters: dict[str, Any]
    preview: str
    provenance: dict[str, Any]
    confirm_endpoint: str
    confirm_method: str
    confirm_body: dict[str, Any]


# ---------------------------------------------------------------------------
# Tool-call error contract — shared between both registries (who set the key)
# and both tool-calling loops plus the streaming loop (who read it), per #400.
# ---------------------------------------------------------------------------

# Set only where a registry generates its own error (unknown tool name, a
# handler raising unexpectedly) -- never by a handler's own {"error": ...}
# business-facing message (e.g. "invalid month"), which is valid tool output,
# not a tool-calling failure. The loops key failure detection off this, not
# off the presence of an "error" field, which handlers use for both cases.
TOOL_ERROR_KEY = "__tool_error__"


def is_registry_error(result_json: str) -> bool:
    """Whether a tool's JSON result string carries the registry's own error sentinel.

    Malformed JSON reads as "no sentinel found", not a parse failure -- this
    runs on every tool result, including from tools whose own downstream
    parsing (draft-action extraction) already has its own logged fallback for
    that shape. Duplicating that failure here would crash the loop instead of
    letting the existing fallback handle it.
    """
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and data.get(TOOL_ERROR_KEY) is True


# ---------------------------------------------------------------------------
# Tool definitions — hardcode per grill decision (System Prompt = static)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="count_candidates_by_status",
        display_name="Đếm ứng viên theo trạng thái",
        kind=ToolKind.READ,
        description=(
            "Đếm số lượng candidate trong pipeline tuyển dụng, có thể lọc "
            "theo trạng thái. Trả về danh sách các đối tượng {status, count}. "
            "Sử dụng khi người dùng hỏi có bao nhiêu candidate, bao nhiêu "
            "candidate đang ở một trạng thái cụ thể, hoặc muốn xem tổng quan pipeline."
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Lọc theo trạng thái (không bắt buộc). Giá trị: new, reviewing, "
                        "interview_scheduled, accepted, rejected, archived. "
                        "Nếu bỏ qua, trả về số lượng cho TẤT CẢ trạng thái."
                    ),
                },
            },
        },
    ),
    ToolDefinition(
        name="list_interviews_for_candidate",
        display_name="Danh sách phỏng vấn",
        kind=ToolKind.READ,
        description=(
            "Liệt kê lịch phỏng vấn của một candidate. Trả về danh sách các buổi phỏng vấn với "
            "thời gian đã lên lịch, trạng thái (scheduled/completed/cancelled), địa điểm, "
            "và ghi chú. "
            "Sử dụng khi người dùng hỏi về lịch phỏng vấn của candidate, các buổi phỏng vấn "
            "sắp tới, hoặc lịch sử phỏng vấn."
        ),
        parameters={
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "UUID của candidate cần xem danh sách phỏng vấn.",
                },
            },
            "required": ["candidate_id"],
        },
    ),
    ToolDefinition(
        name="get_onboarding_task_details",
        display_name="Chi tiết nhiệm vụ onboarding",
        kind=ToolKind.READ,
        description=(
            "Xem chi tiết công việc trong quy trình onboarding. Trả về danh sách tác vụ "
            "với tên, trạng thái (pending/done), ngày đến hạn (nếu có), "
            "is_overdue (boolean), và người được giao (nếu có). "
            "Sử dụng khi người dùng hỏi về tiến độ onboarding, danh sách việc cần làm, "
            "hoặc những gì còn phải hoàn thành cho một quy trình onboarding cụ thể."
        ),
        parameters={
            "type": "object",
            "properties": {
                "onboarding_process_id": {
                    "type": "string",
                    "description": "UUID của quy trình onboarding cần lấy danh sách công việc.",
                },
            },
            "required": ["onboarding_process_id"],
        },
    ),
    ToolDefinition(
        name="list_in_progress_onboarding",
        display_name="Onboarding đang thực hiện",
        kind=ToolKind.READ,
        description=(
            "Liệt kê các quy trình onboarding đang diễn ra. "
            "Trả về danh sách các quy trình với họ tên nhân viên, email, tiến độ "
            "(số công việc đã hoàn thành/tổng số), và trạng thái. "
            "Sử dụng khi người dùng hỏi về tình trạng onboarding, ai đang "
            "được onboard, hoặc tiến độ onboarding."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="search_candidates",
        display_name="Tìm kiếm ứng viên",
        kind=ToolKind.READ,
        description=(
            "Tìm kiếm candidate theo tên hoặc email. Trả về danh sách các candidate "
            "phù hợp với id, tên, email, và trạng thái. "
            "Sử dụng khi người dùng nhắc đến một candidate cụ thể bằng tên "
            "hoặc email, hoặc trước khi soạn thảo email cho candidate."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Từ khóa tìm kiếm — so khớp tên hoặc email candidate.",
                },
            },
            "required": ["query"],
        },
    ),
    ToolDefinition(
        name="get_candidate_parsed_cv",
        display_name="Xem CV đã phân tích",
        kind=ToolKind.READ,
        description=(
            "Lấy dữ liệu CV đã được parse của một candidate. Trả về dữ liệu có cấu trúc "
            "bao gồm kỹ năng, kinh nghiệm, học vấn, tóm tắt, và toàn bộ "
            "CV JSON đã parse từ pipeline AI Automation. "
            "Sử dụng khi người dùng hỏi về nội dung CV, kỹ năng, kinh nghiệm, "
            "hoặc background của candidate — hoặc trước khi soạn email để "
            "cá nhân hóa dựa trên thông tin CV."
        ),
        parameters={
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "UUID của candidate cần đọc CV.",
                },
            },
            "required": ["candidate_id"],
        },
    ),
    ToolDefinition(
        name="draft_interview_invitation",
        display_name="Soạn email mời phỏng vấn",
        kind=ToolKind.DRAFT,
        description=(
            "Soạn thảo email mời phỏng vấn cho một candidate. Trả về một Draft Action "
            "với nội dung email để HR xem xét và xác nhận. "
            "Sử dụng khi người dùng muốn soạn hoặc gửi thư mời phỏng vấn."
        ),
        parameters={
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "UUID của candidate.",
                },
                "interview_date": {
                    "type": "string",
                    "description": "Ngày phỏng vấn (ví dụ: 15/06/2026 hoặc YYYY-MM-DD).",
                },
                "interview_time": {
                    "type": "string",
                    "description": "Giờ phỏng vấn (ví dụ: 09:00 AM).",
                },
                "location": {
                    "type": "string",
                    "description": "Địa điểm hoặc đường link Google Meet cho buổi phỏng vấn.",
                },
            },
            "required": ["candidate_id", "interview_date", "interview_time", "location"],
        },
    ),
    ToolDefinition(
        name="list_job_openings",
        display_name="Danh sách vị trí đang tuyển",
        kind=ToolKind.READ,
        description=(
            "Liệt kê các vị trí đang tuyển dụng trong pipeline, có thể lọc "
            "theo trạng thái. Trả về danh sách các vị trí với id, tiêu đề, phòng ban, "
            "chức vụ, chỉ tiêu tuyển dụng, số lượng đã tuyển, và trạng thái. "
            "Sử dụng khi người dùng hỏi về các vị trí đang tuyển, kế hoạch tuyển dụng, "
            "hoặc tình trạng chỉ tiêu."
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Lọc theo trạng thái (tùy chọn): draft, open, closed, cancelled. "
                        "Mặc định là 'open' nếu bỏ qua."
                    ),
                },
            },
        },
    ),
    ToolDefinition(
        name="get_department_info",
        display_name="Thông tin phòng ban",
        kind=ToolKind.READ,
        description=(
            "Xem thông tin phòng ban. Trả về tên phòng ban, mô tả, "
            "danh sách chức vụ (tên, số lượng nhân viên), và thông tin quản lý. "
            "Nếu bỏ qua department_id, trả về thông tin cho TẤT CẢ phòng ban. "
            "Sử dụng khi người dùng hỏi về cơ cấu phòng ban, "
            "chức vụ, hoặc quản lý."
        ),
        parameters={
            "type": "object",
            "properties": {
                "department_id": {
                    "type": "string",
                    "description": (
                        "UUID của phòng ban (không bắt buộc). Nếu bỏ qua, "
                        "trả về thông tin cho tất cả phòng ban."
                    ),
                },
            },
        },
    ),
    ToolDefinition(
        name="draft_congratulations_email",
        display_name="Soạn email chúc mừng",
        kind=ToolKind.DRAFT,
        description=(
            "Soạn email chúc mừng/thông báo trúng tuyển cho candidate. Trả về Draft Action "
            "với nội dung email để HR xem xét và xác nhận. "
            "Sử dụng khi người dùng muốn gửi thư mời nhận việc/lời chúc mừng đến candidate."
        ),
        parameters={
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "UUID của candidate.",
                },
                "position": {
                    "type": "string",
                    "description": "Chức vụ / vị trí đang được đề nghị.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Ngày bắt đầu làm việc (VD: 15/06/2026 hay YYYY-MM-DD).",
                },
            },
            "required": ["candidate_id", "position", "start_date"],
        },
    ),
]


def get_openai_tools(enabled_names: set[str] | None = None) -> list[dict[str, Any]]:
    """Chuyển đổi TOOL_DEFINITIONS sang định dạng OpenAI function-calling.

    Args:
        enabled_names: Nếu được cung cấp, chỉ bao gồm tool có tên trong tập hợp này.
            Nếu None, tất cả tool đều được bao gồm (tương thích ngược).

    Returns:
        Danh sách các dict tool theo định dạng chat completions API.
    """
    tools = TOOL_DEFINITIONS
    if enabled_names is not None:
        tools = [t for t in TOOL_DEFINITIONS if t.name in enabled_names]

    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]
