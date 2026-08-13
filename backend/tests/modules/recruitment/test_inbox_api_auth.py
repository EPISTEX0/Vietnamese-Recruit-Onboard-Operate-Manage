"""API-layer access control tests for the Recruitment Inbox endpoints.

Tests that the FastAPI /api/recruitment/inbox/* endpoints enforce HR-only
access. Uses the established pattern from attendance tests: build a minimal
test app with overridden dependencies and TestClient.

Covers:
- HR/Admin can list, get, correct, and dismiss inbox items.
- Non-HR (Employee) gets 403 on all endpoints.
- Unauthenticated requests get 401.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import UserRole
from src.modules.recruitment.api.inbox_router import get_inbox_repo
from src.modules.recruitment.api.inbox_router import router as inbox_router
from src.modules.recruitment.domain.entities import RecruitmentInboxItem


class FakeAdminUser:
    """Fake HR/Admin user for dependency override."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.role = UserRole.HR
        self.email = "admin@example.com"


class FakeEmployeeUser:
    """Fake non-admin user for dependency override."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.role = UserRole.USER
        self.email = "employee@example.com"


class FakeRecruitmentInboxRepo:
    """Stand-in for RecruitmentInboxItemRepository returning canned data."""

    def __init__(self) -> None:
        self.create_called = 0
        self.get_by_id_called = 0
        self.update_called = 0

    async def create(self, item: object) -> object:
        self.create_called += 1
        return item

    async def get_by_id(self, id: object) -> object:
        self.get_by_id_called += 1
        return RecruitmentInboxItem(
            gmail_message_id="msg_test",
            gmail_thread_id="thread_test",
            sender_email="test@example.com",
            subject="Test Subject",
        )

    async def get_by_gmail_message_id(self, gmail_message_id: str) -> object:
        return None

    async def update(self, item: object) -> object:
        self.update_called += 1
        return item

    async def list_by_status(  # noqa: PLR0913
        self,
        inbox_status: str | None = None,
        dismissed: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[object], int]:
        return [
            RecruitmentInboxItem(
                gmail_message_id="msg_list",
                gmail_thread_id="thread_list",
                sender_email="list@example.com",
            )
        ], 1

    async def find_dismissed_by_gmail_message_id(self, gmail_message_id: str) -> object:
        return None


async def _raise_401() -> None:
    """Simulate unauthenticated user."""
    raise HTTPException(status_code=401, detail="Not authenticated")


def _build_app(user: object, repo: FakeRecruitmentInboxRepo | None = None) -> FastAPI:
    """Build a test FastAPI app with overridden dependencies."""
    app = FastAPI()
    app.include_router(inbox_router)
    register_auth_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user
    if repo is not None:
        app.dependency_overrides[get_inbox_repo] = lambda: repo

    app.dependency_overrides[get_db_session] = lambda: AsyncMock(spec=AsyncSession)
    return app


class TestInboxAccessControl:
    """Access control: HR/Admin can access, Employee gets 403."""

    def test_hr_can_list_inbox(self) -> None:
        """HR/Admin can list inbox items."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeAdminUser(), repo)
        client = TestClient(app)

        resp = client.get("/api/recruitment/inbox")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] == 1

    def test_employee_cannot_list_inbox(self) -> None:
        """Non-admin Employee gets 403."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeEmployeeUser(), repo)
        client = TestClient(app)

        resp = client.get("/api/recruitment/inbox")
        assert resp.status_code == 403

    def test_hr_can_get_inbox_item(self) -> None:
        """HR/Admin can get a single inbox item."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeAdminUser(), repo)
        client = TestClient(app)

        resp = client.get(f"/api/recruitment/inbox/{uuid4()}")
        assert resp.status_code == 200

    def test_employee_cannot_get_inbox_item(self) -> None:
        """Non-admin Employee gets 403 on get."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeEmployeeUser(), repo)
        client = TestClient(app)

        resp = client.get(f"/api/recruitment/inbox/{uuid4()}")
        assert resp.status_code == 403

    def test_hr_can_correct_intent(self) -> None:
        """HR/Admin can correct intent."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeAdminUser(), repo)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/inbox/{uuid4()}/correct-intent",
            json={"corrected_intent": "job_application"},
        )
        assert resp.status_code == 200

    def test_employee_cannot_correct_intent(self) -> None:
        """Non-admin Employee gets 403 on correct-intent."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeEmployeeUser(), repo)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/inbox/{uuid4()}/correct-intent",
            json={"corrected_intent": "job_application"},
        )
        assert resp.status_code == 403

    def test_hr_can_dismiss(self) -> None:
        """HR/Admin can dismiss."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeAdminUser(), repo)
        client = TestClient(app)

        resp = client.post(f"/api/recruitment/inbox/{uuid4()}/dismiss")
        assert resp.status_code == 200

    def test_employee_cannot_dismiss(self) -> None:
        """Non-admin Employee gets 403 on dismiss."""
        repo = FakeRecruitmentInboxRepo()
        app = _build_app(FakeEmployeeUser(), repo)
        client = TestClient(app)

        resp = client.post(f"/api/recruitment/inbox/{uuid4()}/dismiss")
        assert resp.status_code == 403

    def test_unauthenticated_gets_401(self) -> None:
        """No auth dependency override should result in 401."""
        app = FastAPI()
        app.include_router(inbox_router)
        register_auth_error_handlers(app)

        app.dependency_overrides[get_current_user] = _raise_401
        app.dependency_overrides[get_db_session] = lambda: AsyncMock(spec=AsyncSession)

        client = TestClient(app)

        resp = client.get("/api/recruitment/inbox")
        assert resp.status_code == 401

    def test_unauthenticated_correct_intent_gets_401(self) -> None:
        """Unauthenticated gets 401 on correct-intent."""
        app = FastAPI()
        app.include_router(inbox_router)
        register_auth_error_handlers(app)

        app.dependency_overrides[get_current_user] = _raise_401
        app.dependency_overrides[get_db_session] = lambda: AsyncMock(spec=AsyncSession)

        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/inbox/{uuid4()}/correct-intent",
            json={"corrected_intent": "other"},
        )
        assert resp.status_code == 401
