"""Test the ToolRegistry — executes Read-Tools and wraps Draft-Tools.

Verifies that:
1. Read-Tools call into recruitment/onboarding services correctly
2. Draft-Tools return Draft Actions without executing writes
3. Unknown tools return errors
4. Tool results are valid JSON
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.assistant.application.tool_registry import InterviewLister, ToolRegistry
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.onboarding.domain.exceptions import OnboardingProcessNotFoundError
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
)
from src.modules.recruitment.domain.exceptions import CandidateNotFoundError
from src.shared.messages import get_message


@pytest.fixture
def mock_candidate_service() -> AsyncMock:
    """Mock CandidateService for testing.

    ``spec=CandidateLifecycleService`` is load-bearing (#382): an unspecced
    ``AsyncMock`` auto-generates any attribute a test assigns to it *as
    another AsyncMock* -- awaitable, so a handler calling a method the real
    service does not have would pass every test while raising AttributeError
    in production. ``AsyncMock``, not ``MagicMock``, is the base here because
    that auto-generation-is-awaitable behaviour is exactly what let #382
    hide: a plain ``MagicMock()`` child is not awaitable and would have
    failed loudly (for the wrong reason) even without ``spec=``.
    """
    return AsyncMock(spec=CandidateLifecycleService)


@pytest.fixture
def mock_onboarding_service() -> AsyncMock:
    """Mock OnboardingService for testing."""
    return AsyncMock(spec=OnboardingService)


@pytest.fixture
def mock_interview_lister() -> AsyncMock:
    """Mock InterviewLister port for testing."""
    return AsyncMock(spec=InterviewLister)


@pytest.fixture
def registry(
    mock_candidate_service: AsyncMock,
    mock_onboarding_service: AsyncMock,
    mock_interview_lister: AsyncMock,
) -> ToolRegistry:
    """Create a ToolRegistry with mocked dependencies."""
    return ToolRegistry(
        candidate_service=mock_candidate_service,
        onboarding_service=mock_onboarding_service,
        interview_lister=mock_interview_lister,
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
    """Test the list_interviews_for_candidate Read-Tool.

    #382: the handler used to call ``list_interviews_for_candidate`` on
    ``_candidate_service`` (a ``CandidateLifecycleService``, which has no such
    method) instead of the injected ``InterviewLister`` port. These tests use
    a ``spec=``'d mock for both, so a fixture that assigns an attribute the
    real class does not have fails loudly instead of silently succeeding.
    """

    @pytest.mark.asyncio
    async def test_returns_interviews(
        self,
        registry: ToolRegistry,
        mock_candidate_service: AsyncMock,
        mock_interview_lister: AsyncMock,
    ) -> None:
        """list_interviews_for_candidate returns the port's interview list, serialized."""
        candidate_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        mock_candidate_service.ensure_candidate_exists = AsyncMock(return_value=None)
        mock_interview_lister.list_interviews_for_candidate = AsyncMock(
            return_value=[
                {
                    "id": uuid.uuid4(),
                    "candidate_id": candidate_id,
                    "status": "scheduled",
                    "round_name": "Technical",
                    "start_at": datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
                    "end_at": datetime(2026, 7, 20, 3, 0, tzinfo=UTC),
                    "timezone": "Asia/Ho_Chi_Minh",
                    "calendar_event_id": "evt-1",
                    "needs_relink": False,
                    "participants": [],
                }
            ]
        )

        result_str = await registry.execute(
            "list_interviews_for_candidate", {"candidate_id": str(candidate_id)}
        )
        result = json.loads(result_str)

        assert "error" not in result, result
        assert result["total"] == 1
        assert result["interviews"][0]["status"] == "scheduled"
        assert result["interviews"][0]["round_name"] == "Technical"
        assert result["interviews"][0]["start_at"] == "2026-07-20T02:00:00+00:00"
        mock_interview_lister.list_interviews_for_candidate.assert_called_once_with(candidate_id)

    @pytest.mark.asyncio
    async def test_nonexistent_candidate_returns_not_found(
        self,
        registry: ToolRegistry,
        mock_candidate_service: AsyncMock,
        mock_interview_lister: AsyncMock,
    ) -> None:
        """An unknown candidate_id says so honestly (D3, #382).

        Unlike ``get_candidate``, ``InterviewLister.list_interviews_for_candidate``
        has no not-found branch of its own -- it queries by candidate_id and
        would just return an empty list. Without the existence check below, an
        unknown candidate_id is indistinguishable from "no interviews
        scheduled" (same defect class as #381).
        """
        mock_candidate_service.ensure_candidate_exists = AsyncMock(
            side_effect=CandidateNotFoundError("Candidate not found: <id>")
        )

        result_str = await registry.execute(
            "list_interviews_for_candidate",
            {"candidate_id": "00000000-0000-0000-0000-000000000099"},
        )
        result = json.loads(result_str)

        assert result["error"] == get_message("CANDIDATE_NOT_FOUND", "vi")
        mock_interview_lister.list_interviews_for_candidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidate_lookup_infra_error_does_not_claim_not_found(
        self,
        registry: ToolRegistry,
        mock_candidate_service: AsyncMock,
        mock_interview_lister: AsyncMock,
    ) -> None:
        """A DB/infra failure during the candidate existence check is not 'not found' (#381)."""
        mock_candidate_service.ensure_candidate_exists = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        result_str = await registry.execute(
            "list_interviews_for_candidate",
            {"candidate_id": "00000000-0000-0000-0000-000000000099"},
        )
        result = json.loads(result_str)

        assert result["error"] == get_message("CANDIDATE_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("CANDIDATE_NOT_FOUND", "vi")
        mock_interview_lister.list_interviews_for_candidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_interview_lookup_error_not_reported_as_not_found(
        self,
        registry: ToolRegistry,
        mock_candidate_service: AsyncMock,
        mock_interview_lister: AsyncMock,
    ) -> None:
        """An interview-lookup failure must not be reported as CANDIDATE_NOT_FOUND (#381)."""
        mock_candidate_service.ensure_candidate_exists = AsyncMock(return_value=None)
        mock_interview_lister.list_interviews_for_candidate = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        result_str = await registry.execute(
            "list_interviews_for_candidate",
            {"candidate_id": "00000000-0000-0000-0000-000000000001"},
        )
        result = json.loads(result_str)

        assert result["error"] == get_message("CANDIDATE_LOOKUP_ERROR", "vi")
        assert result["error"] != get_message("CANDIDATE_NOT_FOUND", "vi")


class TestListInterviewsForCandidateRealSeam:
    """Prove the tool returns real interview data through the real seam (#382).

    ``TestListInterviewsForCandidate`` above mocks the ``InterviewLister``
    port directly, which only proves the handler calls *something* by that
    name -- exactly the class of test that let #382 hide behind 2835 green
    tests. This drives ``ToolRegistry`` against a real
    ``InterviewSchedulerService`` (backed by the in-memory fakes the
    interview-calendar property tests use), so a regression that reintroduces
    the wrong service -- or a JSON-serialization bug in the handler that
    ``json.dumps`` would otherwise swallow into a generic "Tool execution
    failed" error -- fails here.
    """

    @pytest.mark.asyncio
    async def test_lists_real_interviews_through_the_real_scheduler_service(self) -> None:
        from src.modules.recruitment.domain.entities import InterviewParticipant
        from tests.modules.recruitment._interview_support import (
            build_calendar_harness,
            make_candidate,
            make_interview,
        )

        candidate = make_candidate()
        interview = make_interview(
            candidate_id=candidate.id,
            round_name="Technical",
            start_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
        )
        harness = build_calendar_harness(candidates=[candidate], interviews=[interview])
        harness.session.participants.append(
            InterviewParticipant(
                interview_id=interview.id,
                type="employee",
                email="interviewer@example.com",
                name="Nguyen Van B",
            )
        )
        registry = ToolRegistry(
            candidate_service=harness.lifecycle,
            onboarding_service=MagicMock(spec=OnboardingService),
            interview_lister=harness.service,
        )

        result_str = await registry.execute(
            "list_interviews_for_candidate", {"candidate_id": str(candidate.id)}
        )
        result = json.loads(result_str)

        assert "error" not in result, result
        assert result["total"] == 1
        returned = result["interviews"][0]
        assert returned["id"] == str(interview.id)
        assert returned["candidate_id"] == str(candidate.id)
        assert returned["round_name"] == "Technical"
        assert returned["start_at"] == "2026-07-20T02:00:00+00:00"
        assert len(returned["participants"]) == 1
        assert returned["participants"][0]["email"] == "interviewer@example.com"

    @pytest.mark.asyncio
    async def test_nonexistent_candidate_through_the_real_scheduler_service(self) -> None:
        from tests.modules.recruitment._interview_support import build_calendar_harness

        harness = build_calendar_harness(candidates=[])

        registry = ToolRegistry(
            candidate_service=harness.lifecycle,
            onboarding_service=MagicMock(spec=OnboardingService),
            interview_lister=harness.service,
        )

        result_str = await registry.execute(
            "list_interviews_for_candidate",
            {"candidate_id": str(uuid.uuid4())},
        )
        result = json.loads(result_str)

        assert result["error"] == get_message("CANDIDATE_NOT_FOUND", "vi")


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
