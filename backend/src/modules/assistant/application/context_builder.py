"""Context Builder — dynamic context injection for AI Assistant system prompts.

Builds a context block string from live system data that is appended as a second
system message after the static core prompt. This gives the LLM real information
about the Organization, pipeline state, and (for Employee Assistant) the current
employee's data.

Architecture:
- HR Assistant context: real-time data fetched on every request
  (organization name, pipeline summary, open job openings).
- Employee Assistant context: per-session data (employee profile,
  leave balance, pending requests, unread payslips).
- No cache layer — ticket #227 explicitly defers it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from src.modules.employee.application.employee_service import EmployeeService
    from src.modules.employee.infrastructure.department_repository import DepartmentRepository
    from src.modules.employee.infrastructure.position_repository import PositionRepository
    from src.modules.employee_request.application.leave_service import LeaveService
    from src.modules.employee_request.application.overtime_service import OvertimeService
    from src.modules.knowledge_base.application.retrieval_service import RetrievalService
    from src.modules.onboarding.application.onboarding_service import OnboardingService
    from src.modules.payslip.application.payslip_service import PayslipService
    from src.modules.recruitment.application.candidate_lifecycle_service import (
        CandidateLifecycleService,
    )
    from src.modules.recruitment.infrastructure.org_settings_repository import (
        OrganizationSettingsRepository,
    )
    from src.modules.recruitment.infrastructure.repositories import JobOpeningRepository

# CandidateStatus values from recruitment/domain/enums.py
_VALID_STATUSES = {"new", "reviewing", "interview_scheduled", "accepted", "rejected", "archived"}

# Status labels in Vietnamese for the context block
_STATUS_LABELS: dict[str, str] = {
    "new": "mới",
    "reviewing": "đang xem xét",
    "interview_scheduled": "đã lên lịch phỏng vấn",
    "accepted": "đã trúng tuyển",
    "rejected": "đã từ chối",
    "archived": "đã lưu trữ",
}

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Builds dynamic context blocks for AI Assistant system prompts.

    Methods:
        build_hr_context: Build context block for HR Assistant (real-time data).
        build_employee_context: Build context block for Employee Assistant.
    """

    def __init__(
        self,
        candidate_service: CandidateLifecycleService | None = None,
        onboarding_service: OnboardingService | None = None,
        org_settings_repo: OrganizationSettingsRepository | None = None,
        job_opening_repo: JobOpeningRepository | None = None,
        employee_service: EmployeeService | None = None,
        leave_service: LeaveService | None = None,
        payslip_service: PayslipService | None = None,
        overtime_service: OvertimeService | None = None,
        retrieval_service: RetrievalService | None = None,
        department_repo: DepartmentRepository | None = None,
        position_repo: PositionRepository | None = None,
    ) -> None:
        self._candidate_service = candidate_service
        self._onboarding_service = onboarding_service
        self._org_settings_repo = org_settings_repo
        self._job_opening_repo = job_opening_repo
        self._employee_service = employee_service
        self._leave_service = leave_service
        self._payslip_service = payslip_service
        self._overtime_service = overtime_service
        self._retrieval_service = retrieval_service
        self._department_repo = department_repo
        self._position_repo = position_repo

    # ------------------------------------------------------------------
    # HR Assistant context (real-time per request)
    # ------------------------------------------------------------------

    async def build_hr_context(self, user_query: str | None = None) -> str:
        """Build a context block for the HR Assistant.

        Includes:
        - Organization name
        - Pipeline summary (candidate counts by status)
        - Open job openings
        - Onboarding in-progress count
        - Knowledge Base retrieval (HR KB docs, top-3 chunks)

        Args:
            user_query: The user's latest chat message, used for KB retrieval.
                        If None, KB retrieval is skipped.

        Returns:
            A formatted string to append as a system message.
        """
        parts: list[str] = []

        # 1. Organization profile
        org_name = await self._get_org_name()
        if org_name:
            parts.append(f"---\nTổ chức: {org_name}")

        # 2. Pipeline summary
        pipeline_summary = await self._get_pipeline_summary()
        if pipeline_summary:
            parts.append(pipeline_summary)

        # 3. Open job openings
        openings = await self._get_open_job_openings()
        if openings:
            parts.append(openings)

        # 4. Onboarding in-progress
        onboarding_summary = await self._get_onboarding_summary()
        if onboarding_summary:
            parts.append(onboarding_summary)

        # 5. Knowledge Base retrieval (HR KB)
        if user_query:
            kb_block = await self._get_kb_context(user_query, kb_types=["hr", "employee"])
            if kb_block:
                parts.append(kb_block)

        if not parts:
            return ""

        parts.append("---")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Employee Assistant context (per-session)
    # ------------------------------------------------------------------

    async def build_employee_context(self, employee_id: UUID, user_query: str | None = None) -> str:
        """Build a context block for the Employee Assistant.

        Includes:
        - Employee name, department, position
        - Leave balance
        - Pending request count
        - Payslip count
        - Knowledge Base retrieval (Employee KB only — no HR KB access)

        Args:
            employee_id: The authenticated employee's UUID.
            user_query: The user's latest chat message, used for KB retrieval.
                        If None, KB retrieval is skipped.

        Returns:
            A formatted string to append as a system message.
        """
        parts: list[str] = []

        # 1. Employee profile
        profile = await self._get_employee_profile(employee_id)
        if profile:
            parts.append(f"---\n{profile}")

        # 2. Leave balance
        leave_balance = await self._get_leave_balance(employee_id)
        if leave_balance:
            parts.append(leave_balance)

        # 3. Pending requests count
        pending_summary = await self._get_pending_requests(employee_id)
        if pending_summary:
            parts.append(pending_summary)

        # 4. Payslip count
        payslip_summary = await self._get_payslip_summary(employee_id)
        if payslip_summary:
            parts.append(payslip_summary)

        # 5. Knowledge Base retrieval (Employee KB only — security boundary)
        if user_query:
            kb_block = await self._get_kb_context(user_query, kb_types=["employee"])
            if kb_block:
                parts.append(kb_block)

        if not parts:
            return ""

        parts.append("---")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # HR context helpers
    # ------------------------------------------------------------------

    async def _get_org_name(self) -> str:
        """Read the organization name from the settings singleton."""
        if self._org_settings_repo is None:
            return ""

        try:
            return await self._org_settings_repo.get_name()
        except Exception:
            logger.exception("Failed to load organization name for assistant context")
            return ""

    async def _get_pipeline_summary(self) -> str:
        """Count candidates by status for the pipeline summary."""
        if self._candidate_service is None:
            return ""

        try:
            counts: dict[str, int] = {}
            total = 0
            for status in sorted(_VALID_STATUSES):
                result = await self._candidate_service.list_candidates(
                    status=[status], page=1, page_size=1
                )
                counts[status] = result.total_count
                total += result.total_count

            if total == 0:
                return "Pipeline tuyển dụng: chưa có ứng viên nào."

            lines = [f"Pipeline tuyển dụng (tổng {total} ứng viên):"]
            for status in sorted(_VALID_STATUSES):
                label = _STATUS_LABELS.get(status, status)
                count = counts.get(status, 0)
                if count > 0:
                    lines.append(f"  - {label}: {count}")
            return "\n".join(lines)
        except Exception:
            logger.exception("Failed to build pipeline summary for assistant context")
            return ""

    async def _get_open_job_openings(self) -> str:
        """List open job openings."""
        if self._job_opening_repo is None:
            return ""

        try:
            openings, total = await self._job_opening_repo.list_job_openings(
                status=["open"], page=1, page_size=10
            )
            if total == 0:
                return ""

            lines = [f"Vị trí đang tuyển ({total}):"]
            for jo in openings:
                lines.append(f"  - {jo.title}")
            return "\n".join(lines)
        except Exception:
            logger.exception("Failed to load open job openings for assistant context")
            return ""

    async def _get_onboarding_summary(self) -> str:
        """Count onboarding processes in progress."""
        if self._onboarding_service is None:
            return ""

        try:
            result = await self._onboarding_service.list_processes(
                status="in_progress", page=1, page_size=1
            )
            if result.total == 0:
                return ""

            return f"Onboarding đang diễn ra: {result.total} nhân viên."
        except Exception:
            logger.exception("Failed to load onboarding summary for assistant context")
            return ""

    # ------------------------------------------------------------------
    # Employee context helpers
    # ------------------------------------------------------------------

    async def _get_employee_profile(self, employee_id: UUID) -> str:
        """Get the employee's basic profile info."""
        if self._employee_service is None:
            return ""

        try:
            employee = await self._employee_service.get_employee(employee_id)
            lines = [
                f"Nhân viên: {employee.full_name}",
            ]
            if employee.department_id and self._department_repo is not None:
                dept = await self._department_repo.get_by_id(employee.department_id)
                if dept:
                    lines.append(f"Phòng ban: {dept.name}")
            if employee.position_id and self._position_repo is not None:
                pos = await self._position_repo.get_by_id(employee.position_id)
                if pos:
                    lines.append(f"Vị trí: {pos.name}")
            if employee.employee_code:
                lines.append(f"Mã NV: {employee.employee_code}")
            return "\n".join(lines)
        except Exception:
            logger.exception("Failed to load employee profile for assistant context")
            return ""

    async def _get_leave_balance(self, employee_id: UUID) -> str:
        """Get the employee's leave balance."""
        if self._leave_service is None:
            return ""

        try:
            balance = await self._leave_service.get_my_leave_balance(employee_id)
            return (
                f"Ngày phép năm: còn {balance['remaining_days']}/"
                f"{balance['annual_entitlement_days']} ngày "
                f"(đã dùng {balance['approved_days_used']}, "
                f"đang chờ duyệt {balance['pending_days']})"
            )
        except Exception:
            logger.exception("Failed to load leave balance for assistant context")
            return ""

    async def _get_pending_requests(self, employee_id: UUID) -> str:
        """Count pending leave and overtime requests."""
        if self._leave_service is None and self._overtime_service is None:
            return ""

        try:
            pending_leave = 0
            pending_overtime = 0

            if self._leave_service:
                leaves = await self._leave_service.list_my_leaves(employee_id)
                from src.modules.employee_request.domain.entities import (
                    RequestStatus,
                )

                pending_leave = sum(1 for r in leaves if r.status == RequestStatus.SUBMITTED)

            if self._overtime_service:
                overtimes = await self._overtime_service.list_my_overtime(employee_id)
                from src.modules.employee_request.domain.entities import (
                    RequestStatus,
                )

                pending_overtime = sum(1 for r in overtimes if r.status == RequestStatus.SUBMITTED)

            total = pending_leave + pending_overtime
            if total == 0:
                return "Yêu cầu đang chờ duyệt: không có."

            parts = [f"Yêu cầu đang chờ duyệt: {total}"]
            if pending_leave > 0:
                parts.append(f"  - Nghỉ phép: {pending_leave}")
            if pending_overtime > 0:
                parts.append(f"  - Làm thêm giờ: {pending_overtime}")
            return "\n".join(parts)
        except Exception:
            logger.exception("Failed to load pending requests for assistant context")
            return ""

    async def _get_payslip_summary(self, employee_id: UUID) -> str:
        """Count the employee's payslips."""
        if self._payslip_service is None:
            return ""

        try:
            payslips = await self._payslip_service.get_my_payslips(employee_id)
            count = len(payslips)
            if count == 0:
                return ""
            return f"Phiếu lương: {count} phiếu có sẵn."
        except Exception:
            logger.exception("Failed to load payslip summary for assistant context")
            return ""

    # ------------------------------------------------------------------
    # Knowledge Base retrieval helper
    # ------------------------------------------------------------------

    async def _get_kb_context(self, user_query: str, kb_types: list[str]) -> str:
        """Retrieve relevant KB chunks for the given user query and KB types.

        Calls the RetrievalService to embed the query and search pgvector.
        Returns a formatted context block string ready for prompt injection.
        Gracefully degrades to empty string on any error (never blocks chat).

        Args:
            user_query: The user's chat message text to search for.
            kb_types: KB types to filter (e.g. ['hr'] or ['employee']).

        Returns:
            Formatted KB context block, or empty string if no results or error.
        """
        if self._retrieval_service is None:
            return ""

        if not user_query.strip():
            return ""

        try:
            return await self._retrieval_service.retrieve(
                query=user_query,
                kb_types=kb_types,
                top_k=3,
                similarity_threshold=0.5,
            )
        except Exception:
            logger.exception("Failed to retrieve knowledge base context for assistant")
            return ""
