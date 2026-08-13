"""Example tests for the Onboarding API validation, not-found, and happy paths.

These are EXAMPLE tests (not property/Hypothesis tests) that pin down the
API-layer contract of the onboarding router (``/api/onboarding/*``):

* **Validation (422).** A malformed/missing task UUID on
  ``PATCH /tasks/{task_id}`` (R4.6), a malformed ``status`` in the PATCH body
  (R3.5/R4.6), and an undefined ``status`` filter on ``GET /processes`` (R6.5)
  are rejected by FastAPI/Pydantic with a 422 *before* the service is invoked,
  so no state changes and no records are returned.
* **Not found (404).** ``GET /processes/{process_id}`` for an id the service
  cannot find raises ``OnboardingProcessNotFoundError`` → 404 (R6.6), and the
  body carries only the error envelope (no process data).
* **Happy-path routing/serialization.** Each endpoint maps the service
  read-model dataclasses to its response schema correctly (in particular the
  dataclasses' ``process_id`` → response ``id``).

The router depends on three injectables: ``require_hr`` (GET auth),
``get_current_user`` (PATCH actor), and ``get_onboarding_service`` (the service).
The tests build a fresh FastAPI app with the onboarding router + error handlers
and override all three via ``app.dependency_overrides``: a fake admin ``User``
satisfies auth, and a fake service returns canned dataclasses / raises the
relevant domain error so no database is needed. This mirrors the established
router-test pattern (see ``tests/modules/identity/test_admin_endpoints.py``).

Validates: Requirements 4.6, 6.5, 6.6 (plus happy-path routing/serialization for
each endpoint).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.identity.api.admin_router import require_hr
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.api.error_handler import register_onboarding_error_handlers
from src.modules.onboarding.api.router import onboarding_router
from src.modules.onboarding.application.onboarding_service import (
    PaginatedProcesses,
    ProcessDetail,
    ProcessListItem,
    ProcessTaskDetail,
)
from src.modules.onboarding.container import get_onboarding_service
from src.modules.onboarding.domain.entities import OnboardingTask
from src.modules.onboarding.domain.exceptions import OnboardingProcessNotFoundError

# ---------------------------------------------------------------------------
# Canned identifiers shared by the fixtures and assertions
# ---------------------------------------------------------------------------

_PROCESS_ID = uuid4()
_EMPLOYEE_ID = uuid4()
_TASK_ID = uuid4()


# ---------------------------------------------------------------------------
# Fake onboarding service (async methods returning canned dataclasses)
# ---------------------------------------------------------------------------


class FakeOnboardingService:
    """Stand-in for :class:`OnboardingService` returning canned read models.

    Records calls so tests can assert the service is *not* invoked on a 422
    validation failure, and exposes configurable return values / a configurable
    ``get_process`` error so each endpoint can be driven down its happy path or
    its not-found path without a database.
    """

    def __init__(self) -> None:
        self.list_processes_calls: list[tuple[str | None, int, int]] = []
        self.get_process_calls: list[UUID] = []
        self.complete_task_calls: list[tuple[UUID, User, str]] = []

        self.list_result = PaginatedProcesses(
            items=[
                ProcessListItem(
                    process_id=_PROCESS_ID,
                    status="in_progress",
                    employee_id=_EMPLOYEE_ID,
                    employee_full_name="HR Candidate",
                    employee_email="candidate@example.com",
                    employee_code="NV-001",
                    completed_count=1,
                    total_count=4,
                    missing_setup_fields=[],
                )
            ],
            total=1,
            page=1,
            page_size=50,
        )
        self.detail_result = ProcessDetail(
            process_id=_PROCESS_ID,
            candidate_id=uuid4(),
            status="in_progress",
            employee_id=_EMPLOYEE_ID,
            completed_count=1,
            total_count=4,
            missing_setup_fields=[],
            tasks=[
                ProcessTaskDetail(
                    id=_TASK_ID,
                    name="Sign Contract",
                    status="done",
                    order_index=0,
                ),
                ProcessTaskDetail(
                    id=uuid4(),
                    name="Submit Documents",
                    status="pending",
                    order_index=1,
                ),
            ],
        )
        self.complete_result = OnboardingTask(
            id=_TASK_ID,
            process_id=_PROCESS_ID,
            task_key="sign_contract",
            name="Sign Contract",
            status="done",
            order_index=0,
        )
        self.get_process_error: Exception | None = None

    async def list_processes(
        self, status: str | None, page: int, page_size: int
    ) -> PaginatedProcesses:
        self.list_processes_calls.append((status, page, page_size))
        return self.list_result

    async def get_process(self, process_id: UUID) -> ProcessDetail:
        self.get_process_calls.append(process_id)
        if self.get_process_error is not None:
            raise self.get_process_error
        return self.detail_result

    async def complete_task(self, task_id: UUID, actor: User, status: str) -> OnboardingTask:
        self.complete_task_calls.append((task_id, actor, status))
        return self.complete_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_admin_user() -> User:
    """Build a fake admin (HR) ``User`` for the auth overrides."""
    suffix = uuid4().hex
    return User(
        email=f"hr-{suffix}@example.com",
        name="HR Admin",
        google_sub=f"sub-{suffix}",
        role=UserRole.HR,
    )


@pytest.fixture
def fake_service() -> FakeOnboardingService:
    """Provide a fresh fake onboarding service per test."""
    return FakeOnboardingService()


@pytest.fixture
def client(fake_service: FakeOnboardingService) -> Iterator[TestClient]:
    """Build a TestClient over the onboarding router with overridden deps.

    Overrides ``require_hr`` and ``get_current_user`` with a fake admin user
    (so auth is satisfied) and ``get_onboarding_service`` with the fake service
    (so no database is touched). Dependency overrides are cleaned up afterwards.
    """
    app = FastAPI()
    app.include_router(onboarding_router)
    register_onboarding_error_handlers(app)

    admin_user = _make_admin_user()
    from unittest.mock import MagicMock

    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    app.dependency_overrides[require_hr] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_onboarding_service] = lambda: fake_service
    app.dependency_overrides[get_db_session] = lambda: mock_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Validation (422) — malformed task UUID and malformed status body (R4.6, R3.5)
# ---------------------------------------------------------------------------


def test_patch_task_malformed_uuid_returns_422(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """A malformed task UUID path param yields 422 and never calls the service.

    Validates: Requirements 4.6
    """
    response = client.patch(
        "/api/onboarding/tasks/not-a-uuid",
        json={"status": "done"},
    )

    assert response.status_code == 422
    # The path failed validation, so the body is FastAPI's error envelope and
    # the service mark-done path was never reached (no state change).
    assert "detail" in response.json()
    assert fake_service.complete_task_calls == []


def test_patch_task_malformed_status_body_returns_422(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """A status value outside ``{pending, done}`` yields 422 with no state change.

    Validates: Requirements 4.6 (and R3.5 at the schema layer)
    """
    response = client.patch(
        f"/api/onboarding/tasks/{_TASK_ID}",
        json={"status": "bogus"},
    )

    assert response.status_code == 422
    assert "detail" in response.json()
    assert fake_service.complete_task_calls == []


# ---------------------------------------------------------------------------
# Validation (422) — undefined status filter, no records (R6.5)
# ---------------------------------------------------------------------------


def test_list_processes_invalid_status_filter_returns_422_no_records(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """An undefined ``status`` filter yields 422 with no records returned.

    The query param is typed as ``OnboardingStatus``; an undefined value is
    rejected by FastAPI/Pydantic before the handler runs, so the service is
    never queried and the body carries no process records.

    Validates: Requirements 6.5
    """
    response = client.get("/api/onboarding/processes", params={"status": "bogus"})

    assert response.status_code == 422
    body = response.json()
    # Error envelope only — no process records in the response.
    assert "detail" in body
    assert "items" not in body
    assert fake_service.list_processes_calls == []


# ---------------------------------------------------------------------------
# Not found (404) — unknown process_id, no body data (R6.6)
# ---------------------------------------------------------------------------


def test_get_process_unknown_id_returns_404_no_process_data(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """An unknown ``process_id`` yields 404 with only the error envelope (R6.6).

    Validates: Requirements 6.6
    """
    fake_service.get_process_error = OnboardingProcessNotFoundError()
    unknown_id = uuid4()

    response = client.get(f"/api/onboarding/processes/{unknown_id}")

    assert response.status_code == 404
    body = response.json()
    # Error envelope carries no process data.
    assert body["error_code"] == "ONBOARDING_PROCESS_NOT_FOUND"
    assert "message" in body
    assert "id" not in body
    assert "employee_id" not in body
    assert "tasks" not in body
    # The service was asked for exactly the requested id.
    assert fake_service.get_process_calls == [unknown_id]


# ---------------------------------------------------------------------------
# Happy-path routing / serialization for each endpoint
# ---------------------------------------------------------------------------


def test_list_processes_happy_path_serializes_pagination(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """GET /processes returns 200 with items mapped and pagination metadata.

    Asserts the read-model ``process_id`` maps to the response ``id`` and that
    ``total`` / ``page`` / ``page_size`` are present.
    """
    response = client.get("/api/onboarding/processes")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["id"] == str(_PROCESS_ID)
    assert item["status"] == "in_progress"
    assert item["employee_id"] == str(_EMPLOYEE_ID)
    assert item["completed_count"] == 1
    assert item["total_count"] == 4

    # The handler forwarded the (no-filter) defaults to the service.
    assert fake_service.list_processes_calls == [(None, 1, 50)]


def test_get_process_happy_path_serializes_detail_with_tasks(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """GET /processes/{id} returns 200 with the checklist serialized.

    Asserts ``process_id`` → ``id`` mapping and each task's name/status/
    order_index.
    """
    response = client.get(f"/api/onboarding/processes/{_PROCESS_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(_PROCESS_ID)
    assert body["status"] == "in_progress"
    assert body["employee_id"] == str(_EMPLOYEE_ID)
    assert body["completed_count"] == 1
    assert body["total_count"] == 4

    assert len(body["tasks"]) == 2
    first_task = body["tasks"][0]
    assert first_task["id"] == str(_TASK_ID)
    assert first_task["name"] == "Sign Contract"
    assert first_task["status"] == "done"
    assert first_task["order_index"] == 0

    second_task = body["tasks"][1]
    assert second_task["name"] == "Submit Documents"
    assert second_task["status"] == "pending"
    assert second_task["order_index"] == 1

    assert fake_service.get_process_calls == [_PROCESS_ID]


def test_patch_task_happy_path_serializes_task_response(
    client: TestClient, fake_service: FakeOnboardingService
) -> None:
    """PATCH /tasks/{id} with {"status":"done"} returns 200 with the task.

    Asserts the service receives the parsed UUID and ``done`` status and that
    the resulting ``OnboardingTask`` serializes to ``OnboardingTaskResponse``.
    """
    response = client.patch(
        f"/api/onboarding/tasks/{_TASK_ID}",
        json={"status": "done"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(_TASK_ID)
    assert body["name"] == "Sign Contract"
    assert body["status"] == "done"
    assert body["order_index"] == 0

    # The service was invoked exactly once with the parsed task id and status.
    assert len(fake_service.complete_task_calls) == 1
    called_task_id, called_actor, called_status = fake_service.complete_task_calls[0]
    assert called_task_id == _TASK_ID
    assert called_status == "done"
    assert called_actor.role == UserRole.HR
