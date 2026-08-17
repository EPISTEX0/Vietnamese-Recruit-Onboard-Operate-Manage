"""Unit tests for ContextBuilder's session-owning helpers (#370).

Covers the behaviour ``_get_org_name`` and ``_get_employee_profile`` must
keep after their raw ``self._session.execute(...)`` calls were replaced with
repository calls: a missing row is a normal "not found" outcome, distinct
from -- and never conflated with -- a repository raising a real error.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.modules.assistant.application.context_builder import ContextBuilder


@pytest.mark.asyncio
async def test_get_org_name_returns_empty_string_when_repo_not_wired() -> None:
    builder = ContextBuilder(org_settings_repo=None)

    result = await builder.build_hr_context(user_query=None)

    assert "Tổ chức:" not in result


@pytest.mark.asyncio
async def test_get_org_name_reads_through_the_repository() -> None:
    org_settings_repo = AsyncMock()
    org_settings_repo.get_name = AsyncMock(return_value="Acme Corp")
    builder = ContextBuilder(org_settings_repo=org_settings_repo)

    result = await builder.build_hr_context(user_query=None)

    assert "Tổ chức: Acme Corp" in result


@pytest.mark.asyncio
async def test_get_org_name_degrades_gracefully_when_repo_raises() -> None:
    """A real repository error must not crash HR context building."""
    org_settings_repo = AsyncMock()
    org_settings_repo.get_name = AsyncMock(side_effect=RuntimeError("db down"))
    builder = ContextBuilder(org_settings_repo=org_settings_repo)

    result = await builder.build_hr_context(user_query=None)

    assert "Tổ chức:" not in result


def _mock_employee(**overrides) -> AsyncMock:
    employee = AsyncMock()
    employee.full_name = "Nguyễn Văn A"
    employee.department_id = None
    employee.position_id = None
    employee.employee_code = "NV-001"
    for key, value in overrides.items():
        setattr(employee, key, value)
    return employee


@pytest.mark.asyncio
async def test_employee_profile_skips_department_when_not_found() -> None:
    """A department lookup returning None (not found) is a normal outcome.

    The rest of the profile (name, employee code) must still be present --
    losing it would mean a not-found department silently nukes the whole
    profile, which is not the contract department_repo.get_by_id() promises.
    """
    dept_id = uuid4()
    employee_service = AsyncMock()
    employee_service.get_employee = AsyncMock(return_value=_mock_employee(department_id=dept_id))
    department_repo = AsyncMock()
    department_repo.get_by_id = AsyncMock(return_value=None)

    builder = ContextBuilder(
        employee_service=employee_service,
        department_repo=department_repo,
    )

    result = await builder.build_employee_context(employee_id=uuid4())

    assert "Nguyễn Văn A" in result
    assert "Mã NV: NV-001" in result
    assert "Phòng ban:" not in result


@pytest.mark.asyncio
async def test_employee_profile_includes_department_name_when_found() -> None:
    dept_id = uuid4()
    employee_service = AsyncMock()
    employee_service.get_employee = AsyncMock(return_value=_mock_employee(department_id=dept_id))
    department_repo = AsyncMock()
    department = AsyncMock()
    department.name = "Engineering"
    department_repo.get_by_id = AsyncMock(return_value=department)

    builder = ContextBuilder(
        employee_service=employee_service,
        department_repo=department_repo,
    )

    result = await builder.build_employee_context(employee_id=uuid4())

    assert "Phòng ban: Engineering" in result


@pytest.mark.asyncio
async def test_employee_profile_degrades_gracefully_when_department_lookup_raises() -> None:
    """A real department-lookup error must not crash employee context building.

    Unlike the removed ``_get_entity_name`` helper, the error is not
    swallowed at the lookup site itself -- it propagates out of
    department_repo.get_by_id() and is caught by this method's own
    try/except, same as any other unexpected failure while building the
    profile.
    """
    dept_id = uuid4()
    employee_service = AsyncMock()
    employee_service.get_employee = AsyncMock(return_value=_mock_employee(department_id=dept_id))
    department_repo = AsyncMock()
    department_repo.get_by_id = AsyncMock(side_effect=RuntimeError("db down"))

    builder = ContextBuilder(
        employee_service=employee_service,
        department_repo=department_repo,
    )

    result = await builder.build_employee_context(employee_id=uuid4())

    assert result == ""
