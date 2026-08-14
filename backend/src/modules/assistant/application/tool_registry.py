"""Tool registry — executes Read-Tools and wraps Draft-Tools.

Backend executes all tools directly (grill decision: "Backend tự execute
tool, không qua LLM function calling"). The LLM only defines WHAT to call;
the backend runs the actual logic.

Read-Tools call into recruitment/onboarding services. The dependency is one-way:
assistant → other modules' services, never the reverse.

Draft-Tools do NOT execute a write. They return a structured Draft Action (see
`CONTEXT.md` § "Nội bộ AI Assistant", entry **Draft Action**). HR reviews it; on
confirm, the frontend calls the real endpoint.
"""

from __future__ import annotations

import json
import logging
import typing
from typing import TYPE_CHECKING, Any

from src.modules.assistant.domain.tools import DraftAction, ToolKind
from src.shared.messages import get_message

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.modules.employee.application.department_service import DepartmentService
    from src.modules.onboarding.application.onboarding_service import OnboardingService
    from src.modules.recruitment.application.candidate_lifecycle_service import (
        CandidateLifecycleService,
    )

logger = logging.getLogger(__name__)

# CandidateStatus values from recruitment/domain/enums.py
_VALID_STATUSES = {"new", "reviewing", "interview_scheduled", "accepted", "rejected", "archived"}

# JobOpeningStatus values from recruitment/domain/enums.py
_VALID_JOB_OPENING_STATUSES = {"draft", "open", "closed", "cancelled"}


class ToolRegistry:
    """Executes tools and returns results for the LLM.

    Injects CandidateLifecycleService and OnboardingService (read-only usage).
    No write capabilities — structural safety per `CONTEXT.md`
    § "Nội bộ AI Assistant" (entry **Tool**).

    Args:
        candidate_service: Recruitment CandidateLifecycleService for read operations.
        onboarding_service: Onboarding OnboardingService for read operations.
    """

    def __init__(
        self,
        candidate_service: CandidateLifecycleService,
        onboarding_service: OnboardingService,
        session: AsyncSession | None = None,
        department_service: DepartmentService | None = None,
    ) -> None:
        self._candidate_service = candidate_service
        self._onboarding_service = onboarding_service
        self._session = session
        self._department_service = department_service

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name and return the result as JSON string.

        Args:
            tool_name: The tool name from the LLM's tool_call.
            arguments: Parsed arguments from the LLM.

        Returns:
            JSON string result for the LLM to consume.

        Raises:
            ValueError: If the tool name is unknown.
        """
        handlers = {
            "count_candidates_by_status": self._count_candidates_by_status,
            "list_in_progress_onboarding": self._list_in_progress_onboarding,
            "search_candidates": self._search_candidates,
            "get_candidate_parsed_cv": self._get_candidate_parsed_cv,
            "list_job_openings": self._list_job_openings,
            "get_department_info": self._get_department_info,
            "list_interviews_for_candidate": self._list_interviews_for_candidate,
            "get_onboarding_task_details": self._get_onboarding_task_details,
            "draft_interview_invitation": self._draft_interview_invitation,
            "draft_congratulations_email": self._draft_congratulations_email,
        }

        handler = handlers.get(tool_name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            result = await handler(arguments)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logger.exception("Tool execution failed: %s", tool_name)
            return json.dumps({"error": f"Tool execution failed: {exc}"})

    def is_draft_tool(self, tool_name: str) -> bool:
        """Check if a tool is a Draft-Tool (returns Draft Action, not data)."""
        from src.modules.assistant.domain.tools import TOOL_DEFINITIONS

        for t in TOOL_DEFINITIONS:
            if t.name == tool_name and t.kind == ToolKind.DRAFT:
                return True
        return False

    async def _count_candidates_by_status(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: count candidates grouped by status."""
        status_filter = args.get("status")

        if status_filter:
            if status_filter not in _VALID_STATUSES:
                valid = sorted(_VALID_STATUSES)
                return {"error": f"Invalid status: {status_filter}. Valid: {valid}"}
            result = await self._candidate_service.list_candidates(
                status=[status_filter], page=1, page_size=1
            )
            return {"status": status_filter, "count": result.total_count}

        counts = {}
        for s in sorted(_VALID_STATUSES):
            result = await self._candidate_service.list_candidates(status=[s], page=1, page_size=1)
            counts[s] = result.total_count
        return {"counts": counts, "total": sum(counts.values())}

    async def _list_in_progress_onboarding(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: list onboarding processes that are in_progress."""
        result = await self._onboarding_service.list_processes(
            status="in_progress", page=1, page_size=50
        )
        items = []
        for item in result.items:
            items.append(
                {
                    "process_id": str(item.process_id),
                    "employee_id": str(item.employee_id),
                    "employee_full_name": item.employee_full_name,
                    "employee_email": item.employee_email,
                    "employee_code": item.employee_code,
                    "completed_count": item.completed_count,
                    "total_count": item.total_count,
                    "status": item.status,
                }
            )
        return {"processes": items, "total": result.total}

    async def _search_candidates(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: search candidates by name or email."""
        query = args.get("query", "")
        if not query:
            return {"error": "Query is required"}

        result = await self._candidate_service.list_candidates(search=query, page=1, page_size=10)
        candidates = []
        for c in result.candidates:
            candidates.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "email": c.email,
                    "status": c.status,
                }
            )
        return {"candidates": candidates, "total": result.total_count}

    async def _get_candidate_parsed_cv(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: get parsed CV data for a candidate."""
        candidate_id_str = args.get("candidate_id")

        if not candidate_id_str:
            return {"error": "Missing required parameter: candidate_id."}

        import uuid

        try:
            candidate_id = uuid.UUID(candidate_id_str)
        except ValueError as e:
            return {"error": f"Invalid candidate_id: {str(e)}"}

        try:
            detail = await self._candidate_service.get_candidate(candidate_id)
            candidate = detail.candidate
        except Exception:
            return {"error": get_message("CANDIDATE_NOT_FOUND", "vi")}

        return {
            "candidate_id": str(candidate.id),
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
            "skills": candidate.skills or [],
            "experience": candidate.experience or [],
            "education": candidate.education or [],
            "summary": candidate.summary or "",
            "parsed_cv_json": candidate.parsed_cv_json,
            "confidence_score": candidate.confidence_score,
            "status": candidate.status,
        }

    async def _draft_interview_invitation(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Draft-Tool: returns a Draft Action for interview invitation."""
        candidate_id_str = args.get("candidate_id")
        date_str = args.get("interview_date")
        time_str = args.get("interview_time")
        location = args.get("location")

        if not candidate_id_str or not date_str or not time_str or not location:
            return {
                "error": "Missing required parameters: candidate_id, interview_date, "
                "interview_time, location."
            }

        import uuid

        try:
            candidate_id = uuid.UUID(candidate_id_str)
            detail = await self._candidate_service.get_candidate(candidate_id)
            candidate = detail.candidate
        except Exception:
            return {"error": get_message("CANDIDATE_NOT_FOUND", "vi")}

        import html

        safe_name = html.escape(candidate.name)
        safe_date = html.escape(str(date_str))
        safe_time = html.escape(str(time_str))
        safe_location = html.escape(str(location))

        subject = get_message("EMAIL_INTERVIEW_SUBJECT", "vi").format(name=safe_name)
        body_html = f"""
        <div style="font-family: sans-serif; line-height: 1.5;">
            <h3>Thư Mời Phỏng Vấn</h3>
            <p>Thân gửi <strong>{safe_name}</strong>,</p>
            <p>Chúng tôi trân trọng kính mời bạn tham gia phỏng vấn tại Vroom HR.</p>
            <ul>
                <li><strong>Ngày:</strong> {safe_date}</li>
                <li><strong>Thời gian:</strong> {safe_time}</li>
                <li><strong>Địa điểm / Link Meet:</strong> {safe_location}</li>
            </ul>
            <p>Vui lòng xác nhận email này nếu bạn có thể tham gia.</p>
            <p>Trân trọng,<br>Phòng Nhân Sự</p>
        </div>
        """

        draft = DraftAction(
            action_type="send_email",
            parameters={
                "candidate_id": str(candidate_id),
                "subject": subject,
                "body_html": body_html,
            },
            preview=f"Gửi thư mời phỏng vấn đến {candidate.email} lúc {time_str} {date_str}",
            provenance={
                "tool": "draft_interview_invitation",
                "scope": "recruitment",
                "assistant_type": "ai_assistant",
                "redacted": True,
                "candidate_id": str(candidate_id),
                "source_fields": ["name", "email"],
            },
            confirm_endpoint=f"/api/recruitment/candidates/{candidate_id}/send-email",
            confirm_method="POST",
            confirm_body={"subject": subject, "body_html": body_html},
        )

        return {
            "draft_action": {
                "action_type": draft.action_type,
                "parameters": draft.parameters,
                "preview": draft.preview,
                "provenance": draft.provenance,
                "confirm_endpoint": draft.confirm_endpoint,
                "confirm_method": draft.confirm_method,
                "confirm_body": draft.confirm_body,
            }
        }

    async def _draft_congratulations_email(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Draft-Tool: returns a Draft Action for congratulations email."""
        candidate_id_str = args.get("candidate_id")
        position = args.get("position")
        start_date = args.get("start_date")

        if not candidate_id_str or not position or not start_date:
            return {"error": "Missing required parameters: candidate_id, position, start_date."}

        import uuid

        try:
            candidate_id = uuid.UUID(candidate_id_str)
            detail = await self._candidate_service.get_candidate(candidate_id)
            candidate = detail.candidate
        except Exception:
            return {"error": get_message("CANDIDATE_NOT_FOUND", "vi")}

        import html

        safe_name = html.escape(candidate.name)
        safe_position = html.escape(str(position))
        safe_date = html.escape(str(start_date))

        subject = get_message("EMAIL_OFFER_SUBJECT", "vi")
        body_html = f"""
        <div style="font-family: sans-serif; line-height: 1.5;">
            <h3>Thư Báo Trúng Tuyển</h3>
            <p>Thân gửi <strong>{safe_name}</strong>,</p>
            <p>Chúc mừng bạn đã trúng tuyển vào vị trí <strong>{safe_position}</strong>
            tại Vroom HR.</p>
            <p>Ngày bắt đầu làm việc dự kiến: <strong>{safe_date}</strong>.</p>
            <p>Vui lòng phản hồi lại email này để xác nhận nhận việc. Chúng tôi sẽ hướng dẫn thủ tục
            Onboarding tiếp theo.</p>
            <p>Trân trọng,<br>Phòng Nhân Sự</p>
        </div>
        """

        draft = DraftAction(
            action_type="send_email",
            parameters={
                "candidate_id": str(candidate_id),
                "subject": subject,
                "body_html": body_html,
            },
            preview=f"Gửi thư trúng tuyển vị trí {position} đến {candidate.email}",
            provenance={
                "tool": "draft_congratulations_email",
                "scope": "recruitment",
                "assistant_type": "ai_assistant",
                "redacted": True,
                "candidate_id": str(candidate_id),
                "source_fields": ["name", "email"],
            },
            confirm_endpoint=f"/api/recruitment/candidates/{candidate_id}/send-email",
            confirm_method="POST",
            confirm_body={"subject": subject, "body_html": body_html},
        )

        return {
            "draft_action": {
                "action_type": draft.action_type,
                "parameters": draft.parameters,
                "preview": draft.preview,
                "provenance": draft.provenance,
                "confirm_endpoint": draft.confirm_endpoint,
                "confirm_method": draft.confirm_method,
                "confirm_body": draft.confirm_body,
            }
        }

    async def _list_job_openings(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: list job openings, optionally filtered by status."""
        if self._session is None:
            return {"error": "Tool not available: no database session configured."}

        status_filter = args.get("status", "open")

        if status_filter not in _VALID_JOB_OPENING_STATUSES:
            valid = sorted(_VALID_JOB_OPENING_STATUSES)
            return {"error": f"Invalid status: {status_filter}. Valid: {valid}"}

        import uuid

        from src.modules.recruitment.infrastructure.repositories import (
            JobOpeningRepository,
        )

        jo_repo = JobOpeningRepository(self._session)
        job_openings, _total = await jo_repo.list_job_openings(
            status=[status_filter], page=1, page_size=100
        )

        if not job_openings:
            return {"job_openings": [], "total": 0, "status": status_filter}

        # Resolve position -> department names
        from sqlmodel import select as sqlmodel_select

        from src.modules.employee.domain.entities import Department, Position

        position_ids = {jo.position_id for jo in job_openings}
        pos_stmt = sqlmodel_select(Position).where(Position.id.in_(position_ids))
        pos_result = await self._session.execute(pos_stmt)
        positions: dict[uuid.UUID, Position] = {p.id: p for p in pos_result.scalars().all()}

        dept_ids = {p.department_id for p in positions.values() if p.department_id}
        dept_map: dict[uuid.UUID, str] = {}
        if dept_ids:
            dept_stmt = sqlmodel_select(Department).where(Department.id.in_(dept_ids))
            dept_result = await self._session.execute(dept_stmt)
            dept_map = {d.id: d.name for d in dept_result.scalars().all()}

        # Batch get accepted candidate counts
        jo_ids = [jo.id for jo in job_openings]
        accepted_counts: dict[uuid.UUID, int] = {}
        for jo_id in jo_ids:
            accepted_counts[jo_id] = await jo_repo.count_accepted_by_job_opening(jo_id)

        items = []
        for jo in job_openings:
            pos = positions.get(jo.position_id)
            dept_name = dept_map.get(pos.department_id) if pos and pos.department_id else None
            items.append(
                {
                    "id": str(jo.id),
                    "title": jo.title,
                    "department": dept_name,
                    "position": pos.name if pos else None,
                    "headcount_target": jo.target_headcount,
                    "headcount_filled": accepted_counts.get(jo.id, 0),
                    "status": jo.status,
                }
            )
        return {"job_openings": items, "total": len(items), "status": status_filter}

    async def _get_department_info(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: get department info, optionally filtered by department_id."""
        if self._session is None:
            return {"error": "Tool not available: no database session configured."}

        import uuid as _uuid

        from sqlmodel import func
        from sqlmodel import select as sqlmodel_select

        from src.modules.employee.domain.entities import Department, Employee, Position

        department_id_str = args.get("department_id")

        if department_id_str:
            try:
                dept_id = _uuid.UUID(department_id_str)
            except ValueError as e:
                return {"error": f"Invalid department_id: {str(e)}"}

            dept_stmt = sqlmodel_select(Department).where(Department.id == dept_id)
            dept_result = await self._session.execute(dept_stmt)
            dept = dept_result.scalars().first()
            if dept is None:
                return {"error": f"Department not found: {department_id_str}"}
            departments = [dept]
        else:
            dept_stmt = sqlmodel_select(Department).order_by(Department.name)
            dept_result = await self._session.execute(dept_stmt)
            departments = list(dept_result.scalars().all())

        if not departments:
            return {"departments": [], "total": 0}

        # Collect all departments data
        result_items = []
        for dept in departments:
            # Get positions in this department
            pos_stmt = (
                sqlmodel_select(Position)
                .where(Position.department_id == dept.id)
                .order_by(Position.name)
            )
            pos_result = await self._session.execute(pos_stmt)
            positions = list(pos_result.scalars().all())

            # Count employees per position
            position_info = []
            for pos in positions:
                count_stmt = (
                    sqlmodel_select(func.count())
                    .select_from(Employee)
                    .where(
                        Employee.position_id == pos.id,
                    )
                )
                count_result = await self._session.execute(count_stmt)
                emp_count = count_result.scalar_one() or 0
                position_info.append(
                    {
                        "position_title": pos.name,
                        "employee_count": emp_count,
                    }
                )

            # Get manager info: employees in this department
            mgr_stmt = sqlmodel_select(Employee).where(
                Employee.department_id == dept.id,
            )
            mgr_result = await self._session.execute(mgr_stmt)
            dept_employees = list(mgr_result.scalars().all())

            manager_ids = {e.manager_id for e in dept_employees if e.manager_id}
            managers = []
            if manager_ids:
                mgr_lookup_stmt = sqlmodel_select(Employee).where(Employee.id.in_(manager_ids))
                mgr_lookup_result = await self._session.execute(mgr_lookup_stmt)
                mgr_map = {e.id: e for e in mgr_lookup_result.scalars().all()}
                for mgr_id in manager_ids:
                    mgr = mgr_map.get(mgr_id)
                    if mgr:
                        managers.append(
                            {
                                "id": str(mgr.id),
                                "full_name": mgr.full_name,
                                "email": mgr.email,
                                "position_id": str(mgr.position_id) if mgr.position_id else None,
                            }
                        )

            result_items.append(
                {
                    "id": str(dept.id),
                    "name": dept.name,
                    "description": dept.description,
                    "positions": position_info,
                    "managers": managers,
                    "employee_count": len(dept_employees),
                }
            )

        return {"departments": result_items, "total": len(result_items)}

    async def _list_interviews_for_candidate(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: list interviews for a candidate."""
        candidate_id_str = args.get("candidate_id")

        if not candidate_id_str:
            return {"error": "Missing required parameter: candidate_id."}

        import uuid

        try:
            candidate_id = uuid.UUID(candidate_id_str)
        except ValueError as e:
            return {"error": f"Invalid candidate_id: {str(e)}"}

        try:
            interviews = await self._candidate_service.list_interviews_for_candidate(candidate_id)
        except Exception:
            return {"error": get_message("CANDIDATE_NOT_FOUND", "vi")}

        return {"interviews": interviews, "total": len(interviews)}

    async def _get_onboarding_task_details(self, args: dict[str, Any]) -> dict[str, typing.Any]:
        """Read-Tool: get task details for an onboarding process."""
        process_id_str = args.get("onboarding_process_id")

        if not process_id_str:
            return {"error": "Missing required parameter: onboarding_process_id."}

        import uuid

        try:
            process_id = uuid.UUID(process_id_str)
        except ValueError as e:
            return {"error": f"Invalid onboarding_process_id: {str(e)}"}

        try:
            detail = await self._onboarding_service.get_process(process_id)
        except Exception:
            return {"error": get_message("ONBOARDING_PROCESS_NOT_FOUND", "vi")}

        tasks = []
        for task in detail.tasks:
            tasks.append(
                {
                    "id": str(task.id),
                    "name": task.name,
                    "status": task.status,
                    "order_index": task.order_index,
                    "due_date": None,  # not yet modelled on OnboardingTask entity
                    "is_overdue": False,  # no due_date to compare against
                    "assigned_to": None,  # not yet modelled on OnboardingTask entity
                    "completed_at": task.completed_at,
                    "completed_by_name": task.completed_by_name,
                }
            )

        return {
            "process_id": str(detail.process_id),
            "status": detail.status,
            "completed_count": detail.completed_count,
            "total_count": detail.total_count,
            "tasks": tasks,
        }
