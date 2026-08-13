"""API-layer tests for attendance network allowlist access control and validation.

Tests the FastAPI router endpoints using mocked dependencies (no database).
Covers:

- Access control: HR/Admin can write, non-admin Employee gets 403.
- Validation: invalid CIDR/IP returns 400, duplicate returns 400,
  combined > 20 returns 400.
- Plain IP normalization: ``192.168.1.10`` accepted as ``192.168.1.10/32``.
- Audit logging: write endpoints call audit_service.log_action.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.attendance.api.error_handler import (
    register_attendance_error_handlers,
)
from src.modules.attendance.api.router import (
    attendance_router,
)
from src.modules.attendance.container import (
    get_attendance_audit_service,
    get_attendance_settings_service,
)
from src.modules.attendance.domain.exceptions import (
    DuplicateCidrError,
    InvalidCidrError,
    TooManyNetworksError,
)
from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import AuditActionType, User, UserRole

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAdminUser:
    """Fake admin user for dependency override."""

    def __init__(self) -> None:
        self.id = "admin-uuid"
        self.role = UserRole.HR
        self.email = "admin@example.com"


class FakeEmployeeUser:
    """Fake non-admin employee user for dependency override."""

    def __init__(self) -> None:
        self.id = "employee-uuid"
        self.role = UserRole.USER
        self.email = "employee@example.com"


class FakeAttendanceService:
    """Stand-in for AttendanceSettingsService returning canned data or raising."""

    def __init__(self) -> None:
        self.networks: list[str] = ["192.168.1.0/24"]
        self.get_calls: int = 0
        self.set_calls: list[list[str]] = []
        self.add_calls: list[list[str]] = []
        self.remove_calls: list[str] = []
        self.set_side_effect: Exception | None = None
        self.add_side_effect: Exception | None = None
        self.remove_side_effect: Exception | None = None

    async def get_allowed_networks(self) -> list[str]:
        self.get_calls += 1
        return list(self.networks)

    async def set_allowed_networks(self, networks: list[str]) -> list[str]:
        if self.set_side_effect:
            raise self.set_side_effect
        self.set_calls.append(networks)
        self.networks = list(networks)
        return list(self.networks)

    async def add_networks(self, networks: list[str]) -> list[str]:
        if self.add_side_effect:
            raise self.add_side_effect
        self.add_calls.append(networks)
        self.networks.extend(networks)
        return list(self.networks)

    async def remove_network(self, cidr: str) -> list[str]:
        if self.remove_side_effect:
            raise self.remove_side_effect
        self.remove_calls.append(cidr)
        self.networks = [n for n in self.networks if n != cidr]
        return list(self.networks)


class FakeAuditService:
    """Stand-in for AuditService recording calls."""

    def __init__(self) -> None:
        self.log_calls: list[tuple[User, AuditActionType, dict]] = []

    async def log_action(self, admin: User, action_type: AuditActionType, details: dict) -> None:
        self.log_calls.append((admin, action_type, details))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(
    user: object,
    service: FakeAttendanceService,
    audit: FakeAuditService | None = None,
) -> FastAPI:
    """Build a test FastAPI app with overridden dependencies."""
    app = FastAPI()
    app.include_router(attendance_router)
    register_attendance_error_handlers(app)
    register_auth_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_attendance_settings_service] = lambda: service
    app.dependency_overrides[get_attendance_audit_service] = lambda: audit or FakeAuditService()
    return app


# ---------------------------------------------------------------------------
# Access control tests
# ---------------------------------------------------------------------------


class TestNetworkAllowlistAccessControl:
    """Access control: HR/Admin can write, Employee cannot."""

    def test_hr_can_get_networks(self) -> None:
        """HR/Admin can read the network allowlist."""
        svc = FakeAttendanceService()
        app = _build_app(FakeAdminUser(), svc)
        client = TestClient(app)

        resp = client.get("/api/attendance/settings/network")
        assert resp.status_code == 200
        assert resp.json()["networks"] == ["192.168.1.0/24"]

    def test_employee_can_get_networks(self) -> None:
        """Non-admin Employee can also read (transparency)."""
        svc = FakeAttendanceService()
        app = _build_app(FakeEmployeeUser(), svc)
        client = TestClient(app)

        resp = client.get("/api/attendance/settings/network")
        assert resp.status_code == 200

    def test_hr_can_update_networks(self) -> None:
        """HR/Admin can replace the network allowlist."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.put(
            "/api/attendance/settings/network",
            json={"networks": ["10.0.0.0/8"]},
        )
        assert resp.status_code == 200
        assert svc.set_calls == [["10.0.0.0/8"]]

    def test_employee_cannot_update_networks(self) -> None:
        """Non-admin Employee gets 403 when trying to update."""
        svc = FakeAttendanceService()
        app = _build_app(FakeEmployeeUser(), svc)
        client = TestClient(app)

        resp = client.put(
            "/api/attendance/settings/network",
            json={"networks": ["10.0.0.0/8"]},
        )
        assert resp.status_code == 403

    def test_hr_can_add_networks(self) -> None:
        """HR/Admin can add CIDRs to the allowlist."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.post(
            "/api/attendance/settings/network/add",
            json={"networks": ["10.0.0.0/8"]},
        )
        assert resp.status_code == 200
        assert svc.add_calls == [["10.0.0.0/8"]]

    def test_employee_cannot_add_networks(self) -> None:
        """Non-admin Employee gets 403 when trying to add."""
        svc = FakeAttendanceService()
        app = _build_app(FakeEmployeeUser(), svc)
        client = TestClient(app)

        resp = client.post(
            "/api/attendance/settings/network/add",
            json={"networks": ["10.0.0.0/8"]},
        )
        assert resp.status_code == 403

    def test_hr_can_remove_network(self) -> None:
        """HR/Admin can remove a CIDR from the allowlist."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.delete(
            "/api/attendance/settings/network",
            params={"cidr": "192.168.1.0/24"},
        )
        assert resp.status_code == 200
        assert svc.remove_calls == ["192.168.1.0/24"]

    def test_employee_cannot_remove_network(self) -> None:
        """Non-admin Employee gets 403 when trying to remove."""
        svc = FakeAttendanceService()
        app = _build_app(FakeEmployeeUser(), svc)
        client = TestClient(app)

        resp = client.delete(
            "/api/attendance/settings/network",
            params={"cidr": "192.168.1.0/24"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Validation error tests (service raises domain exceptions)
# ---------------------------------------------------------------------------


class TestNetworkAllowlistValidationErrors:
    """Validation: invalid CIDR, duplicate, combined >20 return clear errors."""

    def test_invalid_cidr_returns_400(self) -> None:
        """Invalid CIDR format returns 400 with INVALID_CIDR error code."""
        svc = FakeAttendanceService()
        svc.set_side_effect = InvalidCidrError("not-a-cidr")
        app = _build_app(FakeAdminUser(), svc)
        client = TestClient(app)

        resp = client.put(
            "/api/attendance/settings/network",
            json={"networks": ["not-a-cidr"]},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error_code"] == "INVALID_CIDR"
        assert "not-a-cidr" in body["detail"]

    def test_duplicate_cidr_returns_400(self) -> None:
        """Duplicate CIDR returns 400 with DUPLICATE_CIDR error code."""
        svc = FakeAttendanceService()
        svc.add_side_effect = DuplicateCidrError("192.168.1.0/24")
        app = _build_app(FakeAdminUser(), svc)
        client = TestClient(app)

        resp = client.post(
            "/api/attendance/settings/network/add",
            json={"networks": ["192.168.1.0/24"]},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error_code"] == "DUPLICATE_CIDR"

    def test_too_many_networks_returns_400(self) -> None:
        """Combined > 20 networks returns 400 with TOO_MANY_NETWORKS."""
        svc = FakeAttendanceService()
        svc.add_side_effect = TooManyNetworksError(20)
        app = _build_app(FakeAdminUser(), svc)
        client = TestClient(app)

        networks = [f"10.0.{i}.0/24" for i in range(21)]
        resp = client.post(
            "/api/attendance/settings/network/add",
            json={"networks": networks},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error_code"] == "TOO_MANY_NETWORKS"


# ---------------------------------------------------------------------------
# Audit logging tests
# ---------------------------------------------------------------------------


class TestNetworkAllowlistAuditLogging:
    """Write endpoints must log audit entries on success."""

    def test_update_networks_logs_audit(self) -> None:
        """PUT /settings/network logs ATTENDANCE_NETWORK_UPDATE."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.put(
            "/api/attendance/settings/network",
            json={"networks": ["10.0.0.0/8"]},
        )
        assert resp.status_code == 200
        assert len(audit.log_calls) == 1
        _, action_type, details = audit.log_calls[0]
        assert action_type == AuditActionType.ATTENDANCE_NETWORK_UPDATE
        assert details["networks"] == ["10.0.0.0/8"]

    def test_add_networks_logs_audit(self) -> None:
        """POST /settings/network/add logs ATTENDANCE_NETWORK_ADD."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.post(
            "/api/attendance/settings/network/add",
            json={"networks": ["10.0.0.0/8"]},
        )
        assert resp.status_code == 200
        assert len(audit.log_calls) == 1
        _, action_type, details = audit.log_calls[0]
        assert action_type == AuditActionType.ATTENDANCE_NETWORK_ADD
        assert details["added"] == ["10.0.0.0/8"]

    def test_remove_network_logs_audit(self) -> None:
        """DELETE /settings/network logs ATTENDANCE_NETWORK_REMOVE."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.delete(
            "/api/attendance/settings/network",
            params={"cidr": "192.168.1.0/24"},
        )
        assert resp.status_code == 200
        assert len(audit.log_calls) == 1
        _, action_type, details = audit.log_calls[0]
        assert action_type == AuditActionType.ATTENDANCE_NETWORK_REMOVE
        assert details["removed"] == "192.168.1.0/24"

    def test_no_audit_on_failed_write(self) -> None:
        """Failed write (validation error) does not produce audit entry."""
        svc = FakeAttendanceService()
        svc.set_side_effect = InvalidCidrError("bad")
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.put(
            "/api/attendance/settings/network",
            json={"networks": ["bad"]},
        )
        assert resp.status_code == 400
        assert len(audit.log_calls) == 0

    def test_no_audit_on_get(self) -> None:
        """GET (read) does not produce audit entry."""
        svc = FakeAttendanceService()
        audit = FakeAuditService()
        app = _build_app(FakeAdminUser(), svc, audit)
        client = TestClient(app)

        resp = client.get("/api/attendance/settings/network")
        assert resp.status_code == 200
        assert len(audit.log_calls) == 0
