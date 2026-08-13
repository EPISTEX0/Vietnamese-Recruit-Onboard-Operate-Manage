"""Unit tests for AuditService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.modules.identity.application.audit_service import (
    AuditService,
    PaginatedAuditLogs,
)
from src.modules.identity.domain.entities import (
    AuditActionType,
    AuditLog,
    User,
    UserRole,
)


def _make_admin() -> User:
    """Create a mock admin user for testing."""
    user = User(
        id=uuid4(),
        email="admin@example.com",
        name="Admin User",
        google_sub="google-sub-123",
        role=UserRole.SYSTEM_ADMIN,
    )
    return user


@pytest.fixture
def mock_repo() -> AsyncMock:
    """Create a mock AuditLogRepository."""
    repo = AsyncMock()
    repo.create = AsyncMock()
    repo.get_paginated = AsyncMock(return_value=([], 0))
    return repo


@pytest.fixture
def audit_service(mock_repo: AsyncMock) -> AuditService:
    """Create an AuditService with a mock repository."""
    return AuditService(repository=mock_repo)


@pytest.fixture
def admin_user() -> User:
    """Create a test admin user."""
    return _make_admin()


class TestLogAction:
    """Tests for AuditService.log_action."""

    async def test_creates_audit_log_entry(
        self, audit_service: AuditService, mock_repo: AsyncMock, admin_user: User
    ) -> None:
        details = {"entry_value": "test@example.com", "entry_type": "exact_email"}
        mock_repo.create.return_value = AuditLog(
            admin_user_id=admin_user.id,
            admin_email=admin_user.email,
            action_type=AuditActionType.WHITELIST_ADD,
            details=details,
        )

        await audit_service.log_action(
            admin=admin_user,
            action_type=AuditActionType.WHITELIST_ADD,
            details=details,
        )

        mock_repo.create.assert_called_once()
        created_log = mock_repo.create.call_args[0][0]
        assert created_log.admin_user_id == admin_user.id
        assert created_log.admin_email == admin_user.email
        assert created_log.action_type == AuditActionType.WHITELIST_ADD
        assert created_log.details == details

    async def test_strips_client_secret_from_details(
        self, audit_service: AuditService, mock_repo: AsyncMock, admin_user: User
    ) -> None:
        details = {
            "client_id": "my-client-id",
            "client_secret": "super-secret-value",
            "redirect_uri": "https://example.com/callback",
        }
        mock_repo.create.return_value = AuditLog(
            admin_user_id=admin_user.id,
            admin_email=admin_user.email,
            action_type=AuditActionType.OAUTH_UPDATE,
            details={},
        )

        await audit_service.log_action(
            admin=admin_user,
            action_type=AuditActionType.OAUTH_UPDATE,
            details=details,
        )

        created_log = mock_repo.create.call_args[0][0]
        assert created_log.details["client_id"] == "my-client-id"
        assert created_log.details["client_secret"] == "****"
        assert created_log.details["redirect_uri"] == "https://example.com/callback"

    async def test_strips_nested_secret_values(
        self, audit_service: AuditService, mock_repo: AsyncMock, admin_user: User
    ) -> None:
        details = {
            "changes": {
                "client_secret": "nested-secret",
                "client_id": "visible-id",
            }
        }
        mock_repo.create.return_value = AuditLog(
            admin_user_id=admin_user.id,
            admin_email=admin_user.email,
            action_type=AuditActionType.OAUTH_UPDATE,
            details={},
        )

        await audit_service.log_action(
            admin=admin_user,
            action_type=AuditActionType.OAUTH_UPDATE,
            details=details,
        )

        created_log = mock_repo.create.call_args[0][0]
        assert created_log.details["changes"]["client_secret"] == "****"
        assert created_log.details["changes"]["client_id"] == "visible-id"

    async def test_strips_multiple_sensitive_keys(
        self, audit_service: AuditService, mock_repo: AsyncMock, admin_user: User
    ) -> None:
        details = {
            "password": "my-password",
            "token": "my-token",
            "access_token": "my-access-token",
            "refresh_token": "my-refresh-token",
            "safe_field": "visible",
        }
        mock_repo.create.return_value = AuditLog(
            admin_user_id=admin_user.id,
            admin_email=admin_user.email,
            action_type=AuditActionType.OAUTH_UPDATE,
            details={},
        )

        await audit_service.log_action(
            admin=admin_user,
            action_type=AuditActionType.OAUTH_UPDATE,
            details=details,
        )

        created_log = mock_repo.create.call_args[0][0]
        assert created_log.details["password"] == "****"
        assert created_log.details["token"] == "****"
        assert created_log.details["access_token"] == "****"
        assert created_log.details["refresh_token"] == "****"
        assert created_log.details["safe_field"] == "visible"

    async def test_handles_empty_details(
        self, audit_service: AuditService, mock_repo: AsyncMock, admin_user: User
    ) -> None:
        mock_repo.create.return_value = AuditLog(
            admin_user_id=admin_user.id,
            admin_email=admin_user.email,
            action_type=AuditActionType.ROLE_CHANGE,
            details={},
        )

        await audit_service.log_action(
            admin=admin_user,
            action_type=AuditActionType.ROLE_CHANGE,
            details={},
        )

        created_log = mock_repo.create.call_args[0][0]
        assert created_log.details == {}


class TestGetLogs:
    """Tests for AuditService.get_logs."""

    async def test_returns_paginated_result(
        self, audit_service: AuditService, mock_repo: AsyncMock
    ) -> None:
        log1 = AuditLog(
            admin_user_id=uuid4(),
            admin_email="admin@example.com",
            action_type=AuditActionType.WHITELIST_ADD,
            details={"entry_value": "test@example.com"},
        )
        mock_repo.get_paginated.return_value = ([log1], 1)

        result = await audit_service.get_logs(page=1, page_size=10)

        assert isinstance(result, PaginatedAuditLogs)
        assert result.items == [log1]
        assert result.total == 1
        assert result.page == 1
        assert result.page_size == 10

    async def test_calculates_correct_offset(
        self, audit_service: AuditService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_paginated.return_value = ([], 0)

        await audit_service.get_logs(page=3, page_size=20)

        mock_repo.get_paginated.assert_called_once_with(
            offset=40,
            limit=20,
            filters={},
        )

    async def test_passes_action_type_filter(
        self, audit_service: AuditService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_paginated.return_value = ([], 0)

        await audit_service.get_logs(page=1, page_size=10, action_type="whitelist_add")

        call_filters = mock_repo.get_paginated.call_args[1]["filters"]
        assert call_filters["action_type"] == "whitelist_add"

    async def test_passes_date_range_filters(
        self, audit_service: AuditService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_paginated.return_value = ([], 0)
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 12, 31, tzinfo=UTC)

        await audit_service.get_logs(page=1, page_size=10, start_date=start, end_date=end)

        call_filters = mock_repo.get_paginated.call_args[1]["filters"]
        assert call_filters["start_date"] == start
        assert call_filters["end_date"] == end

    async def test_no_filters_when_none_provided(
        self, audit_service: AuditService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_paginated.return_value = ([], 0)

        await audit_service.get_logs(page=1, page_size=20)

        mock_repo.get_paginated.assert_called_once_with(
            offset=0,
            limit=20,
            filters={},
        )

    async def test_default_pagination_values(
        self, audit_service: AuditService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_paginated.return_value = ([], 0)

        await audit_service.get_logs()

        mock_repo.get_paginated.assert_called_once_with(
            offset=0,
            limit=20,
            filters={},
        )


class TestSanitizeDetails:
    """Tests for the static _sanitize_details method."""

    def test_preserves_non_sensitive_keys(self) -> None:
        details = {"action": "add", "target": "user@example.com"}
        result = AuditService._sanitize_details(details)
        assert result == details

    def test_masks_case_insensitive_keys(self) -> None:
        details = {"Client_Secret": "secret-value"}
        result = AuditService._sanitize_details(details)
        assert result["Client_Secret"] == "****"

    def test_does_not_modify_original_dict(self) -> None:
        details = {"client_secret": "original-secret", "client_id": "my-id"}
        AuditService._sanitize_details(details)
        assert details["client_secret"] == "original-secret"
