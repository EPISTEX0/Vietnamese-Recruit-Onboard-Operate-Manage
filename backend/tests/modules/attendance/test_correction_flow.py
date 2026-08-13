"""Tests for HR-only correction flow, reason validation, and audit logging.

Covers:
- HR/Admin-only access: non-admin users get 403.
- CorrectionRequest validation: empty/whitespace reason rejected,
  at least one of check_in_at or check_out_at required.
- Audit logging: successful correction writes ATTENDANCE_CORRECTION audit entry
  with correct details.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.attendance.api.error_handler import register_attendance_error_handlers
from src.modules.attendance.api.router import attendance_router
from src.modules.attendance.container import get_attendance_audit_service, get_attendance_service
from src.modules.attendance.domain.entities import AttendanceRecord, AttendanceSource
from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import AuditActionType, User, UserRole

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

RECORD_ID = uuid4()
EMPLOYEE_ID = uuid4()
ADMIN_USER_ID = uuid4()

RECORD = AttendanceRecord(
    id=RECORD_ID,
    employee_id=EMPLOYEE_ID,
    work_date=datetime.now(UTC).date(),
    check_in_at=datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC),
    check_in_ip="192.168.1.100",
    source=AttendanceSource.WEB,
)


class FakeAdminUser:
    def __init__(self) -> None:
        self.id = ADMIN_USER_ID
        self.role = UserRole.HR
        self.email = "admin@example.com"


class FakeRegularUser:
    def __init__(self) -> None:
        self.id = "user-uuid"
        self.role = UserRole.USER
        self.email = "employee@example.com"


class FakeAttendanceService:
    """Stand-in that records correct_record calls and returns a corrected record."""

    def __init__(self) -> None:
        self.correct_calls: list[dict] = []
        self.correct_side_effect: Exception | None = None

    async def correct_record(
        self,
        record_id,
        check_in_at,
        check_out_at,
        correction_reason,
        corrected_by_user_id,
        admin,
        audit_service,
    ) -> AttendanceRecord:
        if self.correct_side_effect:
            raise self.correct_side_effect
        self.correct_calls.append(
            {
                "record_id": record_id,
                "check_in_at": check_in_at,
                "check_out_at": check_out_at,
                "correction_reason": correction_reason,
                "corrected_by_user_id": corrected_by_user_id,
            }
        )
        corrected = RECORD.model_copy()
        corrected.check_in_at = check_in_at
        corrected.check_out_at = check_out_at
        corrected.correction_reason = correction_reason.strip()
        corrected.corrected_at = datetime.now(UTC)
        corrected.corrected_by_user_id = corrected_by_user_id
        # Mirror real service: write audit inside the same call
        await audit_service.log_action(
            admin=admin,
            action_type=AuditActionType.ATTENDANCE_CORRECTION,
            details={
                "record_id": str(record_id),
                "employee_id": str(corrected.employee_id),
                "correction_reason": correction_reason.strip(),
            },
        )
        return corrected


class FakeAuditService:
    def __init__(self) -> None:
        self.log_calls: list[tuple[User, AuditActionType, dict]] = []

    async def log_action(self, admin: User, action_type: AuditActionType, details: dict) -> None:
        self.log_calls.append((admin, action_type, details))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(
    user: object | None = None,
    service: FakeAttendanceService | None = None,
    audit: FakeAuditService | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(attendance_router)
    register_attendance_error_handlers(app)
    register_auth_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user or FakeAdminUser()
    app.dependency_overrides[get_attendance_service] = lambda: service or FakeAttendanceService()
    app.dependency_overrides[get_attendance_audit_service] = lambda: audit or FakeAuditService()
    return app


VALID_PAYLOAD = {
    "check_in_at": "2026-06-10T09:00:00",
    "correction_reason": "Late bus",
}


# ---------------------------------------------------------------------------
# HR-only access tests
# ---------------------------------------------------------------------------


class TestCorrectionAccessControl:
    """Only HR/Admin can correct attendance records."""

    def test_non_admin_gets_403(self) -> None:
        app = _build_app(user=FakeRegularUser())
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json=VALID_PAYLOAD,
        )
        assert resp.status_code == 403

    def test_admin_can_correct(self) -> None:
        svc = FakeAttendanceService()
        app = _build_app(service=svc)
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json=VALID_PAYLOAD,
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Đã lưu hiệu chỉnh chấm công"
        assert len(svc.correct_calls) == 1


# ---------------------------------------------------------------------------
# CorrectionRequest validation tests
# ---------------------------------------------------------------------------


class TestCorrectionRequestValidation:
    """CorrectionRequest rejects invalid payloads."""

    def test_empty_reason_rejected(self) -> None:
        app = _build_app()
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json={"check_in_at": "2026-06-10T09:00:00", "correction_reason": ""},
        )
        assert resp.status_code == 422

    def test_whitespace_only_reason_rejected(self) -> None:
        app = _build_app()
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json={"check_in_at": "2026-06-10T09:00:00", "correction_reason": "   "},
        )
        assert resp.status_code == 422

    def test_no_changes_rejected(self) -> None:
        """Both check_in_at and check_out_at null → no-op correction rejected."""
        app = _build_app()
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json={"correction_reason": "Just checking"},
        )
        assert resp.status_code == 422

    def test_check_out_only_is_valid(self) -> None:
        """Providing only check_out_at should be accepted."""
        svc = FakeAttendanceService()
        app = _build_app(service=svc)
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json={
                "check_out_at": "2026-06-10T17:00:00",
                "correction_reason": "Forgot to check out",
            },
        )
        assert resp.status_code == 200
        assert len(svc.correct_calls) == 1


# ---------------------------------------------------------------------------
# Audit logging tests
# ---------------------------------------------------------------------------


class TestCorrectionAuditLogging:
    """Successful correction must write an audit log entry."""

    def test_correction_logs_audit_entry(self) -> None:
        audit = FakeAuditService()
        app = _build_app(audit=audit)
        client = TestClient(app)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json=VALID_PAYLOAD,
        )
        assert resp.status_code == 200
        # The service calls audit_service.log_action internally
        assert len(audit.log_calls) == 1
        admin, action_type, details = audit.log_calls[0]
        assert admin.id == ADMIN_USER_ID
        assert action_type == AuditActionType.ATTENDANCE_CORRECTION
        assert details["record_id"] == str(RECORD_ID)
        assert details["employee_id"] == str(EMPLOYEE_ID)
        assert details["correction_reason"] == "Late bus"

    def test_failed_correction_no_audit(self) -> None:
        """If correction fails, no audit entry should be written."""
        svc = FakeAttendanceService()
        svc.correct_side_effect = ValueError("Attendance record not found")
        audit = FakeAuditService()
        app = _build_app(service=svc, audit=audit)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put(
            f"/api/attendance/records/{RECORD_ID}/correct",
            json=VALID_PAYLOAD,
        )
        assert resp.status_code == 500
        assert len(audit.log_calls) == 0
