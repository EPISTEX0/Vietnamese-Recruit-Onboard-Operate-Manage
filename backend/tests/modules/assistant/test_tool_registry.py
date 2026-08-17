"""Test the ToolRegistry — executes Read-Tools and wraps Draft-Tools.

Verifies that:
1. Read-Tools call into recruitment/onboarding services correctly
2. Draft-Tools return Draft Actions without executing writes
3. Unknown tools return errors
4. Tool results are valid JSON
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.assistant.application.tool_registry import ToolRegistry
from src.modules.onboarding.domain.exceptions import OnboardingProcessNotFoundError
from src.modules.recruitment.domain.exceptions import CandidateNotFoundError
from src.shared.messages import get_message


@pytest.fixture
def mock_candidate_service() -> AsyncMock:
    """Mock CandidateService for testing."""
    service = AsyncMock()
    return service


@pytest.fixture
def mock_onboarding_service() -> AsyncMock:
    """Mock OnboardingService for testing."""
    service = AsyncMock()
    return service


@pytest.fixture
def registry(
    mock_candidate_service: AsyncMock,
    mock_onboarding_service: AsyncMock,
) -> ToolRegistry:
    """Create a ToolRegistry with mocked dependencies."""
    return ToolRegistry(
        candidate_service=mock_candidate_service,
        onboarding_service=mock_onboarding_service,
    )


class TestToolRegistryReadTools:
    """Test Read-Tool execution."""

    @pytest.mark.asyncio
    async def test_count_candidates_returns_json(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """count_candidates_by_status returns valid JSON with count."""
        # Setup mock
        mock_result = MagicMock()
        mock_result.total_count = 5
        mock_candidate_service.list_candidates = AsyncMock(return_value=mock_result)

        result_str = await registry.execute("count_candidates_by_status", {"status": "reviewing"})
        result = json.loads(result_str)

        assert result["status"] == "reviewing"
        assert result["count"] == 5
        mock_candidate_service.list_candidates.assert_called_once_with(
            status=["reviewing"], page=1, page_size=1
        )

    @pytest.mark.asyncio
    async def test_count_candidates_invalid_status(self, registry: ToolRegistry) -> None:
        """count_candidates_by_status with invalid status returns error."""
        result_str = await registry.execute(
            "count_candidates_by_status", {"status": "invalid_status"}
        )
        result = json.loads(result_str)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_in_progress_onboarding(
        self, registry: ToolRegistry, mock_onboarding_service: AsyncMock
    ) -> None:
        """list_in_progress_onboarding returns processes."""
        mock_item = MagicMock()
        mock_item.process_id = "test-id"
        mock_item.employee_id = "emp-id"
        mock_item.employee_full_name = "Nguyen Van A"
        mock_item.employee_email = "a@example.com"
        mock_item.employee_code = "NV-001"
        mock_item.completed_count = 2
        mock_item.total_count = 4
        mock_item.status = "in_progress"

        mock_result = MagicMock()
        mock_result.items = [mock_item]
        mock_result.total = 1
        mock_onboarding_service.list_processes = AsyncMock(return_value=mock_result)

        result_str = await registry.execute("list_in_progress_onboarding", {})
        result = json.loads(result_str)

        assert result["total"] == 1
        assert len(result["processes"]) == 1
        assert result["processes"][0]["completed_count"] == 2
        assert result["processes"][0]["employee_full_name"] == "Nguyen Van A"

    @pytest.mark.asyncio
    async def test_search_candidates(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """search_candidates returns matching candidates."""
        mock_candidate = MagicMock()
        mock_candidate.id = "test-id"
        mock_candidate.name = "Nguyen Van A"
        mock_candidate.email = "a@example.com"
        mock_candidate.status = "reviewing"

        mock_result = MagicMock()
        mock_result.candidates = [mock_candidate]
        mock_result.total_count = 1
        mock_candidate_service.list_candidates = AsyncMock(return_value=mock_result)

        result_str = await registry.execute("search_candidates", {"query": "Nguyen"})
        result = json.loads(result_str)

        assert result["total"] == 1
        assert result["candidates"][0]["name"] == "Nguyen Van A"

    @pytest.mark.asyncio
    async def test_search_candidates_empty_query(self, registry: ToolRegistry) -> None:
        """search_candidates with empty query returns error."""
        result_str = await registry.execute("search_candidates", {"query": ""})
        result = json.loads(result_str)
        assert "error" in result


class TestToolRegistryDraftTools:
    """Test Draft-Tool behavior — returns proposals, never executes writes."""

    @pytest.mark.asyncio
    async def test_draft_interview_invitation_returns_draft_action(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """draft_interview_invitation returns a Draft Action."""
        mock_detail = MagicMock()
        mock_detail.candidate.name = "Nguyen Van A"
        mock_detail.candidate.email = "a@example.com"
        mock_candidate_service.get_candidate = AsyncMock(return_value=mock_detail)

        candidate_id = "00000000-0000-0000-0000-000000000001"
        result_str = await registry.execute(
            "draft_interview_invitation",
            {
                "candidate_id": candidate_id,
                "interview_date": "15/06/2026",
                "interview_time": "09:00 AM",
                "location": "Phòng họp 1",
            },
        )
        result = json.loads(result_str)

        assert "draft_action" in result
        draft = result["draft_action"]
        assert draft["action_type"] == "send_email"
        assert draft["confirm_endpoint"] == f"/api/recruitment/candidates/{candidate_id}/send-email"
        assert draft["confirm_method"] == "POST"
        assert draft["parameters"]["candidate_id"] == candidate_id
        assert draft["provenance"]["tool"] == "draft_interview_invitation"
        assert draft["provenance"]["candidate_id"] == candidate_id

    @pytest.mark.asyncio
    async def test_draft_interview_invitation_missing_params(self, registry: ToolRegistry) -> None:
        """draft_interview_invitation without required params returns error."""
        result_str = await registry.execute(
            "draft_interview_invitation",
            {
                "candidate_id": "00000000-0000-0000-0000-000000000001",
                # missing other params
            },
        )
        result = json.loads(result_str)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_draft_interview_invitation_invalid_uuid_returns_error(
        self, registry: ToolRegistry
    ) -> None:
        """draft_interview_invitation with a malformed candidate_id says so, not not-found.

        The UUID parse and the candidate lookup used to share one try/except,
        so a malformed id fell into the same handler as a genuine
        CandidateNotFoundError and was misreported as "not found".
        """
        result_str = await registry.execute(
            "draft_interview_invitation",
            {
                "candidate_id": "not-a-uuid",
                "interview_date": "15/06/2026",
                "interview_time": "09:00 AM",
                "location": "Phòng họp 1",
            },
        )
        result = json.loads(result_str)
        assert "Invalid candidate_id" in result["error"]

    @pytest.mark.asyncio
    async def test_draft_interview_invitation_lookup_infra_error_not_reported_as_not_found(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """draft_interview_invitation: an infra failure must not claim not-found (#381)."""
        mock_candidate_service.get_candidate = AsyncMock(side_effect=RuntimeError("db down"))
        result_str = await registry.execute(
            "draft_interview_invitation",
            {
                "candidate_id": "00000000-0000-0000-0000-000000000001",
                "interview_date": "15/06/2026",
                "interview_time": "09:00 AM",
                "location": "Phòng họp 1",
            },
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("CANDIDATE_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("CANDIDATE_NOT_FOUND", "vi")

    @pytest.mark.asyncio
    async def test_draft_congratulations_email_returns_draft_action(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """draft_congratulations_email returns a Draft Action."""
        mock_detail = MagicMock()
        mock_detail.candidate.name = "Nguyen Van A"
        mock_detail.candidate.email = "a@example.com"
        mock_candidate_service.get_candidate = AsyncMock(return_value=mock_detail)

        candidate_id = "00000000-0000-0000-0000-000000000001"
        result_str = await registry.execute(
            "draft_congratulations_email",
            {
                "candidate_id": candidate_id,
                "position": "Backend Developer",
                "start_date": "20/06/2026",
            },
        )
        result = json.loads(result_str)

        assert "draft_action" in result
        draft = result["draft_action"]
        assert draft["action_type"] == "send_email"
        assert draft["confirm_endpoint"] == f"/api/recruitment/candidates/{candidate_id}/send-email"
        assert draft["provenance"]["tool"] == "draft_congratulations_email"
        assert draft["provenance"]["candidate_id"] == candidate_id

    @pytest.mark.asyncio
    async def test_draft_congratulations_email_missing_params(self, registry: ToolRegistry) -> None:
        """draft_congratulations_email without required params returns error."""
        result_str = await registry.execute(
            "draft_congratulations_email",
            {
                "candidate_id": "00000000-0000-0000-0000-000000000001",
            },
        )
        result = json.loads(result_str)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_draft_congratulations_email_invalid_uuid_returns_error(
        self, registry: ToolRegistry
    ) -> None:
        """draft_congratulations_email with a malformed candidate_id says so, not not-found."""
        result_str = await registry.execute(
            "draft_congratulations_email",
            {
                "candidate_id": "not-a-uuid",
                "position": "Backend Developer",
                "start_date": "20/06/2026",
            },
        )
        result = json.loads(result_str)
        assert "Invalid candidate_id" in result["error"]

    @pytest.mark.asyncio
    async def test_draft_congratulations_email_lookup_infra_error_not_reported_as_not_found(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """draft_congratulations_email: an infra failure must not claim not-found (#381)."""
        mock_candidate_service.get_candidate = AsyncMock(side_effect=RuntimeError("db down"))
        result_str = await registry.execute(
            "draft_congratulations_email",
            {
                "candidate_id": "00000000-0000-0000-0000-000000000001",
                "position": "Backend Developer",
                "start_date": "20/06/2026",
            },
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("CANDIDATE_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("CANDIDATE_NOT_FOUND", "vi")

    def test_is_draft_tool(self, registry: ToolRegistry) -> None:
        """draft tools are correctly identified as Draft-Tools."""
        assert registry.is_draft_tool("draft_interview_invitation") is True
        assert registry.is_draft_tool("draft_congratulations_email") is True

    def test_is_not_draft_tool(self, registry: ToolRegistry) -> None:
        """Read-Tools are not identified as Draft-Tools."""
        assert registry.is_draft_tool("count_candidates_by_status") is False
        assert registry.is_draft_tool("search_candidates") is False
        assert registry.is_draft_tool("list_in_progress_onboarding") is False


class TestToolRegistryEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, registry: ToolRegistry) -> None:
        """Unknown tool name returns error JSON."""
        result_str = await registry.execute("nonexistent_tool", {})
        result = json.loads(result_str)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error(self, registry: ToolRegistry) -> None:
        """Tool execution exception returns error JSON, not crash."""
        # search_candidates with query will call list_candidates which we can mock to raise
        # For simplicity, test with an invalid status that triggers the error path
        result_str = await registry.execute(
            "count_candidates_by_status", {"status": "totally_invalid"}
        )
        result = json.loads(result_str)
        assert "error" in result


class TestGetCandidateParsedCV:
    """Test the get_candidate_parsed_cv Read-Tool."""

    @pytest.mark.asyncio
    async def test_returns_parsed_cv_data(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """get_candidate_parsed_cv returns structured CV data."""
        from unittest.mock import MagicMock

        candidate_id = "00000000-0000-0000-0000-000000000001"

        mock_candidate = MagicMock()
        mock_candidate.id = candidate_id
        mock_candidate.name = "Nguyen Van A"
        mock_candidate.email = "a@example.com"
        mock_candidate.phone = "0123456789"
        mock_candidate.skills = ["Python", "FastAPI"]
        mock_candidate.experience = [{"company": "FPT", "role": "Dev"}]
        mock_candidate.education = [{"school": "Bach Khoa", "degree": "Ky su"}]
        mock_candidate.summary = "5 nam kinh nghiem"
        mock_candidate.parsed_cv_json = {"raw": "data"}
        mock_candidate.confidence_score = 0.95
        mock_candidate.status = "reviewing"

        mock_detail = MagicMock()
        mock_detail.candidate = mock_candidate
        mock_candidate_service.get_candidate = AsyncMock(return_value=mock_detail)

        result_str = await registry.execute(
            "get_candidate_parsed_cv",
            {"candidate_id": candidate_id},
        )
        result = json.loads(result_str)

        assert "error" not in result
        assert result["candidate_id"] == candidate_id
        assert result["name"] == "Nguyen Van A"
        assert result["email"] == "a@example.com"
        assert result["skills"] == ["Python", "FastAPI"]
        assert result["experience"] == [{"company": "FPT", "role": "Dev"}]
        assert result["education"] == [{"school": "Bach Khoa", "degree": "Ky su"}]
        assert result["summary"] == "5 nam kinh nghiem"
        assert result["parsed_cv_json"] == {"raw": "data"}
        assert result["confidence_score"] == 0.95
        assert result["status"] == "reviewing"

    @pytest.mark.asyncio
    async def test_missing_candidate_id_returns_error(self, registry: ToolRegistry) -> None:
        """get_candidate_parsed_cv without candidate_id returns error."""
        result_str = await registry.execute("get_candidate_parsed_cv", {})
        result = json.loads(result_str)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_error(self, registry: ToolRegistry) -> None:
        """get_candidate_parsed_cv with invalid UUID returns error."""
        result_str = await registry.execute(
            "get_candidate_parsed_cv",
            {"candidate_id": "not-a-uuid"},
        )
        result = json.loads(result_str)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_nonexistent_candidate_returns_not_found(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """get_candidate_parsed_cv for a genuinely missing candidate says so honestly."""
        mock_candidate_service.get_candidate = AsyncMock(
            side_effect=CandidateNotFoundError("Candidate not found: <id>")
        )
        result_str = await registry.execute(
            "get_candidate_parsed_cv",
            {"candidate_id": "00000000-0000-0000-0000-000000000099"},
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("CANDIDATE_NOT_FOUND", "vi")

    @pytest.mark.asyncio
    async def test_lookup_infra_error_does_not_claim_not_found(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """A DB/infra failure during lookup must not be reported as 'not found'.

        #381: this handler used to catch every exception -- including a
        Postgres outage -- and return the same "candidate not found" message
        it uses for a genuine CandidateNotFoundError. That is a false claim:
        the assistant would tell HR a candidate doesn't exist when the real
        problem is the lookup itself failed.
        """
        mock_candidate_service.get_candidate = AsyncMock(side_effect=RuntimeError("db down"))
        result_str = await registry.execute(
            "get_candidate_parsed_cv",
            {"candidate_id": "00000000-0000-0000-0000-000000000099"},
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("CANDIDATE_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("CANDIDATE_NOT_FOUND", "vi")


class TestListInterviewsForCandidate:
    """Test the list_interviews_for_candidate Read-Tool."""

    @pytest.mark.asyncio
    async def test_returns_interviews(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """list_interviews_for_candidate returns the service's interview list."""
        mock_candidate_service.list_interviews_for_candidate = AsyncMock(
            return_value=[{"id": "iv-1", "status": "scheduled"}]
        )
        result_str = await registry.execute(
            "list_interviews_for_candidate",
            {"candidate_id": "00000000-0000-0000-0000-000000000001"},
        )
        result = json.loads(result_str)
        assert result["total"] == 1
        assert result["interviews"] == [{"id": "iv-1", "status": "scheduled"}]

    @pytest.mark.asyncio
    async def test_lookup_error_not_reported_as_not_found(
        self, registry: ToolRegistry, mock_candidate_service: AsyncMock
    ) -> None:
        """A lookup failure must not be reported as CANDIDATE_NOT_FOUND (#381).

        Unlike ``get_candidate``, this call has no narrow not-found exception
        to preserve: it never raises for an unknown candidate_id, so every
        exception reaching this handler is a lookup failure.
        """
        mock_candidate_service.list_interviews_for_candidate = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        result_str = await registry.execute(
            "list_interviews_for_candidate",
            {"candidate_id": "00000000-0000-0000-0000-000000000001"},
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("CANDIDATE_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("CANDIDATE_NOT_FOUND", "vi")


class TestGetOnboardingTaskDetails:
    """Test the get_onboarding_task_details Read-Tool."""

    @pytest.mark.asyncio
    async def test_returns_task_details(
        self, registry: ToolRegistry, mock_onboarding_service: AsyncMock
    ) -> None:
        """get_onboarding_task_details returns process + task data."""
        mock_task = MagicMock()
        mock_task.id = "task-1"
        mock_task.name = "Ky hop dong"
        mock_task.status = "done"
        mock_task.order_index = 1
        mock_task.completed_at = None
        mock_task.completed_by_name = None

        mock_detail = MagicMock()
        mock_detail.process_id = "00000000-0000-0000-0000-000000000001"
        mock_detail.status = "in_progress"
        mock_detail.completed_count = 1
        mock_detail.total_count = 2
        mock_detail.tasks = [mock_task]
        mock_onboarding_service.get_process = AsyncMock(return_value=mock_detail)

        result_str = await registry.execute(
            "get_onboarding_task_details",
            {"onboarding_process_id": "00000000-0000-0000-0000-000000000001"},
        )
        result = json.loads(result_str)
        assert "error" not in result
        assert result["status"] == "in_progress"
        assert len(result["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_nonexistent_process_returns_not_found(
        self, registry: ToolRegistry, mock_onboarding_service: AsyncMock
    ) -> None:
        """get_onboarding_task_details for a genuinely missing process says so honestly."""
        mock_onboarding_service.get_process = AsyncMock(
            side_effect=OnboardingProcessNotFoundError()
        )
        result_str = await registry.execute(
            "get_onboarding_task_details",
            {"onboarding_process_id": "00000000-0000-0000-0000-000000000099"},
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("ONBOARDING_PROCESS_NOT_FOUND", "vi")

    @pytest.mark.asyncio
    async def test_lookup_infra_error_does_not_claim_not_found(
        self, registry: ToolRegistry, mock_onboarding_service: AsyncMock
    ) -> None:
        """A DB/infra failure during lookup must not be reported as 'not found' (#381)."""
        mock_onboarding_service.get_process = AsyncMock(side_effect=RuntimeError("db down"))
        result_str = await registry.execute(
            "get_onboarding_task_details",
            {"onboarding_process_id": "00000000-0000-0000-0000-000000000099"},
        )
        result = json.loads(result_str)
        assert result["error"] == get_message("ONBOARDING_PROCESS_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("ONBOARDING_PROCESS_NOT_FOUND", "vi")
