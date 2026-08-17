"""Unit tests for the bulk/filtered lookup methods added to employee repositories (#370).

These back the ``tool_registry.py`` and ``context_builder.py`` Read-Tools that
used to run raw ``self._session.execute(...)`` queries directly; the methods
here are what those call sites were rewritten to use instead.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.employee.domain.entities import Department, Employee, Position
from src.modules.employee.infrastructure.department_repository import DepartmentRepository
from src.modules.employee.infrastructure.employee_repository import EmployeeRepository
from src.modules.employee.infrastructure.position_repository import PositionRepository


def _session_returning(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _session_returning_scalar(value: int) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_department_get_by_ids_returns_empty_dict_for_empty_input() -> None:
    session = MagicMock()
    session.execute = AsyncMock()

    found = await DepartmentRepository(session).get_by_ids([])

    assert found == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_department_get_by_ids_maps_id_to_department() -> None:
    dept_a = Department(id=uuid4(), name="Engineering")
    dept_b = Department(id=uuid4(), name="Sales")
    session = _session_returning([dept_a, dept_b])

    found = await DepartmentRepository(session).get_by_ids([dept_a.id, dept_b.id])

    assert found == {dept_a.id: dept_a, dept_b.id: dept_b}


@pytest.mark.asyncio
async def test_position_get_by_ids_returns_empty_dict_for_empty_input() -> None:
    session = MagicMock()
    session.execute = AsyncMock()

    found = await PositionRepository(session).get_by_ids([])

    assert found == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_position_get_by_ids_maps_id_to_position() -> None:
    dept_id = uuid4()
    pos_a = Position(id=uuid4(), name="Engineer", department_id=dept_id)
    pos_b = Position(id=uuid4(), name="Manager", department_id=dept_id)
    session = _session_returning([pos_a, pos_b])

    found = await PositionRepository(session).get_by_ids([pos_a.id, pos_b.id])

    assert found == {pos_a.id: pos_a, pos_b.id: pos_b}


@pytest.mark.asyncio
async def test_position_list_by_department_returns_matching_positions() -> None:
    dept_id = uuid4()
    positions = [Position(id=uuid4(), name="Engineer", department_id=dept_id)]
    session = _session_returning(positions)

    found = await PositionRepository(session).list_by_department(dept_id)

    assert found == positions


@pytest.mark.asyncio
async def test_employee_count_by_position_returns_scalar_count() -> None:
    session = _session_returning_scalar(3)

    count = await EmployeeRepository(session).count_by_position(uuid4())

    assert count == 3


@pytest.mark.asyncio
async def test_employee_count_by_position_defaults_to_zero() -> None:
    session = _session_returning_scalar(0)

    count = await EmployeeRepository(session).count_by_position(uuid4())

    assert count == 0


@pytest.mark.asyncio
async def test_employee_list_by_department_includes_inactive() -> None:
    """No is_active filter -- unlike list(), every employee in the department is returned."""
    dept_id = uuid4()
    employees = [
        Employee(
            id=uuid4(),
            employee_code="NV-001",
            full_name="Active",
            email="active@example.com",
            department_id=dept_id,
            is_active=True,
        ),
        Employee(
            id=uuid4(),
            employee_code="NV-002",
            full_name="Inactive",
            email="inactive@example.com",
            department_id=dept_id,
            is_active=False,
        ),
    ]
    session = _session_returning(employees)

    found = await EmployeeRepository(session).list_by_department(dept_id)

    assert found == employees
