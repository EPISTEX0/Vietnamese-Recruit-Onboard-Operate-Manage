"""Safety tests for HR Assistant tools.

Each tool gets its own test class inheriting BaseToolSafetyTest.
All 8 handler-wired tools in ToolRegistry are covered.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.assistant.application.tool_registry import InterviewLister, ToolRegistry
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
)
from src.modules.recruitment.domain.exceptions import CandidateNotFoundError
from tests.modules.assistant.test_base_safety import BaseToolSafetyTest


def _make_registry(
    candidate_service: MagicMock | None = None,
    onboarding_service: MagicMock | None = None,
    interview_lister: MagicMock | None = None,
) -> ToolRegistry:
    """Build a ToolRegistry with spec='d mocks.

    #382: an unspecced mock auto-generates any attribute a test assigns to
    it, hiding a handler wired to a method the real service does not have.
    """
    return ToolRegistry(
        candidate_service=candidate_service or MagicMock(spec=CandidateLifecycleService),
        onboarding_service=onboarding_service or MagicMock(spec=OnboardingService),
        interview_lister=interview_lister or MagicMock(spec=InterviewLister),
    )


# =========================================================================
# Read-Tools — no entity lookup
# =========================================================================


class TestCountCandidatesByStatusSafety(BaseToolSafetyTest):
    """Safety tests for count_candidates_by_status (Read-Tool)."""

    TOOL_NAME = "count_candidates_by_status"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_count_candidates_by_status"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        return _make_registry()

    @pytest.fixture
    def valid_args(self) -> dict:
        return {"status": "reviewing"}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Tool returns org-wide counts (not scoped to any employee)."""
        mock_result = MagicMock()
        mock_result.total_count = 5

        for status in ["new", "reviewing", "accepted"]:
            mock_result.total_count = 128  # org-wide data
            registry._candidate_service.list_candidates = AsyncMock(return_value=mock_result)

            result = await self.execute_tool(registry, {"status": status})
            assert "error" not in result, f"Unexpected error for status {status}: {result}"

            # Tool queries org-wide — no employee filter
            call_kwargs = registry._candidate_service.list_candidates.call_args.kwargs
            assert "status" in call_kwargs
            assert call_kwargs["page"] >= 1
            assert call_kwargs["page_size"] >= 1

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Tool handles invalid status gracefully (no entity to look up)."""
        result = await self.execute_tool(registry, {"status": "does_not_exist"})
        self.assert_error(result)
        assert "Invalid status" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Tool handles invalid status string."""
        result = await self.execute_tool(registry, {"status": "not_a_real_status!!"})
        self.assert_error(result)
        assert "Invalid status" in result["error"]


class TestListInProgressOnboardingSafety(BaseToolSafetyTest):
    """Safety tests for list_in_progress_onboarding (Read-Tool)."""

    TOOL_NAME = "list_in_progress_onboarding"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_list_in_progress_onboarding"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None
    NO_PARAMS = True

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        return _make_registry()

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        mock_item = MagicMock()
        mock_item.process_id = "p1"
        mock_item.employee_id = "e1"
        mock_item.employee_full_name = "Nguyen Van A"
        mock_item.employee_email = "a@company.com"
        mock_item.employee_code = "NV-001"
        mock_item.completed_count = 3
        mock_item.total_count = 5
        mock_item.status = "in_progress"

        mock_result = MagicMock()
        mock_item2 = MagicMock()
        mock_item2.process_id = "p2"
        mock_item2.employee_id = "e2"
        mock_item2.employee_full_name = "Tran Thi B"
        mock_item2.employee_email = "b@company.com"
        mock_item2.employee_code = "NV-002"
        mock_item2.completed_count = 1
        mock_item2.total_count = 3
        mock_item2.status = "in_progress"

        mock_result.items = [mock_item, mock_item2]  # multiple employees
        mock_result.total = 2
        registry._onboarding_service.list_processes = AsyncMock(return_value=mock_result)

        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["total"] >= 2  # org-wide: sees all employees

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Tool gracefully returns empty list when no onboardings exist."""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        registry._onboarding_service.list_processes = AsyncMock(return_value=mock_result)

        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["total"] == 0
        assert result["processes"] == []

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Tool accepts no params — extra params are ignored."""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        registry._onboarding_service.list_processes = AsyncMock(return_value=mock_result)

        result = await self.execute_tool(registry, {"unknown_param": "should_be_ignored"})
        # Should still work because extra params are ignored
        assert "error" not in result
        assert result["processes"] == []


class TestSearchCandidatesSafety(BaseToolSafetyTest):
    """Safety tests for search_candidates (Read-Tool)."""

    TOOL_NAME = "search_candidates"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_search_candidates"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        return _make_registry()

    @pytest.fixture
    def valid_args(self) -> dict:
        return {"query": "Nguyen"}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Tool searches across ALL candidates (org-wide)."""
        mock_candidate = MagicMock()
        mock_candidate.id = "c1"
        mock_candidate.name = "Nguyen Van A"
        mock_candidate.email = "a@company.com"
        mock_candidate.status = "reviewing"

        mock_result = MagicMock()
        mock_result.candidates = [mock_candidate]
        mock_result.total_count = 1
        registry._candidate_service.list_candidates = AsyncMock(return_value=mock_result)

        result = await self.execute_tool(registry, {"query": "Nguyen"})
        assert "error" not in result
        assert "candidates" in result
        assert result["total"] == 1
        # Verify it calls list_candidates with search, not employee-scoped
        call_kwargs = registry._candidate_service.list_candidates.call_args.kwargs
        assert call_kwargs.get("search") == "Nguyen"

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Tool returns empty list when no candidates match (not crash)."""
        mock_result = MagicMock()
        mock_result.candidates = []
        mock_result.total_count = 0
        registry._candidate_service.list_candidates = AsyncMock(return_value=mock_result)

        result = await self.execute_tool(registry, {"query": "NoMatchXyz"})
        assert "error" not in result
        assert result["candidates"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Empty query returns error (not crash)."""
        result = await self.execute_tool(registry, {"query": ""})
        self.assert_error(result)


# =========================================================================
# Read-Tools — entity lookup required
# =========================================================================


class TestGetCandidateParsedCVSafety(BaseToolSafetyTest):
    """Safety tests for get_candidate_parsed_cv (Read-Tool)."""

    TOOL_NAME = "get_candidate_parsed_cv"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_get_candidate_parsed_cv"
    HAS_ENTITY_LOOKUP = True
    ENTITY_ID_PARAM = "candidate_id"

    _VALID_CANDIDATE_ID = "00000000-0000-0000-0000-000000000001"
    _MISSING_CANDIDATE_ID = "00000000-0000-0000-0000-000000000099"

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        reg = _make_registry()
        reg._candidate_service.get_candidate = AsyncMock()
        return reg

    @pytest.fixture
    def valid_args(self) -> dict:
        return {"candidate_id": self._VALID_CANDIDATE_ID}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Tool reads a single candidate by ID (targeted scope)."""
        mock_candidate = MagicMock()
        mock_candidate.id = self._VALID_CANDIDATE_ID
        mock_candidate.name = "Nguyen Van A"
        mock_candidate.email = "a@example.com"
        mock_candidate.phone = ""
        mock_candidate.skills = []
        mock_candidate.experience = []
        mock_candidate.education = []
        mock_candidate.summary = ""
        mock_candidate.parsed_cv_json = None
        mock_candidate.confidence_score = None
        mock_candidate.status = "new"

        mock_detail = MagicMock()
        mock_detail.candidate = mock_candidate
        registry._candidate_service.get_candidate = AsyncMock(return_value=mock_detail)

        result = await self.execute_tool(registry, {"candidate_id": self._VALID_CANDIDATE_ID})
        assert "error" not in result
        assert str(result["candidate_id"]) == self._VALID_CANDIDATE_ID

        # Must have called get_candidate with the exact ID
        registry._candidate_service.get_candidate.assert_called_once()
        call_id = registry._candidate_service.get_candidate.call_args[0][0]
        assert str(call_id) == self._VALID_CANDIDATE_ID

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Tool returns clear error for nonexistent candidate."""
        registry._candidate_service.get_candidate = AsyncMock(
            side_effect=Exception("Candidate not found")
        )
        result = await self.execute_tool(registry, {"candidate_id": self._MISSING_CANDIDATE_ID})
        self.assert_error(result)

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Invalid UUID returns error (not crash)."""
        result = await self.execute_tool(registry, {"candidate_id": "not-a-uuid"})
        self.assert_error(result)

        # Missing candidate_id
        result = await self.execute_tool(registry, {})
        self.assert_error(result)


class TestListInterviewsForCandidateSafety(BaseToolSafetyTest):
    """Safety tests for list_interviews_for_candidate (Read-Tool)."""

    TOOL_NAME = "list_interviews_for_candidate"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_list_interviews_for_candidate"
    HAS_ENTITY_LOOKUP = True
    ENTITY_ID_PARAM = "candidate_id"

    _VALID_CANDIDATE_ID = "00000000-0000-0000-0000-000000000001"
    _MISSING_CANDIDATE_ID = "00000000-0000-0000-0000-000000000099"

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        reg = _make_registry()
        reg._candidate_service.ensure_candidate_exists = AsyncMock()
        reg._interview_lister.list_interviews_for_candidate = AsyncMock()
        return reg

    @pytest.fixture
    def valid_args(self) -> dict:
        return {"candidate_id": self._VALID_CANDIDATE_ID}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Tool lists interviews for the specified candidate only."""
        registry._candidate_service.ensure_candidate_exists = AsyncMock(return_value=None)
        mock_interviews = [
            {
                "id": uuid.uuid4(),
                "candidate_id": uuid.UUID(self._VALID_CANDIDATE_ID),
                "status": "scheduled",
                "round_name": "Technical",
                "start_at": None,
                "end_at": None,
                "timezone": "Asia/Ho_Chi_Minh",
                "calendar_event_id": None,
                "needs_relink": False,
                "participants": [],
            }
        ]
        registry._interview_lister.list_interviews_for_candidate = AsyncMock(
            return_value=mock_interviews
        )

        result = await self.execute_tool(registry, {"candidate_id": self._VALID_CANDIDATE_ID})
        assert "error" not in result
        assert result["total"] == 1
        registry._interview_lister.list_interviews_for_candidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Tool returns clear error for nonexistent candidate (D3, #382)."""
        registry._candidate_service.ensure_candidate_exists = AsyncMock(
            side_effect=CandidateNotFoundError("Candidate not found")
        )
        result = await self.execute_tool(registry, {"candidate_id": self._MISSING_CANDIDATE_ID})
        self.assert_error(result)
        registry._interview_lister.list_interviews_for_candidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Invalid UUID returns error (not crash)."""
        result = await self.execute_tool(registry, {"candidate_id": "bad-uuid-!!"})
        self.assert_error(result)

        # Missing candidate_id
        result = await self.execute_tool(registry, {})
        self.assert_error(result)


class TestGetOnboardingTaskDetailsSafety(BaseToolSafetyTest):
    """Safety tests for get_onboarding_task_details (Read-Tool)."""

    TOOL_NAME = "get_onboarding_task_details"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_get_onboarding_task_details"
    HAS_ENTITY_LOOKUP = True
    ENTITY_ID_PARAM = "onboarding_process_id"

    _VALID_PROCESS_ID = "00000000-0000-0000-0000-000000000001"
    _MISSING_PROCESS_ID = "00000000-0000-0000-0000-000000000099"

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        reg = _make_registry()
        reg._onboarding_service.get_process = AsyncMock()
        return reg

    @pytest.fixture
    def valid_args(self) -> dict:
        return {"onboarding_process_id": self._VALID_PROCESS_ID}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Tool reads tasks for the specified onboarding process only."""
        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.name = "Cung cấp giấy tờ"
        mock_task.status = "pending"
        mock_task.order_index = 0
        mock_task.completed_at = None
        mock_task.completed_by_name = None

        mock_detail = MagicMock()
        mock_detail.process_id = self._VALID_PROCESS_ID
        mock_detail.status = "in_progress"
        mock_detail.completed_count = 0
        mock_detail.total_count = 4
        mock_detail.tasks = [mock_task]

        registry._onboarding_service.get_process = AsyncMock(return_value=mock_detail)

        result = await self.execute_tool(
            registry, {"onboarding_process_id": self._VALID_PROCESS_ID}
        )
        assert "error" not in result
        assert result["process_id"] == self._VALID_PROCESS_ID

        registry._onboarding_service.get_process.assert_called_once()
        call_id = registry._onboarding_service.get_process.call_args[0][0]
        assert str(call_id) == self._VALID_PROCESS_ID

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Tool returns clear error for nonexistent onboarding process."""
        registry._onboarding_service.get_process = AsyncMock(
            side_effect=Exception("Process not found")
        )
        result = await self.execute_tool(
            registry, {"onboarding_process_id": self._MISSING_PROCESS_ID}
        )
        self.assert_error(result)

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Invalid UUID returns error (not crash)."""
        result = await self.execute_tool(registry, {"onboarding_process_id": "bad-uuid"})
        self.assert_error(result)

        # Missing process_id
        result = await self.execute_tool(registry, {})
        self.assert_error(result)


# =========================================================================
# Draft-Tools — human-in-the-loop (CONTEXT.md, entry Draft Action)
# =========================================================================


class _BaseDraftToolSafety(BaseToolSafetyTest):
    """Shared safety base for Draft-Tools."""

    HAS_ENTITY_LOOKUP = True
    ENTITY_ID_PARAM = "candidate_id"

    _VALID_CANDIDATE_ID = "00000000-0000-0000-0000-000000000001"
    _MISSING_CANDIDATE_ID = "00000000-0000-0000-0000-000000000099"

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        reg = _make_registry()
        reg._candidate_service.get_candidate = AsyncMock()
        return reg

    def _setup_mock_candidate(self, registry: ToolRegistry) -> None:
        """Set up a mock candidate that returns successfully."""
        mock_candidate = MagicMock()
        mock_candidate.name = "Nguyen Van A"
        mock_candidate.email = "a@example.com"
        mock_detail = MagicMock()
        mock_detail.candidate = mock_candidate
        registry._candidate_service.get_candidate = AsyncMock(return_value=mock_detail)

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: ToolRegistry) -> None:
        """Draft tool returns clear error for nonexistent candidate."""
        registry._candidate_service.get_candidate = AsyncMock(
            side_effect=Exception("Candidate not found")
        )
        result = await self.execute_tool(
            registry, {self.ENTITY_ID_PARAM: self._MISSING_CANDIDATE_ID}
        )
        self.assert_error(result)

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Invalid UUID returns error (not crash)."""
        result = await self.execute_tool(registry, {self.ENTITY_ID_PARAM: "bad-uuid-format"})
        self.assert_error(result)

        # Missing all params (no entity_id)
        minimal_args = {} if self.NO_PARAMS else {self.ENTITY_ID_PARAM: self._VALID_CANDIDATE_ID}
        if minimal_args:
            result = await self.execute_tool(registry, minimal_args)
            self.assert_error(result)


class TestDraftInterviewInvitationSafety(_BaseDraftToolSafety):
    """Safety tests for draft_interview_invitation (Draft-Tool)."""

    TOOL_NAME = "draft_interview_invitation"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_draft_interview_invitation"

    # Override: this draft tool needs all 4 params
    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Missing required params or bad UUID returns error."""
        # Bad UUID
        result = await self.execute_tool(
            registry,
            {
                "candidate_id": "bad-uuid-format",
                "interview_date": "15/06/2026",
                "interview_time": "09:00 AM",
                "location": "Room 1",
            },
        )
        self.assert_error(result)

        # Missing all params
        result = await self.execute_tool(registry, {})
        self.assert_error(result)

    @pytest.fixture
    def valid_args(self) -> dict:
        return {
            "candidate_id": self._VALID_CANDIDATE_ID,
            "interview_date": "15/06/2026",
            "interview_time": "09:00 AM",
            "location": "Phòng họp 1",
        }

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Draft tool reads candidate data to personalize the email."""
        self._setup_mock_candidate(registry)
        result = await self.execute_tool(
            registry,
            {
                "candidate_id": self._VALID_CANDIDATE_ID,
                "interview_date": "15/06/2026",
                "interview_time": "09:00 AM",
                "location": "Room 1",
            },
        )
        assert "draft_action" in result
        draft = result["draft_action"]
        assert draft["action_type"] == "send_email"
        assert draft["confirm_method"] == "POST"
        # Draft action includes candidate data for personalization
        assert draft["parameters"]["candidate_id"] == self._VALID_CANDIDATE_ID


class TestDraftCongratulationsEmailSafety(_BaseDraftToolSafety):
    """Safety tests for draft_congratulations_email (Draft-Tool)."""

    TOOL_NAME = "draft_congratulations_email"
    HANDLER_CLASS = ToolRegistry
    HANDLER_METHOD = "_draft_congratulations_email"

    @pytest.fixture
    def valid_args(self) -> dict:
        return {
            "candidate_id": self._VALID_CANDIDATE_ID,
            "position": "Backend Developer",
            "start_date": "01/07/2026",
        }

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: ToolRegistry) -> None:
        """Draft tool reads candidate data to personalize the offer email."""
        self._setup_mock_candidate(registry)
        result = await self.execute_tool(
            registry,
            {
                "candidate_id": self._VALID_CANDIDATE_ID,
                "position": "Backend Developer",
                "start_date": "01/07/2026",
            },
        )
        assert "draft_action" in result
        draft = result["draft_action"]
        assert draft["action_type"] == "send_email"
        assert draft["confirm_method"] == "POST"
        assert draft["parameters"]["candidate_id"] == self._VALID_CANDIDATE_ID

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: ToolRegistry) -> None:
        """Missing required params returns error."""
        # Only candidate_id — missing position and start_date
        result = await self.execute_tool(
            registry,
            {"candidate_id": self._VALID_CANDIDATE_ID},
        )
        self.assert_error(result)

        # Bad UUID
        result = await self.execute_tool(
            registry,
            {
                "candidate_id": "not-a-uuid!!",
                "position": "Dev",
                "start_date": "01/07/2026",
            },
        )
        self.assert_error(result)
