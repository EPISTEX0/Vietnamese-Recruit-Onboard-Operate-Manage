"""API-layer tests for HR-admin payslip management endpoints.

Tests the FastAPI admin router endpoints using mocked dependencies (no database).
Covers:

- HR can create draft Payslip for one Employee and period_month.
- Duplicate period_month returns 409.
- HR can update draft Payslip values.
- HR can publish a draft Payslip.
- Employee cannot create/update/publish Payslips (403).
- Published payslip cannot be updated (400).
- Published payslip cannot be deleted (400).
- Draft payslip can be deleted (204).
- List supports filtering by status/employee/period_month.
- Audit log is written for publish/update/delete.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.identity.api.admin_router import require_hr
from src.modules.identity.domain.entities import AuditActionType, AuditLog, User, UserRole
from src.modules.payslip.api.admin_router import admin_payslip_router
from src.modules.payslip.api.error_handler import register_payslip_error_handlers
from src.modules.payslip.application.payslip_hr_service import PayslipHRService
from src.modules.payslip.container import get_payslip_hr_service
from src.modules.payslip.domain.entities import Payslip, PayslipStatus
from src.modules.payslip.domain.exceptions import (
    PayslipAlreadyExistsError,
    PayslipAlreadyPublishedError,
    PayslipNotDraftError,
    PayslipNotFoundError,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_ADMIN_USER = User(
    id=uuid4(),
    email="admin@company.vn",
    name="Admin",
    role=UserRole.HR,
    avatar_url=None,
    google_sub="admin-sub-123",
    created_at=datetime.now(UTC),
    last_login=datetime.now(UTC),
    is_active=True,
)


class FakeAuditService:
    """Fake audit service that records actions in memory."""

    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []

    async def log_action(
        self,
        admin: User,
        action_type: AuditActionType,
        details: dict[str, Any],
    ) -> AuditLog:
        self.logs.append({"admin_id": admin.id, "action_type": action_type, "details": details})
        return AuditLog(
            id=uuid4(),
            admin_user_id=admin.id,
            admin_email=admin.email,
            action_type=action_type,
            details=details,
        )


class FakePayslipHRService:
    """Fake HR service for testing admin payslip operations."""

    def __init__(self) -> None:
        self.payslips: dict[UUID, Payslip] = {}
        self.audit_log: list[dict[str, Any]] = []

    def _add(self, payslip: Payslip) -> None:
        self.payslips[payslip.id] = payslip

    async def create_draft(
        self,
        admin: User,
        employee_id: UUID,
        period_month: date,
        gross_salary: Decimal,
        deductions: Decimal,
        insurance_employee: Decimal,
        taxable_income: Decimal,
        pit_amount: Decimal,
        net_salary: Decimal,
        pdf_url: str | None = None,
    ) -> Payslip:
        # Check uniqueness
        for p in self.payslips.values():
            if p.employee_id == employee_id and p.period_month == period_month:
                raise PayslipAlreadyExistsError(str(employee_id), str(period_month))

        p = Payslip(
            id=uuid4(),
            employee_id=employee_id,
            period_month=period_month,
            gross_salary=gross_salary,
            deductions=deductions,
            insurance_employee=insurance_employee,
            taxable_income=taxable_income,
            pit_amount=pit_amount,
            net_salary=net_salary,
            currency="VND",
            status=PayslipStatus.DRAFT,
            published_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.payslips[p.id] = p
        self.audit_log.append({"action": "create", "payslip_id": str(p.id)})
        return p

    async def update_draft(
        self,
        admin: User,
        payslip_id: UUID,
        *,
        gross_salary: Decimal | None = None,
        deductions: Decimal | None = None,
        insurance_employee: Decimal | None = None,
        taxable_income: Decimal | None = None,
        pit_amount: Decimal | None = None,
        net_salary: Decimal | None = None,
        pdf_url: str | None = None,
    ) -> Payslip:
        p = self.payslips.get(payslip_id)
        if p is None:
            raise PayslipNotFoundError(str(payslip_id))
        if p.status != PayslipStatus.DRAFT:
            raise PayslipNotDraftError(str(payslip_id))

        if gross_salary is not None:
            p.gross_salary = gross_salary
        if deductions is not None:
            p.deductions = deductions
        if insurance_employee is not None:
            p.insurance_employee = insurance_employee
        if taxable_income is not None:
            p.taxable_income = taxable_income
        if pit_amount is not None:
            p.pit_amount = pit_amount
        if net_salary is not None:
            p.net_salary = net_salary

        p.updated_at = datetime.now(UTC)
        self.audit_log.append({"action": "update", "payslip_id": str(payslip_id)})
        return p

    async def publish(self, admin: User, payslip_id: UUID) -> Payslip:
        p = self.payslips.get(payslip_id)
        if p is None:
            raise PayslipNotFoundError(str(payslip_id))
        if p.status == PayslipStatus.PUBLISHED:
            raise PayslipAlreadyPublishedError(str(payslip_id))

        p.status = PayslipStatus.PUBLISHED
        p.published_at = datetime.now(UTC)
        p.updated_at = datetime.now(UTC)
        self.audit_log.append({"action": "publish", "payslip_id": str(payslip_id)})
        return p

    async def delete(self, admin: User, payslip_id: UUID) -> None:
        p = self.payslips.get(payslip_id)
        if p is None:
            raise PayslipNotFoundError(str(payslip_id))
        if p.status != PayslipStatus.DRAFT:
            raise PayslipNotDraftError(str(payslip_id))

        del self.payslips[payslip_id]
        self.audit_log.append({"action": "delete", "payslip_id": str(payslip_id)})

    async def list_payslips(
        self,
        admin: User,
        page: int = 1,
        page_size: int = 20,
        employee_id: UUID | None = None,
        status: PayslipStatus | None = None,
        period_month: date | None = None,
    ) -> tuple[list[Payslip], int]:
        results = list(self.payslips.values())
        if employee_id is not None:
            results = [p for p in results if p.employee_id == employee_id]
        if status is not None:
            results = [p for p in results if p.status == status]
        if period_month is not None:
            results = [p for p in results if p.period_month == period_month]
        results.sort(key=lambda p: p.period_month, reverse=True)
        return results, len(results)

    async def get_payslip_by_id(self, admin: User, payslip_id: UUID) -> Payslip:
        p = self.payslips.get(payslip_id)
        if p is None:
            raise PayslipNotFoundError(str(payslip_id))
        return p


def _make_draft_payslip(
    employee_id: UUID | None = None,
    period_month: date | None = None,
) -> Payslip:
    """Create a sample draft Payslip."""
    return Payslip(
        id=uuid4(),
        employee_id=employee_id or uuid4(),
        period_month=period_month or date(2026, 5, 1),
        gross_salary=Decimal("15000000"),
        deductions=Decimal("500000"),
        insurance_employee=Decimal("1575000"),
        taxable_income=Decimal("12925000"),
        pit_amount=Decimal("1000000"),
        net_salary=Decimal("11925000"),
        currency="VND",
        status=PayslipStatus.DRAFT,
        published_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _build_app(
    admin_auth: bool = True,
    service: PayslipHRService | None = None,
) -> FastAPI:
    """Create a test app with admin auth overrides."""
    app = FastAPI()
    app.include_router(admin_payslip_router)
    register_payslip_error_handlers(app)

    if admin_auth:

        async def _override_admin() -> User:
            return _ADMIN_USER
    else:

        async def _override_admin() -> None:
            raise HTTPException(status_code=403, detail="Not authenticated")

    app.dependency_overrides[require_hr] = _override_admin

    if service is not None:

        async def _override_service() -> PayslipHRService:
            return service  # type: ignore[return-value]

        app.dependency_overrides[get_payslip_hr_service] = _override_service

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreatePayslip:
    """Tests for POST /api/hr/payslips."""

    def test_hr_can_create_draft_payslip(self) -> None:
        """HR can create a draft payslip."""
        employee_id = uuid4()

        service = FakePayslipHRService()

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.post(
            "/api/hr/payslips",
            json={
                "employee_id": str(employee_id),
                "period_month": "2026-06-01",
                "gross_salary": "20000000",
                "deductions": "1000000",
                "insurance_employee": "2100000",
                "taxable_income": "16900000",
                "pit_amount": "1500000",
                "net_salary": "15400000",
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "draft"
        assert data["gross_salary"] == "20000000"
        assert data["net_salary"] == "15400000"
        assert data["period_month"] == "2026-06-01"

    def test_duplicate_period_month_returns_409(self) -> None:
        """Duplicate employee+period_month returns 409."""
        employee_id = uuid4()
        period_month = date(2026, 6, 1)

        service = FakePayslipHRService()
        service._add(_make_draft_payslip(employee_id=employee_id, period_month=period_month))

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.post(
            "/api/hr/payslips",
            json={
                "employee_id": str(employee_id),
                "period_month": "2026-06-01",
                "gross_salary": "20000000",
                "deductions": "1000000",
                "insurance_employee": "2100000",
                "taxable_income": "16900000",
                "pit_amount": "1500000",
                "net_salary": "15400000",
            },
        )

        assert resp.status_code == 409

    def test_employee_cannot_create_payslip(self) -> None:
        """Non-admin user gets 403."""
        app = _build_app(admin_auth=False)
        client = TestClient(app)

        resp = client.post(
            "/api/hr/payslips",
            json={
                "employee_id": str(uuid4()),
                "period_month": "2026-06-01",
                "gross_salary": "20000000",
                "deductions": "1000000",
                "insurance_employee": "2100000",
                "taxable_income": "16900000",
                "pit_amount": "1500000",
                "net_salary": "15400000",
            },
        )

        assert resp.status_code == 403


class TestUpdatePayslip:
    """Tests for PATCH /api/hr/payslips/{id}."""

    def test_hr_can_update_draft_payslip(self) -> None:
        """HR can update draft payslip values."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.patch(
            f"/api/hr/payslips/{payslip.id}",
            json={"gross_salary": "18000000", "net_salary": "13400000"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["gross_salary"] == "18000000"
        assert data["net_salary"] == "13400000"

    def test_cannot_update_published_payslip(self) -> None:
        """Published payslip returns 400."""
        payslip = _make_draft_payslip()
        payslip.status = PayslipStatus.PUBLISHED
        payslip.published_at = datetime.now(UTC)
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.patch(
            f"/api/hr/payslips/{payslip.id}",
            json={"gross_salary": "18000000"},
        )

        assert resp.status_code == 400

    def test_employee_cannot_update_payslip(self) -> None:
        """Non-admin gets 403."""
        app = _build_app(admin_auth=False)
        client = TestClient(app)

        resp = client.patch(
            f"/api/hr/payslips/{uuid4()}",
            json={"gross_salary": "18000000"},
        )

        assert resp.status_code == 403

    def test_partial_update_only_provided_fields(self) -> None:
        """Partial update changes only provided fields."""
        payslip = _make_draft_payslip()
        original_net = payslip.net_salary
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.patch(
            f"/api/hr/payslips/{payslip.id}",
            json={"gross_salary": "18000000"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["gross_salary"] == "18000000"
        assert data["net_salary"] == str(original_net)


class TestPublishPayslip:
    """Tests for POST /api/hr/payslips/{id}/publish."""

    def test_hr_can_publish_draft_payslip(self) -> None:
        """HR can publish a draft payslip."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.post(f"/api/hr/payslips/{payslip.id}/publish")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "published"
        assert data["published_at"] is not None

    def test_cannot_publish_already_published(self) -> None:
        """Already published payslip returns 400."""
        payslip = _make_draft_payslip()
        payslip.status = PayslipStatus.PUBLISHED
        payslip.published_at = datetime.now(UTC)
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.post(f"/api/hr/payslips/{payslip.id}/publish")
        assert resp.status_code == 400

    def test_employee_cannot_publish_payslip(self) -> None:
        """Non-admin gets 403."""
        app = _build_app(admin_auth=False)
        client = TestClient(app)

        resp = client.post(f"/api/hr/payslips/{uuid4()}/publish")
        assert resp.status_code == 403

    def test_publish_non_existent_returns_404(self) -> None:
        """Publishing non-existent payslip returns 404."""
        service = FakePayslipHRService()

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.post(f"/api/hr/payslips/{uuid4()}/publish")
        assert resp.status_code == 404


class TestDeletePayslip:
    """Tests for DELETE /api/hr/payslips/{id}."""

    def test_hr_can_delete_draft_payslip(self) -> None:
        """HR can delete a draft payslip."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.delete(f"/api/hr/payslips/{payslip.id}")
        assert resp.status_code == 204

    def test_cannot_delete_published_payslip(self) -> None:
        """Published payslip returns 400 on delete."""
        payslip = _make_draft_payslip()
        payslip.status = PayslipStatus.PUBLISHED
        payslip.published_at = datetime.now(UTC)
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.delete(f"/api/hr/payslips/{payslip.id}")
        assert resp.status_code == 400

    def test_employee_cannot_delete_payslip(self) -> None:
        """Non-admin gets 403."""
        app = _build_app(admin_auth=False)
        client = TestClient(app)

        resp = client.delete(f"/api/hr/payslips/{uuid4()}")
        assert resp.status_code == 403

    def test_delete_non_existent_returns_404(self) -> None:
        """Deleting non-existent payslip returns 404."""
        service = FakePayslipHRService()

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.delete(f"/api/hr/payslips/{uuid4()}")
        assert resp.status_code == 404


class TestListPayslips:
    """Tests for GET /api/hr/payslips."""

    def test_hr_can_list_payslips(self) -> None:
        """HR can list all payslips."""
        service = FakePayslipHRService()
        service._add(_make_draft_payslip())
        service._add(_make_draft_payslip())

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.get("/api/hr/payslips")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["payslips"]) == 2

    def test_filter_by_status(self) -> None:
        """Supports filtering by status."""
        employee_id = uuid4()
        service = FakePayslipHRService()

        draft = _make_draft_payslip(employee_id=employee_id)
        published = _make_draft_payslip(employee_id=employee_id)
        published.status = PayslipStatus.PUBLISHED
        published.published_at = datetime.now(UTC)

        service._add(draft)
        service._add(published)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.get("/api/hr/payslips?status=draft")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["payslips"]) == 1
        assert data["payslips"][0]["status"] == "draft"

    def test_filter_by_employee(self) -> None:
        """Supports filtering by employee_id."""
        emp1 = uuid4()
        emp2 = uuid4()
        service = FakePayslipHRService()
        service._add(_make_draft_payslip(employee_id=emp1))
        service._add(_make_draft_payslip(employee_id=emp2))

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.get(f"/api/hr/payslips?employee_id={emp1}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["payslips"]) == 1

    def test_employee_cannot_list(self) -> None:
        """Non-admin gets 403."""
        app = _build_app(admin_auth=False)
        client = TestClient(app)

        resp = client.get("/api/hr/payslips")
        assert resp.status_code == 403


class TestAuditLogging:
    """Verify audit logs are written for mutating operations."""

    def test_create_logs_audit(self) -> None:
        """Create action writes audit log."""
        employee_id = uuid4()
        service = FakePayslipHRService()

        app = _build_app(service=service)
        client = TestClient(app)

        client.post(
            "/api/hr/payslips",
            json={
                "employee_id": str(employee_id),
                "period_month": "2026-06-01",
                "gross_salary": "20000000",
                "deductions": "1000000",
                "insurance_employee": "2100000",
                "taxable_income": "16900000",
                "pit_amount": "1500000",
                "net_salary": "15400000",
            },
        )

        assert any(log["action"] == "create" for log in service.audit_log)

    def test_update_logs_audit(self) -> None:
        """Update action writes audit log."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        client.patch(
            f"/api/hr/payslips/{payslip.id}",
            json={"gross_salary": "18000000"},
        )

        assert any(log["action"] == "update" for log in service.audit_log)

    def test_publish_logs_audit(self) -> None:
        """Publish action writes audit log."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        client.post(f"/api/hr/payslips/{payslip.id}/publish")

        assert any(log["action"] == "publish" for log in service.audit_log)

    def test_delete_logs_audit(self) -> None:
        """Delete action writes audit log."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        client.delete(f"/api/hr/payslips/{payslip.id}")

        assert any(log["action"] == "delete" for log in service.audit_log)


class TestGetPayslipDetail:
    """Tests for GET /api/hr/payslips/{id}."""

    def test_hr_can_get_payslip_by_id(self) -> None:
        """HR can get any payslip by ID."""
        payslip = _make_draft_payslip()
        service = FakePayslipHRService()
        service._add(payslip)

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.get(f"/api/hr/payslips/{payslip.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(payslip.id)
        assert data["status"] == "draft"

    def test_non_existent_returns_404(self) -> None:
        """Non-existent payslip returns 404."""
        service = FakePayslipHRService()

        app = _build_app(service=service)
        client = TestClient(app)

        resp = client.get(f"/api/hr/payslips/{uuid4()}")
        assert resp.status_code == 404
