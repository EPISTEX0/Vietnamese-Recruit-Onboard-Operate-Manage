"""API-layer tests for employee-owned payslip read endpoints.

Tests the FastAPI router endpoints using mocked dependencies (no database).
Covers:

- Active employee can list own published payslips.
- Active employee can view own published payslip by ID.
- Admin without Employee record → 403.
- Inactive employee → 403.
- Unpublished payslip → 404.
- Someone else's published payslip → 404.
- Non-existent payslip → 404.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-secret-key-32-chars-min-for-hs256")
os.environ.setdefault("AUTH_OAUTH_TOKEN_ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.employee.api.dependencies import get_current_employee
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import User, UserRole
from src.modules.payslip.api.employee_router import employee_payslip_router
from src.modules.payslip.api.error_handler import register_payslip_error_handlers
from src.modules.payslip.application.payslip_service import PayslipService
from src.modules.payslip.container import get_payslip_service
from src.modules.payslip.domain.entities import Payslip, PayslipStatus
from src.modules.payslip.domain.exceptions import PayslipNotFoundError

_EMPLOYEE_ID = uuid4()
_OTHER_EMPLOYEE_ID = uuid4()


class FakeEmployee:
    """Minimal employee stub for dependency override."""

    def __init__(self, employee_id: str | None = None, is_active: bool = True):
        self.id = employee_id or str(_EMPLOYEE_ID)
        self.is_active = is_active
        self.user_id = "user-uuid"
        self.email = "employee@example.com"


class FakeUser:
    """Minimal user stub for dependency override."""

    def __init__(self, role: UserRole = UserRole.USER):
        self.id = uuid4()
        self.role = role
        self.email = "employee@example.com"
        self.name = "Employee"
        self.avatar_url = None
        self.google_sub = "google-sub-123"
        self.created_at = datetime.now(UTC)
        self.last_login = datetime.now(UTC)
        self.is_active = True


def _make_payslip(
    employee_id: str | None = None,
    status: PayslipStatus = PayslipStatus.PUBLISHED,
) -> Payslip:
    """Create a sample Payslip for testing."""
    return Payslip(
        id=uuid4(),
        employee_id=employee_id or str(_EMPLOYEE_ID),
        period_month=date(2026, 5, 1),
        gross_salary=Decimal("15000000"),
        deductions=Decimal("500000"),
        insurance_employee=Decimal("1575000"),
        taxable_income=Decimal("12925000"),
        pit_amount=Decimal("1000000"),
        net_salary=Decimal("11925000"),
        currency="VND",
        status=status,
        published_at=datetime.now(UTC) if status == PayslipStatus.PUBLISHED else None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class FakePayslipService:
    """Fake service returning in-memory payslips."""

    def __init__(self) -> None:
        self.payslips: list[Payslip] = []
        self._index = 0

    async def get_my_payslips(self, employee_id: str) -> list[Payslip]:
        """Return published payslips for the given employee."""
        return [
            p
            for p in self.payslips
            if str(p.employee_id) == str(employee_id) and p.status == PayslipStatus.PUBLISHED
        ]

    async def get_my_payslip_by_id(self, payslip_id: str, employee_id: str) -> Payslip:
        """Return a specific published payslip."""
        for p in self.payslips:
            if str(p.id) == str(payslip_id):
                if str(p.employee_id) != str(employee_id):
                    raise PayslipNotFoundError(str(payslip_id))
                if p.status != PayslipStatus.PUBLISHED:
                    raise PayslipNotFoundError(str(payslip_id))
                return p
        raise PayslipNotFoundError(str(payslip_id))


def _build_app(
    employee: FakeEmployee | None = None,
    user: FakeUser | None = None,
    service: PayslipService | None = None,
) -> FastAPI:
    """Create a test app with optional overrides."""
    app = FastAPI()
    app.include_router(employee_payslip_router)
    register_payslip_error_handlers(app)

    if user is None:
        user = FakeUser()

    async def _override_user() -> User:
        return User(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            google_sub=user.google_sub,
            created_at=user.created_at,
            last_login=user.last_login,
            is_active=user.is_active,
            role=user.role,
        )

    app.dependency_overrides[get_current_user] = _override_user

    async def _override_employee() -> FakeEmployee | None:
        if employee is None:
            return None
        if not employee.is_active:
            raise HTTPException(status_code=403, detail="Employee account is inactive")
        return employee

    app.dependency_overrides[get_current_employee] = _override_employee

    if service is not None:

        async def _override_service() -> PayslipService:
            return service  # type: ignore[return-value]

        app.dependency_overrides[get_payslip_service] = _override_service

    return app


class TestListPayslips:
    """Tests for GET /api/payslips/me."""

    def test_active_employee_can_list_published_payslips(self) -> None:
        """Active employee sees own published payslips."""
        service = FakePayslipService()
        service.payslips = [
            _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.PUBLISHED),
            _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.PUBLISHED),
        ]

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get("/api/payslips/me")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["payslips"]) == 2

    def test_admin_without_employee_record_returns_403(self) -> None:
        """Admin user without Employee record gets 403."""
        app = _build_app(employee=None, user=FakeUser(role=UserRole.HR))
        client = TestClient(app)

        resp = client.get("/api/payslips/me")
        assert resp.status_code == 403

    def test_inactive_employee_returns_403(self) -> None:
        """Inactive employee gets 403."""
        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=False),
        )
        client = TestClient(app)

        resp = client.get("/api/payslips/me")
        assert resp.status_code == 403

    def test_empty_list_when_no_payslips(self) -> None:
        """No payslips returns empty list."""
        service = FakePayslipService()
        service.payslips = []

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get("/api/payslips/me")
        assert resp.status_code == 200
        assert resp.json()["payslips"] == []

    def test_only_published_payslips_returned(self) -> None:
        """Unpublished payslips are excluded from list."""
        service = FakePayslipService()
        service.payslips = [
            _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.PUBLISHED),
            _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.DRAFT),
        ]

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get("/api/payslips/me")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["payslips"]) == 1


class TestGetPayslipById:
    """Tests for GET /api/payslips/me/{payslip_id}."""

    def test_active_employee_can_view_own_published_payslip(self) -> None:
        """Active employee sees own published payslip by ID."""
        payslip = _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.PUBLISHED)
        service = FakePayslipService()
        service.payslips = [payslip]

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get(f"/api/payslips/me/{payslip.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(payslip.id)
        assert data["net_salary"] == "11925000"

    def test_admin_without_employee_returns_403(self) -> None:
        """Admin without Employee record gets 403 on detail view."""
        app = _build_app(employee=None, user=FakeUser(role=UserRole.HR))
        client = TestClient(app)

        resp = client.get(f"/api/payslips/me/{uuid4()}")
        assert resp.status_code == 403

    def test_inactive_employee_returns_403(self) -> None:
        """Inactive employee gets 403 on detail view."""
        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=False),
        )
        client = TestClient(app)

        resp = client.get(f"/api/payslips/me/{uuid4()}")
        assert resp.status_code == 403

    def test_unpublished_payslip_returns_404(self) -> None:
        """Unpublished payslip returns 404."""
        payslip = _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.DRAFT)
        service = FakePayslipService()
        service.payslips = [payslip]

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get(f"/api/payslips/me/{payslip.id}")
        assert resp.status_code == 404

    def test_other_employee_payslip_returns_404(self) -> None:
        """Someone else's payslip returns 404."""
        payslip = _make_payslip(employee_id=str(_OTHER_EMPLOYEE_ID), status=PayslipStatus.PUBLISHED)
        service = FakePayslipService()
        service.payslips = [payslip]

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get(f"/api/payslips/me/{payslip.id}")
        assert resp.status_code == 404

    def test_non_existent_payslip_returns_404(self) -> None:
        """Non-existent payslip returns 404."""
        service = FakePayslipService()
        service.payslips = [
            _make_payslip(employee_id=str(_EMPLOYEE_ID), status=PayslipStatus.PUBLISHED),
        ]

        app = _build_app(
            employee=FakeEmployee(employee_id=str(_EMPLOYEE_ID), is_active=True),
            service=service,
        )
        client = TestClient(app)

        resp = client.get(f"/api/payslips/me/{uuid4()}")
        assert resp.status_code == 404
