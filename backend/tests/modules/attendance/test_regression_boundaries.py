"""Regression tests for Attendance Record critical boundaries.

Covers edge cases across the AttendanceService that existing unit-level
tests may not reach in combination: empty-history, corrective-overwrite
guards, and the get_today None case.

These tests are kept focused -- no brittle snapshots, no integration DB.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.modules.attendance.application.attendance_service import AttendanceService
from src.modules.attendance.application.attendance_settings_service import (
    AttendanceSettingsService,
)
from src.modules.attendance.domain.entities import AttendanceRecord, AttendanceSource
from src.modules.attendance.domain.exceptions import (
    NotCheckedInError,
    OfficeNetworkRequiredError,
)
from src.modules.attendance.infrastructure.attendance_record_repository import (
    AttendanceRecordRepository,
)
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)


@pytest.fixture
def mock_attendance_repo():
    """Create a mock AttendanceRecordRepository."""
    return AsyncMock(spec=AttendanceRecordRepository)


@pytest.fixture
def mock_org_settings_repo():
    """Create a mock OrganizationSettingsRepository."""
    repo = AsyncMock(spec=OrganizationSettingsRepository)
    repo.get_timezone = AsyncMock(return_value="Asia/Ho_Chi_Minh")
    return repo


@pytest.fixture
def mock_settings_service():
    """Create a mock AttendanceSettingsService."""
    return AsyncMock(spec=AttendanceSettingsService)


@pytest.fixture
def attendance_service(mock_attendance_repo, mock_org_settings_repo, mock_settings_service):
    """Create an AttendanceService with mocked dependencies."""
    return AttendanceService(
        attendance_repo=mock_attendance_repo,
        org_settings_repo=mock_org_settings_repo,
        settings_service=mock_settings_service,
    )


# ---------------------------------------------------------------------------
# Regression: blocked IP boundary
# ---------------------------------------------------------------------------


class TestBlockedIPBoundary:
    """Regression: blocked IP for check-in and check-out."""

    @pytest.mark.asyncio
    async def test_check_in_blocked_ip_does_not_create_record(
        self, attendance_service, mock_settings_service, mock_attendance_repo
    ):
        """Blocked IP raises error and never calls the repository."""
        mock_settings_service.is_ip_allowed = AsyncMock(return_value=False)

        with pytest.raises(OfficeNetworkRequiredError):
            await attendance_service.check_in(
                employee_id=uuid4(),
                client_ip="10.0.0.1",
                user_agent="test",
            )

        mock_attendance_repo.upsert_check_in.assert_not_called()
        mock_attendance_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_out_blocked_ip_does_not_update_record(
        self, attendance_service, mock_settings_service, mock_attendance_repo
    ):
        """Blocked IP on check-out raises error and never updates."""
        mock_settings_service.is_ip_allowed = AsyncMock(return_value=False)

        with pytest.raises(OfficeNetworkRequiredError):
            await attendance_service.check_out(
                employee_id=uuid4(),
                client_ip="10.0.0.1",
                user_agent="test",
            )

        mock_attendance_repo.get_by_employee_and_date.assert_not_called()
        mock_attendance_repo.update.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: allowed IP boundary
# ---------------------------------------------------------------------------


class TestAllowedIPBoundary:
    """Regression: allowed IP proceeds to persistence."""

    @pytest.mark.asyncio
    async def test_allowed_ip_check_in_creates_record(
        self, attendance_service, mock_settings_service, mock_attendance_repo
    ):
        """Allowed IP results in upsert_check_in call."""
        mock_settings_service.is_ip_allowed = AsyncMock(return_value=True)

        employee_id = uuid4()
        created = AttendanceRecord(
            id=uuid4(),
            employee_id=employee_id,
            work_date=date.today(),
            check_in_at=datetime.now(UTC),
            check_in_ip="192.168.1.100",
            source=AttendanceSource.WEB,
        )
        mock_attendance_repo.upsert_check_in = AsyncMock(return_value=created)

        result = await attendance_service.check_in(
            employee_id=employee_id,
            client_ip="192.168.1.100",
            user_agent="test",
        )

        assert result.employee_id == employee_id
        mock_attendance_repo.upsert_check_in.assert_called_once()


# ---------------------------------------------------------------------------
# Regression: idempotency boundary
# ---------------------------------------------------------------------------


class TestIdempotencyBoundary:
    """Regression: repeated check-in/check-out are no-ops."""

    @pytest.mark.asyncio
    async def test_repeated_check_in_returns_same_record(
        self, attendance_service, mock_settings_service, mock_attendance_repo
    ):
        """Calling check-in twice returns the same existing record."""
        mock_settings_service.is_ip_allowed = AsyncMock(return_value=True)
        employee_id = uuid4()

        existing = AttendanceRecord(
            id=uuid4(),
            employee_id=employee_id,
            work_date=date.today(),
            check_in_at=datetime.now(UTC),
            check_in_ip="192.168.1.100",
            source=AttendanceSource.WEB,
        )
        mock_attendance_repo.upsert_check_in = AsyncMock(return_value=existing)

        result = await attendance_service.check_in(
            employee_id=employee_id,
            client_ip="192.168.1.100",
            user_agent="test",
        )

        assert result.id == existing.id
        assert result.check_in_at == existing.check_in_at

        # Second call returns same record (idempotent)
        result2 = await attendance_service.check_in(
            employee_id=employee_id,
            client_ip="192.168.1.100",
            user_agent="test",
        )
        assert result2.id == existing.id
        assert result2.check_in_at == existing.check_in_at

    @pytest.mark.asyncio
    async def test_repeated_check_out_returns_same_record(
        self, attendance_service, mock_settings_service, mock_attendance_repo
    ):
        """Calling check-out twice returns the same existing record (no update)."""
        mock_settings_service.is_ip_allowed = AsyncMock(return_value=True)
        employee_id = uuid4()
        now = datetime.now(UTC)

        existing = AttendanceRecord(
            id=uuid4(),
            employee_id=employee_id,
            work_date=date.today(),
            check_in_at=now,
            check_out_at=now,
            check_in_ip="192.168.1.100",
            check_out_ip="192.168.1.100",
            source=AttendanceSource.WEB,
        )
        mock_attendance_repo.get_by_employee_and_date = AsyncMock(return_value=existing)

        result = await attendance_service.check_out(
            employee_id=employee_id,
            client_ip="192.168.1.100",
            user_agent="test",
        )

        assert result.check_out_at == existing.check_out_at
        mock_attendance_repo.update.assert_not_called()

        # Second call returns same record (idempotent, no update)
        result2 = await attendance_service.check_out(
            employee_id=employee_id,
            client_ip="192.168.1.100",
            user_agent="test",
        )
        assert result2.check_out_at == existing.check_out_at


# ---------------------------------------------------------------------------
# Regression: ownership boundary
# ---------------------------------------------------------------------------


class TestOwnershipBoundary:
    """Regression: check-out fails when no check-in exists for that employee."""

    @pytest.mark.asyncio
    async def test_check_out_without_check_in_raises_error(
        self, attendance_service, mock_settings_service, mock_attendance_repo
    ):
        """Check-out raises NotCheckedInError when no record exists for today."""
        mock_settings_service.is_ip_allowed = AsyncMock(return_value=True)
        mock_attendance_repo.get_by_employee_and_date = AsyncMock(return_value=None)

        with pytest.raises(NotCheckedInError):
            await attendance_service.check_out(
                employee_id=uuid4(),
                client_ip="192.168.1.100",
                user_agent="test",
            )


# ---------------------------------------------------------------------------
# Regression: timezone work_date boundary
# ---------------------------------------------------------------------------


class TestWorkDateBoundary:
    """Regression: work_date is always derived from Organization timezone."""

    @pytest.mark.asyncio
    async def test_work_date_midnight_boundary(self, attendance_service, mock_org_settings_repo):
        """Work date in UTC+7 timezone (Asia/Ho_Chi_Minh) is computed correctly."""
        mock_org_settings_repo.get_timezone = AsyncMock(return_value="Asia/Ho_Chi_Minh")

        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        frozen = datetime(2026, 6, 11, 17, 0, 0, tzinfo=UTC)
        # Patch datetime.now in the service module to return frozen time
        module_path = attendance_service.__module__
        with patch(f"{module_path}.datetime") as mock_dt:
            mock_dt.now.return_value = frozen
            mock_dt.UTC = UTC
            work_date = await attendance_service._get_work_date()

        expected = frozen.astimezone(tz=ZoneInfo("Asia/Ho_Chi_Minh")).date()
        assert work_date == expected, "work_date must be derived from Asia/Ho_Chi_Minh timezone"


# ---------------------------------------------------------------------------
# Regression: HR correction audit boundary
# ---------------------------------------------------------------------------


class TestCorrectionAuditBoundary:
    """Regression: correction writes audit log with full change details."""

    @pytest.mark.asyncio
    async def test_correction_preserves_previous_timestamps(
        self, attendance_service, mock_attendance_repo
    ):
        """Correction saves previous check_in/check_out values before overwriting."""
        employee_id = uuid4()

        original = AttendanceRecord(
            id=uuid4(),
            employee_id=employee_id,
            work_date=date(2026, 6, 1),
            check_in_at=datetime(2026, 6, 1, 1, 0, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 6, 1, 10, 30, 0, tzinfo=UTC),
            check_in_ip="192.168.1.100",
            check_out_ip="192.168.1.100",
            source=AttendanceSource.WEB,
        )
        previous_check_in_at = original.check_in_at
        previous_check_out_at = original.check_out_at

        # Return a deep-ish copy so in-place mutation by service doesn't
        # alter the snapshot we compare against.
        import copy

        mock_attendance_repo.get_by_id = AsyncMock(return_value=original)
        mock_attendance_repo.update = AsyncMock(side_effect=lambda r: copy.copy(r))

        from src.modules.identity.domain.entities import AuditActionType, User, UserRole

        admin = User(id=uuid4(), role=UserRole.HR, email="admin@test.com")

        audit_service = AsyncMock()
        audit_service.log_action = AsyncMock()

        corrected = await attendance_service.correct_record(
            record_id=original.id,
            check_in_at=datetime(2026, 6, 1, 2, 0, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC),
            correction_reason="Late arrival adjusted",
            corrected_by_user_id=admin.id,
            admin=admin,
            audit_service=audit_service,
        )

        # Previous values preserved
        assert corrected.previous_check_in_at == previous_check_in_at
        assert corrected.previous_check_out_at == previous_check_out_at

        # New values applied
        assert corrected.check_in_at == datetime(2026, 6, 1, 2, 0, 0, tzinfo=UTC)
        assert corrected.check_out_at == datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC)

        # Audit logged
        audit_service.log_action.assert_called_once()
        call_args = audit_service.log_action.call_args
        assert call_args[1]["action_type"] == AuditActionType.ATTENDANCE_CORRECTION
        details = call_args[1]["details"]
        assert details["correction_reason"] == "Late arrival adjusted"
        assert details["record_id"] == str(original.id)
        assert details["previous_check_in_at"] == previous_check_in_at.isoformat()
        assert details["previous_check_out_at"] == previous_check_out_at.isoformat()
        assert details["new_check_in_at"] == datetime(2026, 6, 1, 2, 0, 0, tzinfo=UTC).isoformat()
        assert details["new_check_out_at"] == datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC).isoformat()


# ---------------------------------------------------------------------------
# Regression: get_today / get_history empty-state boundaries
# ---------------------------------------------------------------------------


class TestEmptyStateBoundary:
    """Regression: service returns None/empty for missing data."""

    @pytest.mark.asyncio
    async def test_get_today_returns_none_when_no_record(
        self, attendance_service, mock_attendance_repo
    ):
        """get_today returns None (not raises) when no record for today."""
        mock_attendance_repo.get_by_employee_and_date = AsyncMock(return_value=None)

        result = await attendance_service.get_today(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_history_returns_empty_list_when_no_records(
        self, attendance_service, mock_attendance_repo
    ):
        """get_history returns [] (not raises) when no records in month."""
        mock_attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])

        result = await attendance_service.get_history(
            employee_id=uuid4(),
            year=2026,
            month=1,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_history_returns_empty_list_when_no_records(
        self, attendance_service, mock_attendance_repo
    ):
        """get_history returns [] (not raises) when no records in recent days."""
        mock_attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])

        result = await attendance_service.get_history(
            employee_id=uuid4(),
            days=7,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_history_computes_date_range(
        self, attendance_service, mock_attendance_repo, mock_org_settings_repo
    ):
        """get_history with days param computes correct date range."""
        from zoneinfo import ZoneInfo

        mock_org_settings_repo.get_timezone = AsyncMock(return_value="Asia/Ho_Chi_Minh")
        mock_attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])

        emp_id = uuid4()
        await attendance_service.get_history(
            employee_id=emp_id,
            days=7,
        )

        # Verify the date range was passed correctly
        call_args = mock_attendance_repo.get_by_employee_and_date_range.call_args
        assert call_args is not None
        kwargs = call_args[1]
        assert kwargs["employee_id"] == emp_id
        # Use same timezone as service (Asia/Ho_Chi_Minh) for deterministic assertion
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        assert kwargs["start_date"] == today - timedelta(days=6)
        assert kwargs["end_date"] == today

    @pytest.mark.asyncio
    async def test_correct_nonexistent_record_raises_value_error(
        self, attendance_service, mock_attendance_repo
    ):
        """Correcting a non-existent record raises ValueError."""
        mock_attendance_repo.get_by_id = AsyncMock(return_value=None)

        from src.modules.identity.domain.entities import User, UserRole

        admin = User(id=uuid4(), role=UserRole.HR, email="admin@test.com")
        audit_service = AsyncMock()

        with pytest.raises(ValueError, match="Attendance record not found"):
            await attendance_service.correct_record(
                record_id=uuid4(),
                check_in_at=datetime.now(UTC),
                check_out_at=None,
                correction_reason="Fix",
                corrected_by_user_id=admin.id,
                admin=admin,
                audit_service=audit_service,
            )
