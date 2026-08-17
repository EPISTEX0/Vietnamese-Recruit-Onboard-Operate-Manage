"""Unit tests for WhitelistManager composite whitelist service."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.identity.application.whitelist_manager import (
    WhitelistManager,
)
from src.modules.identity.domain.entities import (
    User,
    UserRole,
    WhitelistEntry,
    WhitelistEntryType,
)


@pytest.fixture
def mock_repo() -> MagicMock:
    """Create a mock WhitelistRepository."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=[])
    repo.get_user_email = AsyncMock(return_value="unknown@example.com")
    repo.add = AsyncMock()
    repo.remove = AsyncMock()
    repo.exists = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def admin_user() -> User:
    """Create a mock admin user."""
    return User(
        id=uuid4(),
        email="admin@example.com",
        name="Admin User",
        google_sub="google-sub-123",
        role=UserRole.SYSTEM_ADMIN,
    )


@pytest.fixture
def sample_db_entries(admin_user: User) -> list[WhitelistEntry]:
    """Create sample database whitelist entries."""
    return [
        WhitelistEntry(
            id=uuid4(),
            value="db-user@example.com",
            entry_type=WhitelistEntryType.EXACT_EMAIL,
            added_by_user_id=admin_user.id,
            created_at=datetime.now(UTC),
        ),
        WhitelistEntry(
            id=uuid4(),
            value="@db-domain.com",
            entry_type=WhitelistEntryType.DOMAIN_PATTERN,
            added_by_user_id=admin_user.id,
            created_at=datetime.now(UTC),
        ),
    ]


class TestWhitelistManagerIsAllowed:
    """Tests for is_allowed and is_allowed_async methods."""

    async def test_db_exact_email_match(
        self, mock_repo: MagicMock, sample_db_entries: list[WhitelistEntry]
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=sample_db_entries)
        manager = WhitelistManager(repo=mock_repo)
        assert await manager.is_allowed_async("db-user@example.com") is True

    async def test_db_domain_pattern_match(
        self, mock_repo: MagicMock, sample_db_entries: list[WhitelistEntry]
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=sample_db_entries)
        manager = WhitelistManager(repo=mock_repo)
        assert await manager.is_allowed_async("anyone@db-domain.com") is True

    async def test_db_only_whitelist_works(
        self, mock_repo: MagicMock, sample_db_entries: list[WhitelistEntry]
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=sample_db_entries)
        manager = WhitelistManager(repo=mock_repo)
        assert await manager.is_allowed_async("db-user@example.com") is True
        assert await manager.is_allowed_async("alice@example.com") is False


class TestWhitelistManagerAddEntry:
    """Tests for add_entry method."""

    async def test_add_valid_email(self, mock_repo: MagicMock, admin_user: User) -> None:
        mock_repo.add = AsyncMock(side_effect=lambda entry: entry)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        result = await manager.add_entry("new@example.com", admin_user)
        assert result.value == "new@example.com"
        assert result.entry_type == WhitelistEntryType.EXACT_EMAIL
        assert result.added_by_user_id == admin_user.id

    async def test_add_valid_domain_pattern(self, mock_repo: MagicMock, admin_user: User) -> None:
        mock_repo.add = AsyncMock(side_effect=lambda entry: entry)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        result = await manager.add_entry("@newdomain.com", admin_user)
        assert result.value == "@newdomain.com"
        assert result.entry_type == WhitelistEntryType.DOMAIN_PATTERN

    async def test_add_invalid_format_raises_422(
        self, mock_repo: MagicMock, admin_user: User
    ) -> None:
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await manager.add_entry("not-an-email", admin_user)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "WHITELIST_INVALID_FORMAT"

    async def test_add_invalid_domain_raises_422(
        self, mock_repo: MagicMock, admin_user: User
    ) -> None:
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await manager.add_entry("@", admin_user)
        assert exc_info.value.status_code == 422

    async def test_add_duplicate_raises_409(self, mock_repo: MagicMock, admin_user: User) -> None:
        mock_repo.exists = AsyncMock(return_value=True)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await manager.add_entry("existing@example.com", admin_user)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "WHITELIST_DUPLICATE"

    async def test_add_updates_cache_immediately(
        self, mock_repo: MagicMock, admin_user: User
    ) -> None:
        mock_repo.add = AsyncMock(side_effect=lambda entry: entry)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        assert manager.is_allowed("new@example.com") is False
        await manager.add_entry("new@example.com", admin_user)
        assert manager.is_allowed("new@example.com") is True

    async def test_add_domain_updates_cache_immediately(
        self, mock_repo: MagicMock, admin_user: User
    ) -> None:
        mock_repo.add = AsyncMock(side_effect=lambda entry: entry)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        assert manager.is_allowed("user@newdomain.com") is False
        await manager.add_entry("@newdomain.com", admin_user)
        assert manager.is_allowed("user@newdomain.com") is True


class TestWhitelistManagerRemoveEntry:
    """Tests for remove_entry method."""

    async def test_remove_existing_entry(
        self,
        mock_repo: MagicMock,
        admin_user: User,
        sample_db_entries: list[WhitelistEntry],
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=sample_db_entries)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        entry_id = sample_db_entries[0].id
        await manager.remove_entry(entry_id, admin_user)
        mock_repo.remove.assert_called_once_with(entry_id)

    async def test_remove_nonexistent_raises_404(
        self, mock_repo: MagicMock, admin_user: User
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=[])
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await manager.remove_entry(uuid4(), admin_user)
        assert exc_info.value.status_code == 404

    async def test_remove_updates_cache(
        self,
        mock_repo: MagicMock,
        admin_user: User,
        sample_db_entries: list[WhitelistEntry],
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=sample_db_entries)
        manager = WhitelistManager(repo=mock_repo)
        await manager.refresh_cache()

        assert manager.is_allowed("db-user@example.com") is True
        await manager.remove_entry(sample_db_entries[0].id, admin_user)
        assert manager.is_allowed("db-user@example.com") is False


class TestWhitelistManagerListEntries:
    """Tests for list_entries method."""

    async def test_list_db_entries(
        self,
        mock_repo: MagicMock,
        admin_user: User,
        sample_db_entries: list[WhitelistEntry],
    ) -> None:
        mock_repo.get_all = AsyncMock(return_value=sample_db_entries)
        mock_repo.get_user_email = AsyncMock(return_value=admin_user.email)

        manager = WhitelistManager(repo=mock_repo)
        entries = await manager.list_entries()

        assert len(entries) == 2
        assert all(e.source == "database" for e in entries)
        assert all(e.is_readonly is False for e in entries)
        assert all(e.added_by_email == admin_user.email for e in entries)
        mock_repo.get_user_email.assert_awaited_with(sample_db_entries[0].added_by_user_id)


class TestWhitelistManagerRefreshCache:
    """Tests for refresh_cache method."""

    async def test_refresh_updates_timestamp(self, mock_repo: MagicMock) -> None:
        manager = WhitelistManager(repo=mock_repo)
        assert manager._cache_timestamp == 0.0
        await manager.refresh_cache()
        assert manager._cache_timestamp > 0.0


class TestWhitelistManagerEntryTypeDetection:
    """Tests for _detect_entry_type static method."""

    def test_valid_email(self) -> None:
        assert (
            WhitelistManager._detect_entry_type("user@example.com")
            == WhitelistEntryType.EXACT_EMAIL
        )

    def test_valid_domain_pattern(self) -> None:
        assert (
            WhitelistManager._detect_entry_type("@example.com") == WhitelistEntryType.DOMAIN_PATTERN
        )

    def test_invalid_no_at_sign(self) -> None:
        assert WhitelistManager._detect_entry_type("notanemail") is None

    def test_invalid_just_at(self) -> None:
        assert WhitelistManager._detect_entry_type("@") is None

    def test_invalid_domain_no_tld(self) -> None:
        assert WhitelistManager._detect_entry_type("@localhost") is None

    def test_valid_subdomain_pattern(self) -> None:
        assert (
            WhitelistManager._detect_entry_type("@sub.example.com")
            == WhitelistEntryType.DOMAIN_PATTERN
        )

    def test_valid_complex_email(self) -> None:
        assert (
            WhitelistManager._detect_entry_type("user.name+tag@example.co.uk")
            == WhitelistEntryType.EXACT_EMAIL
        )
