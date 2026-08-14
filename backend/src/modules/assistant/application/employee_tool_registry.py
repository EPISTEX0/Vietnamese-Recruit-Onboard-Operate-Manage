"""Tool registry for the Employee Assistant — scoped to one Employee.

Every Read-Tool is hard-wired to the employee_id provided at construction time.
The LLM never receives employee_id as a parameter; it is injected from the
authenticated session. This is the structural safety that enforces "assistant
only reads the Employee's own data" — see `CONTEXT.md` § "Ngôn ngữ domain",
entry **Employee Assistant**.

Draft-Tools return a Draft Action — they never write to the database.
The employee reviews the draft; on confirm, the frontend calls the real
write endpoint directly (human-in-the-loop — `CONTEXT.md`
§ "Nội bộ AI Assistant", entry **Draft Action**).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.modules.assistant.domain.employee_tools import (
    EMPLOYEE_TOOL_DEFINITIONS,
)
from src.modules.assistant.domain.tools import TOOL_DEFINITIONS, DraftAction, ToolKind

if TYPE_CHECKING:
    from src.modules.attendance.infrastructure.attendance_record_repository import (
        AttendanceRecordRepository,
    )
    from src.modules.employee.application.document_service import DocumentService
    from src.modules.employee.application.employee_service import EmployeeService
    from src.modules.employee_request.application.leave_service import LeaveService
    from src.modules.employee_request.application.overtime_service import OvertimeService
    from src.modules.payslip.application.payslip_service import PayslipService

logger = logging.getLogger(__name__)

# Generic error returned to LLM on tool failure — no PII/DB detail leaked
_TOOL_ERROR_MSG = "Không thể xử lý yêu cầu. Vui lòng thử lại sau."
_HR_TOOL_NAMES = frozenset(tool.name for tool in TOOL_DEFINITIONS)
_REFUSAL_MSG = "Trợ lý Employee chỉ được truy cập dữ liệu và yêu cầu của chính bạn."


class EmployeeToolRegistry:
    """Executes Employee Assistant tools — scoped to a single employee."""

    def __init__(
        self,
        employee_id: UUID,
        employee_service: EmployeeService,
        document_service: DocumentService,
        attendance_repo: AttendanceRecordRepository,
        leave_service: LeaveService,
        overtime_service: OvertimeService,
        payslip_service: PayslipService,
    ) -> None:
        self._employee_id = employee_id
        self._employee_service = employee_service
        self._document_service = document_service
        self._attendance_repo = attendance_repo
        self._leave_service = leave_service
        self._overtime_service = overtime_service
        self._payslip_service = payslip_service

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name, returning JSON-string result."""
        handlers = {
            "get_my_profile": self._get_my_profile,
            "list_my_documents": self._list_my_documents,
            "get_today_attendance": self._get_today_attendance,
            "list_my_attendance_records": self._list_my_attendance_records,
            "list_my_employee_requests": self._list_my_employee_requests,
            "get_my_leave_balance": self._get_my_leave_balance,
            "list_my_payslips": self._list_my_payslips,
            "draft_leave_request": self._draft_leave_request,
            "draft_overtime_request": self._draft_overtime_request,
        }

        if tool_name in _HR_TOOL_NAMES:
            return json.dumps({"error": _REFUSAL_MSG, "code": "scope_denied"}, ensure_ascii=False)

        handler = handlers.get(tool_name)
        if handler is None:
            return json.dumps({"error": _TOOL_ERROR_MSG})

        try:
            result = await handler(arguments)
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            # Input validation errors — safe to return message, no PII
            logger.warning("Tool input validation failed: %s", exc)
            return json.dumps({"error": str(exc)})
        except Exception:
            # Unknown error — log full traceback, return generic message
            logger.exception("Employee tool execution failed: %s", tool_name)
            return json.dumps({"error": _TOOL_ERROR_MSG})

    @staticmethod
    def is_draft_tool(tool_name: str) -> bool:
        for t in EMPLOYEE_TOOL_DEFINITIONS:
            if t.name == tool_name and t.kind == ToolKind.DRAFT:
                return True
        return False

    # -----------------------------------------------------------------------
    # Read-Tools
    # -----------------------------------------------------------------------

    async def _get_my_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        # Strip employee_id if LLM sneaks it in — always use auth session
        args.pop("employee_id", None)
        from src.modules.employee.domain.entities import Employee

        employee: Employee = await self._employee_service.get_employee(
            self._employee_id,
        )
        return {
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "email": employee.email,
            "phone": employee.phone or "",
            "date_of_birth": (str(employee.date_of_birth) if employee.date_of_birth else ""),
            "gender": employee.gender or "",
            "address": employee.address or "",
            "department_id": (str(employee.department_id) if employee.department_id else ""),
            "position_id": (str(employee.position_id) if employee.position_id else ""),
            "start_date": str(employee.start_date) if employee.start_date else "",
            "contract_type": employee.contract_type or "",
        }

    async def _list_my_documents(self, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("employee_id", None)
        documents = await self._document_service.list_documents(self._employee_id)
        return {
            "documents": [
                {
                    "id": str(doc.id),
                    "document_type": doc.document_type,
                    "file_name": doc.file_name,
                    "file_size": doc.file_size,
                    "mime_type": doc.mime_type,
                    "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else "",
                }
                for doc in documents
            ]
        }

    async def _get_today_attendance(self, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("employee_id", None)
        today = date.today()
        records = await self._attendance_repo.get_by_employee_and_date_range(
            self._employee_id,
            today,
            today,
        )
        if not records:
            return {
                "date": str(today),
                "check_in_at": None,
                "check_out_at": None,
                "status": "not_checked_in",
            }
        record = records[0]
        return {
            "date": str(record.work_date),
            "check_in_at": record.check_in_at.strftime("%H:%M") if record.check_in_at else None,
            "check_out_at": record.check_out_at.strftime("%H:%M") if record.check_out_at else None,
            "status": "present" if record.check_in_at else "not_checked_in",
        }

    async def _list_my_attendance_records(self, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("employee_id", None)
        month = args.get("month")
        year = args.get("year")

        today = date.today()
        if year is None:
            year = today.year
        else:
            year = int(year)
        if month is None:
            month = today.month
        else:
            month = int(month)

        if month < 1 or month > 12:
            return {"error": "Tháng không hợp lệ. Vui lòng nhập từ 1 đến 12."}
        if year < 1900 or year > 2100:
            return {"error": "Năm không hợp lệ."}

        # Calculate date range for the month
        from calendar import monthrange

        _, last_day = monthrange(year, month)
        start_date = date(year, month, 1)
        end_date = date(year, month, last_day)

        records = await self._attendance_repo.get_by_employee_and_date_range(
            self._employee_id,
            start_date,
            end_date,
        )

        return {
            "records": [
                {
                    "date": str(r.work_date),
                    "check_in_at": r.check_in_at.strftime("%H:%M") if r.check_in_at else None,
                    "check_out_at": r.check_out_at.strftime("%H:%M") if r.check_out_at else None,
                    "status": "present" if r.check_in_at else "not_checked_in",
                }
                for r in records
            ]
        }

    async def _list_my_employee_requests(self, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("employee_id", None)
        request_type = args.get("request_type")

        valid_types = {"leave", "overtime"}
        if request_type is not None and request_type not in valid_types:
            return {
                "error": (
                    f"Loại yêu cầu không hợp lệ: '{request_type}'. "
                    f"Các loại: {', '.join(sorted(valid_types))}."
                )
            }

        leaves = []
        overtime = []

        if request_type is None or request_type == "leave":
            leaves = await self._leave_service.list_my_leaves(self._employee_id)

        if request_type is None or request_type == "overtime":
            overtime = await self._overtime_service.list_my_overtime(self._employee_id)

        all_requests = []

        for leave in leaves:
            all_requests.append(
                {
                    "id": str(leave.id),
                    "type": "leave",
                    "status": leave.status.value if hasattr(leave, "status") else "pending",
                    "start_date": str(leave.start_date) if leave.start_date else "",
                    "end_date": str(leave.end_date) if leave.end_date else "",
                    "reason": leave.reason or "",
                    "created_at": leave.created_at.isoformat() if leave.created_at else "",
                }
            )

        for ot in overtime:
            all_requests.append(
                {
                    "id": str(ot.id),
                    "type": "overtime",
                    "status": ot.status.value if hasattr(ot, "status") else "pending",
                    "work_date": str(ot.work_date) if ot.work_date else "",
                    "start_time": ot.start_time.strftime("%H:%M") if ot.start_time else "",
                    "end_time": ot.end_time.strftime("%H:%M") if ot.end_time else "",
                    "reason": ot.reason or "",
                    "created_at": ot.created_at.isoformat() if ot.created_at else "",
                }
            )

        # Sort by created_at descending
        all_requests.sort(
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )

        return {"requests": all_requests[:50]}

    async def _get_my_leave_balance(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return an employee-scoped annual leave balance snapshot."""
        args.pop("employee_id", None)
        return await self._leave_service.get_my_leave_balance(self._employee_id)

    async def _list_my_payslips(self, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("employee_id", None)
        payslips = await self._payslip_service.get_my_payslips(self._employee_id)
        payslips = payslips[:50]
        return {
            "payslips": [
                {
                    "id": str(p.id),
                    "period_month": str(p.period_month),
                    "gross_salary": float(p.gross_salary),
                    "deductions": float(p.deductions),
                    "insurance_employee": float(p.insurance_employee),
                    "taxable_income": float(p.taxable_income),
                    "pit_amount": float(p.pit_amount),
                    "net_salary": float(p.net_salary),
                    "currency": p.currency,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                }
                for p in payslips
            ]
        }

    # -----------------------------------------------------------------------
    # Draft-Tools
    # -----------------------------------------------------------------------

    async def _draft_leave_request(self, args: dict[str, Any]) -> dict[str, Any]:
        leave_type = args.get("leave_type")
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        reason = args.get("reason")

        if not leave_type or not start_date or not end_date or not reason:
            return {"error": ("Thiếu thông tin: loại nghỉ, ngày bắt đầu, ngày kết thúc và lý do.")}

        allowed_types = {"annual", "sick", "unpaid", "other"}

        # Cross-field validation: parse dates before comparing
        import datetime as _dt

        try:
            parsed_start = _dt.date.fromisoformat(start_date)
            parsed_end = _dt.date.fromisoformat(end_date)
        except (ValueError, TypeError):
            return {"error": ("Ngày không hợp lệ. Định dạng: YYYY-MM-DD.")}
        if parsed_end < parsed_start:
            return {"error": ("Ngày kết thúc phải sau hoặc bằng ngày bắt đầu.")}

        if leave_type not in allowed_types:
            return {
                "error": (
                    f"Loại nghỉ không hợp lệ: '{leave_type}'. "
                    f"Các loại: {', '.join(sorted(allowed_types))}."
                )
            }

        leave_type_labels = {
            "annual": "Nghỉ phép năm",
            "sick": "Nghỉ ốm",
            "unpaid": "Nghỉ không lương",
            "other": "Khác",
        }
        leave_label = leave_type_labels.get(leave_type, leave_type)
        preview = f"Xin nghỉ {leave_label} từ {start_date} đến {end_date}. Lý do: {reason}"

        draft = DraftAction(
            action_type="submit_leave_request",
            parameters={
                "leave_type": leave_type,
                "start_date": start_date,
                "end_date": end_date,
                "reason": reason,
            },
            preview=preview,
            provenance={
                "tool": "draft_leave_request",
                "scope": "employee_self_service",
                "assistant_type": "employee_assistant",
                "redacted": True,
                "source_fields": [],
            },
            confirm_endpoint="/api/employee-requests/me/leave",
            confirm_method="POST",
            confirm_body={
                "leave_type": leave_type,
                "start_date": start_date,
                "end_date": end_date,
                "reason": reason,
            },
        )
        return {"draft_action": _draft_to_dict(draft)}

    async def _draft_overtime_request(self, args: dict[str, Any]) -> dict[str, Any]:
        work_date = args.get("work_date")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        reason = args.get("reason")
        project_or_task = args.get("project_or_task")

        if not work_date or not start_time or not end_time or not reason:
            return {
                "error": "Thiếu thông tin: ngày làm việc, giờ bắt đầu, giờ kết thúc và lý do.",
            }

        # Cross-field validation: parse times before comparing
        import datetime as _dt

        try:
            parsed_start = _dt.time.fromisoformat(start_time)
            parsed_end = _dt.time.fromisoformat(end_time)
        except (ValueError, TypeError):
            return {"error": ("Giờ không hợp lệ. Định dạng: HH:MM.")}
        if parsed_end <= parsed_start:
            return {
                "error": "Giờ kết thúc phải sau giờ bắt đầu.",
            }

        preview = f"Đăng ký tăng ca ngày {work_date}, {start_time} - {end_time}. Lý do: {reason}"
        if project_or_task:
            preview += f" | Dự án: {project_or_task}"

        body: dict[str, Any] = {
            "work_date": work_date,
            "start_time": start_time,
            "end_time": end_time,
            "reason": reason,
        }
        if project_or_task:
            body["project_or_task"] = project_or_task

        draft = DraftAction(
            action_type="submit_overtime_request",
            parameters={
                "work_date": work_date,
                "start_time": start_time,
                "end_time": end_time,
                "reason": reason,
                "project_or_task": project_or_task,
            },
            preview=preview,
            provenance={
                "tool": "draft_overtime_request",
                "scope": "employee_self_service",
                "assistant_type": "employee_assistant",
                "redacted": True,
                "source_fields": [],
            },
            confirm_endpoint="/api/employee-requests/me/overtime",
            confirm_method="POST",
            confirm_body=body,
        )
        return {"draft_action": _draft_to_dict(draft)}

    # -----------------------------------------------------------------------
    # OpenAI-format tools
    # -----------------------------------------------------------------------

    def get_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in EMPLOYEE_TOOL_DEFINITIONS
        ]


def _draft_to_dict(draft: DraftAction) -> dict[str, Any]:
    return {
        "action_type": draft.action_type,
        "parameters": draft.parameters,
        "preview": draft.preview,
        "provenance": draft.provenance,
        "confirm_endpoint": draft.confirm_endpoint,
        "confirm_method": draft.confirm_method,
        "confirm_body": draft.confirm_body,
    }
