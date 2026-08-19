"""Round-trip tests for every ``Enum``-annotated column backed by ``VARCHAR``/``TEXT``.

Commit 0e485dc fixed ``users.role``: the field was annotated ``UserRole`` but
mapped with a bare ``Column(String(10))``, so SQLAlchemy had no type
information to rebuild the member on read and handed back a plain ``str``.
Nine sibling columns across five modules carried the identical defect. Because
every one of these enums subclasses ``str``, all ``==`` comparisons kept
working and the whole class of bug stayed invisible -- until something touched
``.value`` and got ``AttributeError``.

Proving it requires a real load from PostgreSQL. A test that builds an entity
in memory holds whatever the constructor was handed -- always a genuine enum
member -- which is exactly why the existing unit suites for these modules
(``test_review_service.py`` mocks the repository with in-memory rows) stayed
green while ``POST /api/employee-requests/{id}/approve`` returned 500 in
production. So each test here writes the row, calls ``expunge_all`` to drop it
from the identity map, and reads it back through SQLAlchemy's result
processing.

The two service-level regressions live in this module rather than beside their
own unit suites because they exercise the same defect on the same rows; the
``postgres_async_url`` container they run against is session-scoped in
``tests/conftest.py`` and shared with every other database round-trip test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.assistant.application.employee_tool_registry import EmployeeToolRegistry
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantFeedbackEvent,
    AssistantType,
    FeedbackType,
)
from src.modules.attendance.domain.entities import AttendanceRecord, AttendanceSource
from src.modules.employee.domain.entities import Employee
from src.modules.employee_request.application.leave_service import LeaveService
from src.modules.employee_request.application.overtime_service import OvertimeService
from src.modules.employee_request.application.review_service import (
    EmployeeRequestReviewService,
)
from src.modules.employee_request.domain.entities import EmployeeRequest
from src.modules.employee_request.domain.enums import LeaveType, RequestStatus, RequestType
from src.modules.employee_request.domain.exceptions import RequestNotReviewableError
from src.modules.employee_request.infrastructure.employee_request_repository import (
    EmployeeRequestRepository,
)
from src.modules.identity.domain.entities import (
    AuditActionType,
    AuditLog,
    User,
    UserRole,
)
from src.modules.payslip.domain.entities import Payslip, PayslipStatus


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """Provide a fresh async session per test, rolled back on teardown."""
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def _make_user(db_session: AsyncSession, role: UserRole = UserRole.HR) -> User:
    """Insert one user, needed as the FK parent for several tables."""
    suffix = uuid4().hex[:12]
    user = User(
        email=f"enum-roundtrip-{suffix}@example.com",
        name="Enum Round-trip User",
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_employee(db_session: AsyncSession) -> Employee:
    """Insert one employee, needed as the FK parent for several tables."""
    suffix = uuid4().hex[:10]
    employee = Employee(
        employee_code=f"NV-{suffix[:8]}",
        full_name="Enum Round-trip Employee",
        email=f"enum-emp-{suffix}@example.com",
    )
    db_session.add(employee)
    await db_session.flush()
    return employee


async def _reload(db_session: AsyncSession, model: type, pk: UUID) -> Any:
    """Re-read a row straight from PostgreSQL, bypassing the identity map.

    ``expunge_all`` is the entire point: without it the session hands back the
    very object the test constructed, whose enum field never passed through
    SQLAlchemy's result processing and so proves nothing.
    """
    db_session.expunge_all()
    result = await db_session.execute(select(model).where(model.id == pk))
    return result.scalars().one()


class TestEnumColumnsLoadBackAsEnums:
    """Every enum-annotated column must load back as its enum, not ``str``."""

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(RequestType))
    async def test_employee_request_request_type(
        self, session: AsyncSession, member: RequestType
    ) -> None:
        """``employee_requests.request_type`` (Column(Text)) round-trips."""
        employee = await _make_employee(session)
        row = EmployeeRequest(employee_id=employee.id, request_type=member)
        session.add(row)
        await session.flush()

        loaded = await _reload(session, EmployeeRequest, row.id)

        assert isinstance(loaded.request_type, RequestType), (
            f"request_type loaded as {type(loaded.request_type).__name__}; "
            "every .value access on it raises AttributeError."
        )
        assert loaded.request_type is member

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(RequestStatus))
    async def test_employee_request_status(
        self, session: AsyncSession, member: RequestStatus
    ) -> None:
        """``employee_requests.status`` (Column(Text)) round-trips."""
        employee = await _make_employee(session)
        row = EmployeeRequest(
            employee_id=employee.id, request_type=RequestType.LEAVE, status=member
        )
        session.add(row)
        await session.flush()

        loaded = await _reload(session, EmployeeRequest, row.id)

        assert isinstance(loaded.status, RequestStatus)
        assert loaded.status is member

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(LeaveType))
    async def test_employee_request_leave_type(
        self, session: AsyncSession, member: LeaveType
    ) -> None:
        """``employee_requests.leave_type`` (nullable Column(Text)) round-trips."""
        employee = await _make_employee(session)
        row = EmployeeRequest(
            employee_id=employee.id, request_type=RequestType.LEAVE, leave_type=member
        )
        session.add(row)
        await session.flush()

        loaded = await _reload(session, EmployeeRequest, row.id)

        assert isinstance(loaded.leave_type, LeaveType)
        assert loaded.leave_type is member

    @pytest.mark.integration
    async def test_employee_request_leave_type_stays_none(self, session: AsyncSession) -> None:
        """A NULL ``leave_type`` must stay ``None``, not become an enum member.

        ``leave_type`` is the only nullable column in this set, so it is the
        one place a ``TypeDecorator`` that forgot its ``None`` guard would turn
        a missing value into a spurious member.
        """
        employee = await _make_employee(session)
        row = EmployeeRequest(employee_id=employee.id, request_type=RequestType.OVERTIME)
        session.add(row)
        await session.flush()

        loaded = await _reload(session, EmployeeRequest, row.id)

        assert loaded.leave_type is None

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(AttendanceSource))
    async def test_attendance_record_source(
        self, session: AsyncSession, member: AttendanceSource
    ) -> None:
        """``attendance_records.source`` round-trips."""
        employee = await _make_employee(session)
        row = AttendanceRecord(employee_id=employee.id, work_date=date(2026, 8, 13), source=member)
        session.add(row)
        await session.flush()

        loaded = await _reload(session, AttendanceRecord, row.id)

        assert isinstance(loaded.source, AttendanceSource)
        assert loaded.source is member

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(PayslipStatus))
    async def test_payslip_status(self, session: AsyncSession, member: PayslipStatus) -> None:
        """``payslips.status`` round-trips.

        ``published_at`` tracks the status because
        ``ck_payslips_status_published_at_consistent`` rejects a published
        payslip with no publication timestamp.
        """
        employee = await _make_employee(session)
        row = Payslip(
            employee_id=employee.id,
            period_month=date(2026, 8, 1),
            gross_salary=Decimal("1000.00"),
            net_salary=Decimal("900.00"),
            status=member,
            published_at=(datetime.now(UTC) if member is PayslipStatus.PUBLISHED else None),
        )
        session.add(row)
        await session.flush()

        loaded = await _reload(session, Payslip, row.id)

        assert isinstance(loaded.status, PayslipStatus)
        assert loaded.status is member

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(AuditActionType))
    async def test_audit_log_action_type(
        self, session: AsyncSession, member: AuditActionType
    ) -> None:
        """``audit_logs.action_type`` round-trips for every one of its 34 members.

        Parametrised over the whole enum rather than a sample because
        ``audit_logs`` is append-only history: a member that cannot be read
        back breaks every ``GET`` of the audit trail, not just its own row.
        """
        user = await _make_user(session)
        row = AuditLog(admin_user_id=user.id, admin_email=user.email, action_type=member)
        session.add(row)
        await session.flush()

        loaded = await _reload(session, AuditLog, row.id)

        assert isinstance(loaded.action_type, AuditActionType)
        assert loaded.action_type is member

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(AssistantType))
    async def test_assistant_chat_session_assistant_type(
        self, session: AsyncSession, member: AssistantType
    ) -> None:
        """``assistant_chat_sessions.assistant_type`` round-trips."""
        user = await _make_user(session)
        row = AssistantChatSession(user_id=user.id, assistant_type=member)
        session.add(row)
        await session.flush()

        loaded = await _reload(session, AssistantChatSession, row.id)

        assert isinstance(loaded.assistant_type, AssistantType)
        assert loaded.assistant_type is member

    @pytest.mark.integration
    @pytest.mark.parametrize("member", list(FeedbackType))
    async def test_assistant_feedback_event_feedback_type(
        self, session: AsyncSession, member: FeedbackType
    ) -> None:
        """``assistant_feedback_events.feedback_type`` round-trips.

        The real column is ``VARCHAR(4)`` and ``'down'`` is exactly 4
        characters, so this is also the width check for the tightest column in
        the set.
        """
        user = await _make_user(session)
        chat = AssistantChatSession(user_id=user.id, assistant_type=AssistantType.EMPLOYEE)
        session.add(chat)
        await session.flush()
        row = AssistantFeedbackEvent(session_id=chat.id, message_index=0, feedback_type=member)
        session.add(row)
        await session.flush()

        loaded = await _reload(session, AssistantFeedbackEvent, row.id)

        assert isinstance(loaded.feedback_type, FeedbackType)
        assert loaded.feedback_type is member


class TestReviewServiceOnDatabaseLoadedRequests:
    """``EmployeeRequestReviewService`` must survive a request loaded from the DB.

    The service reads ``request.status.value`` and ``request.request_type.value``
    off the row returned by ``get_by_id_with_lock`` -- a genuine ``SELECT ... FOR
    UPDATE``, so the values came through SQLAlchemy's result processing. The
    existing unit tests pass an ``AsyncMock`` repository that returns in-memory
    entities, so they never exercised the loaded type.
    """

    @staticmethod
    async def _submitted_request(db_session: AsyncSession) -> tuple[EmployeeRequest, User]:
        """Insert one SUBMITTED overtime request and an HR reviewer."""
        employee = await _make_employee(db_session)
        reviewer = await _make_user(db_session, UserRole.HR)
        request = EmployeeRequest(
            employee_id=employee.id,
            request_type=RequestType.OVERTIME,
            status=RequestStatus.SUBMITTED,
            work_date=date(2026, 8, 13),
            start_time=time(18, 0),
            end_time=time(20, 0),
            duration_minutes=120,
            reason="Enum round-trip",
        )
        db_session.add(request)
        await db_session.flush()
        db_session.expunge_all()
        return request, reviewer

    @pytest.mark.integration
    async def test_approve_submitted_request_succeeds(self, session: AsyncSession) -> None:
        """Approving a SUBMITTED request must not raise.

        The audit-log payload reads ``request.request_type.value`` on the happy
        path, so this fails even when the status guard is satisfied.
        """
        request, reviewer = await self._submitted_request(session)
        service = EmployeeRequestReviewService(
            repo=EmployeeRequestRepository(session), audit_service=AsyncMock()
        )

        updated = await service.approve_request(request.id, reviewer, "ok")

        assert updated.status is RequestStatus.APPROVED

    @pytest.mark.integration
    async def test_second_approve_raises_business_error_not_attribute_error(
        self, session: AsyncSession
    ) -> None:
        """Approving twice must raise the business error, not ``AttributeError``.

        This is the concurrency case ``get_by_id_with_lock`` exists to handle
        -- a double submit, or two HR reviewers on one request. The second call
        formats the rejected status with ``.value``, which is where a
        ``str``-typed column turns a 400 into a 500.
        """
        request, reviewer = await self._submitted_request(session)
        service = EmployeeRequestReviewService(
            repo=EmployeeRequestRepository(session), audit_service=AsyncMock()
        )
        await service.approve_request(request.id, reviewer, "first")
        session.expunge_all()

        with pytest.raises(RequestNotReviewableError) as exc_info:
            await service.approve_request(request.id, reviewer, "second")

        assert "approved" in str(exc_info.value)

    @pytest.mark.integration
    async def test_second_reject_raises_business_error_not_attribute_error(
        self, session: AsyncSession
    ) -> None:
        """Rejecting an already-rejected request must raise the business error."""
        request, reviewer = await self._submitted_request(session)
        service = EmployeeRequestReviewService(
            repo=EmployeeRequestRepository(session), audit_service=AsyncMock()
        )
        await service.reject_request(request.id, reviewer, "first")
        session.expunge_all()

        with pytest.raises(RequestNotReviewableError) as exc_info:
            await service.reject_request(request.id, reviewer, "second")

        assert "rejected" in str(exc_info.value)


class TestEmployeeToolRegistryOnDatabaseLoadedRequests:
    """The assistant tool must serialise a DB-loaded request's status.

    ``_list_my_employee_requests`` guards the access with
    ``hasattr(leave, "status")``, which cannot help: the attribute is present,
    it just holds a ``str``, so ``hasattr`` returns ``True`` and ``.value``
    raises anyway.
    """

    @pytest.mark.integration
    async def test_list_my_employee_requests_serialises_status(self, session: AsyncSession) -> None:
        """Listing an employee's requests must return the status string."""
        employee = await _make_employee(session)
        session.add(
            EmployeeRequest(
                employee_id=employee.id,
                request_type=RequestType.LEAVE,
                status=RequestStatus.SUBMITTED,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 8, 13),
                end_date=date(2026, 8, 14),
                reason="Enum round-trip",
            )
        )
        session.add(
            EmployeeRequest(
                employee_id=employee.id,
                request_type=RequestType.OVERTIME,
                status=RequestStatus.APPROVED,
                work_date=date(2026, 8, 13),
                start_time=time(18, 0),
                end_time=time(20, 0),
                duration_minutes=120,
                reason="Enum round-trip",
            )
        )
        await session.flush()
        session.expunge_all()

        repo = EmployeeRequestRepository(session)
        registry = EmployeeToolRegistry(
            employee_id=employee.id,
            employee_service=AsyncMock(),
            document_service=AsyncMock(),
            attendance_repo=AsyncMock(),
            leave_service=LeaveService(repo=repo),
            overtime_service=OvertimeService(repo=repo),
            payslip_service=AsyncMock(),
        )

        result = await registry.execute("list_my_employee_requests", {})

        assert '"status": "submitted"' in result
        assert '"status": "approved"' in result


class TestEnumColumnDdlIsUnchanged:
    """The mapped columns must stay the exact SQL types they were.

    ``EnumAsString`` is only migration-free while it keeps emitting the same
    DDL. If a column's mapped type drifts, the next autogenerate proposes an
    ``ALTER`` -- so pin the type name and width against the deployed schema.
    """

    @pytest.mark.parametrize(
        ("model", "column", "sql_type", "length"),
        [
            (EmployeeRequest, "request_type", "TEXT", None),
            (EmployeeRequest, "status", "TEXT", None),
            (EmployeeRequest, "leave_type", "TEXT", None),
            (AttendanceRecord, "source", "VARCHAR", 20),
            (Payslip, "status", "VARCHAR", 10),
            (AuditLog, "action_type", "VARCHAR", 50),
            (AssistantChatSession, "assistant_type", "VARCHAR", 10),
            (AssistantFeedbackEvent, "feedback_type", "VARCHAR", 4),
        ],
    )
    def test_mapped_column_type_matches_the_deployed_schema(
        self, model: type, column: str, sql_type: str, length: int | None
    ) -> None:
        """Each converted column keeps its original SQL type and width."""
        mapped = model.__table__.c[column]

        assert str(mapped.type).split("(")[0] == sql_type
        assert getattr(mapped.type, "length", None) == length
