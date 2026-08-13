"""Unit tests for OrganizationSettingsRepository domain methods.

Tests the CRUD operations for allowed_domains on the OrganizationSettings
entity, including validation and edge cases.
"""

from __future__ import annotations

import pytest

from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)


class FakeSession:
    """Minimal fake async session for repository tests."""

    def __init__(self) -> None:
        self._settings = None
        self._flushed = False
        self._committed = False

    async def execute(self, statement):
        class FakeResult:
            def __init__(self, settings):
                self._settings = settings

            def scalars(self):
                class FakeScalarResult:
                    def __init__(self, settings):
                        self._settings = settings

                    def first(self):
                        return self._settings

                return FakeScalarResult(self._settings)

        return FakeResult(self._settings)

    def add(self, obj):
        self._settings = obj

    async def flush(self):
        self._flushed = True

    async def commit(self):
        # The repository's write paths commit; without this the double raises
        # AttributeError and every write test fails before its own assertions.
        self._committed = True


@pytest.fixture
def repo() -> tuple[OrganizationSettingsRepository, FakeSession]:
    """Create a repository with a fake session."""
    session = FakeSession()
    repository = OrganizationSettingsRepository(session=session)
    return repository, session


@pytest.mark.asyncio
async def test_get_allowed_domains_empty(repo: tuple) -> None:
    """get_allowed_domains returns empty list when no settings row exists."""
    repository, session = repo
    result = await repository.get_allowed_domains()
    assert result == []


@pytest.mark.asyncio
async def test_get_allowed_domains_with_data(repo: tuple) -> None:
    """get_allowed_domains returns existing domains."""
    from src.modules.recruitment.domain.entities import OrganizationSettings

    repository, session = repo
    session._settings = OrganizationSettings(
        timezone="Asia/Ho_Chi_Minh",
        allowed_domains=["company.vn", "subsidiary.vn"],
    )
    result = await repository.get_allowed_domains()
    assert result == ["company.vn", "subsidiary.vn"]


@pytest.mark.asyncio
async def test_set_allowed_domains(repo: tuple) -> None:
    """set_allowed_domains replaces the entire list."""
    repository, session = repo
    result = await repository.set_allowed_domains(["a.com", "b.com"])
    assert result == ["a.com", "b.com"]
    assert session._flushed is True


@pytest.mark.asyncio
async def test_add_domains(repo: tuple) -> None:
    """add_domains appends new domains."""
    repository, session = repo
    # First add some domains
    await repository.set_allowed_domains(["existing.vn"])
    # Reset flush flag
    session._flushed = False
    # Add more
    result = await repository.add_domains(["new.vn"])
    assert "existing.vn" in result
    assert "new.vn" in result


@pytest.mark.asyncio
async def test_add_domains_duplicate_rejected() -> None:
    """add_domains rejects duplicates."""
    session = FakeSession()
    from src.modules.recruitment.domain.entities import OrganizationSettings

    session._settings = OrganizationSettings(
        timezone="Asia/Ho_Chi_Minh",
        allowed_domains=["company.vn"],
    )
    repository = OrganizationSettingsRepository(session=session)
    with pytest.raises(ValueError, match="đã có trong danh sách"):
        await repository.add_domains(["company.vn"])


@pytest.mark.asyncio
async def test_remove_domain(repo: tuple) -> None:
    """remove_domain removes a specific domain."""
    repository, session = repo
    await repository.set_allowed_domains(["a.com", "b.com", "c.com"])
    session._flushed = False
    result = await repository.remove_domain("b.com")
    assert result == ["a.com", "c.com"]
    assert "b.com" not in result


@pytest.mark.asyncio
async def test_remove_domain_not_found() -> None:
    """remove_domain raises ValueError for non-existent domain."""
    session = FakeSession()
    from src.modules.recruitment.domain.entities import OrganizationSettings

    session._settings = OrganizationSettings(
        timezone="Asia/Ho_Chi_Minh",
        allowed_domains=["company.vn"],
    )
    repository = OrganizationSettingsRepository(session=session)
    with pytest.raises(ValueError, match="không tìm thấy"):
        await repository.remove_domain("other.vn")


@pytest.mark.asyncio
async def test_normalize_lowercase() -> None:
    """Domains are normalized to lowercase."""
    session = FakeSession()
    repository = OrganizationSettingsRepository(session=session)
    result = await repository.set_allowed_domains(["Company.VN", "  SUBSIDIARY.VN  "])
    assert result == ["company.vn", "subsidiary.vn"]


@pytest.mark.asyncio
async def test_invalid_domain_rejected() -> None:
    """Invalid domain format is rejected."""
    session = FakeSession()
    repository = OrganizationSettingsRepository(session=session)
    with pytest.raises(ValueError, match="không hợp lệ"):
        await repository.set_allowed_domains(["-invalid.vn"])


@pytest.mark.asyncio
async def test_too_many_domains_rejected() -> None:
    """Lists exceeding max limit are rejected."""
    session = FakeSession()
    repository = OrganizationSettingsRepository(session=session)
    domains = [f"d{i}.com" for i in range(51)]
    with pytest.raises(ValueError, match="Quá nhiều tên miền"):
        await repository.set_allowed_domains(domains)
