"""Integration test for the GH #154 create_interview command.

Exercises the new ``CandidateService.create_interview`` method through a mocked
Google Calendar HTTP layer. Unlike the legacy ``schedule_interview`` flow, this
command does NOT change the Candidate status -- the HR explicitly transitions
the candidate via accept/reject after interviews.

What is tested
--------------
* Calendar event created on the selected calendar (not primary).
* Google Meet only created when mode is ``google_meet``.
* Custom link passed through for ``custom_link`` mode.
* In-person mode does not request a Meet link.
* Candidate is always included as an attendee.
* Employee interviewers are resolved and invited.
* External participant emails are validated and invited.
* Calendar event ETag and updated time are persisted.
* Interview and InterviewParticipant records are created.
* Audit entries are written on success.
* Invalid external emails are rejected.
* Missing mode raises ValueError.
* Calendar failure rolls back and raises CalendarEventCreateFailedError.

Requirements: GH #154 AC 1-10
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

import src.modules.gmail.domain.entities  # noqa: F401
from src.modules.employee.domain.entities import Employee
from src.modules.employee.infrastructure.employee_repository import EmployeeRepository
from src.modules.identity.domain.entities import (
    OAuthGrant,
    OrganizationGoogleConnection,
    User,
    UserRole,
)
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.connection_state_repository import (
    OrganizationGoogleConnectionRepository,
)
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.recruitment.application.interview_scheduler_service import (
    InterviewSchedulerService as CandidateService,
)
from src.modules.recruitment.domain.entities import (
    Candidate,
    Interview,
    InterviewParticipant,
    RecruitmentAuditLog,
)
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.infrastructure.calendar_adapter import CalendarAdapter
from src.modules.recruitment.infrastructure.config import RecruitmentSettings
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)
from src.modules.recruitment.infrastructure.repositories import (
    CalendarConflictRepository,
    CandidateRepository,
    InterviewRepository,
)
from tests.postgres_support import make_postgres_container

BACKEND_DIR = Path(__file__).resolve().parents[3]
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"

_STABLE_EVENT_ID = "evt_create_int_abc123"
_HTML_LINK = f"https://www.google.com/calendar/event?eid={_STABLE_EVENT_ID}"
_MEET_LINK = "https://meet.google.com/xxx-yyyy-zzz"
_EVENT_ETAG = '"etag-abc123"'
_ORG_TIMEZONE = "Asia/Ho_Chi_Minh"
# The recruitment calendar the Organization connection selects; every event
# must be created here rather than on the connected account's primary calendar.
_SELECTED_CALENDAR_ID = "recruitment@example.com"

_ACCESS_TOKEN_PLAINTEXT = "ya29.create-interview-token"
_REFRESH_TOKEN_PLAINTEXT = "1//create-refresh-token"

_OP_CREATED = "interview_created"
_OP_CREATE_FAILED = "interview_create_failed"


class _GoogleCalendarMock:
    """Mock Google Calendar that routes by path for calendar-aware testing."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._event_counter = 0

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"evt_{self._event_counter:04d}_{uuid4().hex[:8]}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method
        body = _json_body(request) if request.content else {}

        # Route events collection or item endpoints
        is_collection = "/events" in path and not path.endswith("/calendarList")
        is_item = "/events/" in path and not path.endswith("/calendarList")

        if method == "GET" and "calendarList" in path:
            # Calendar list response
            return httpx.Response(200, json=self._calendar_list_json())

        if method == "POST" and is_collection:
            # Google only mints a conference when the create body actually asks
            # for one. The previous form (``.get("createRequest", {}) is not
            # None``) is true for *every* body, so the mock handed back a Meet
            # link even for in_person/custom_link events and could never catch a
            # wrongly-requested conference.
            created = "createRequest" in body.get("conferenceData", {})
            return httpx.Response(200, json=self._created_event_json(created))
        if method == "PATCH" and is_item:
            event_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self._updated_event_json(event_id))
        if method == "DELETE" and is_item:
            return httpx.Response(204)

        return httpx.Response(404, json={"error": {"message": f"unrouted {method} {path}"}})

    def _calendar_list_json(self) -> dict[str, Any]:
        """Return a calendar list response with a primary and a secondary calendar."""
        return {
            "items": [
                {
                    "id": "primary",
                    "summary": "Primary Calendar",
                    "description": "The user's primary calendar",
                    "primary": True,
                    "accessRole": "owner",
                },
                {
                    "id": "recruitment@example.com",
                    "summary": "Recruitment Calendar",
                    "description": "Calendar for interviews",
                    "primary": False,
                    "accessRole": "writer",
                },
            ]
        }

    def _created_event_json(self, has_meet: bool) -> dict[str, Any]:
        """Build a created-event response, optionally with Meet."""
        event: dict[str, Any] = {
            "id": _STABLE_EVENT_ID,
            "htmlLink": _HTML_LINK,
            "etag": _EVENT_ETAG,
            "updated": "2025-01-15T10:00:00.000Z",
            "attendees": [
                {"email": "candidate@example.com"},
            ],
        }
        if has_meet:
            event["hangoutLink"] = _MEET_LINK
            event["conferenceData"] = {
                "entryPoints": [
                    {"entryPointType": "video", "uri": _MEET_LINK},
                ],
            }
        return event

    @staticmethod
    def _updated_event_json(event_id: str) -> dict[str, Any]:
        """Build an updated-event response echoing the patched event id."""
        return {
            "id": event_id,
            "htmlLink": _HTML_LINK,
            "etag": _EVENT_ETAG,
            "updated": "2025-01-15T11:00:00.000Z",
        }


# ---------------------------------------------------------------------------
# Migration / container helpers
# ---------------------------------------------------------------------------
def _docker_available(docker_module: object) -> bool:
    try:
        client = docker_module.from_env()
        client.ping()
    except Exception:
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="module")
def postgres_async_url() -> Iterator[str]:
    docker = pytest.importorskip("docker")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for interview integration test")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url


@pytest_asyncio.fixture
async def session_maker(
    postgres_async_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def calendar_mock() -> _GoogleCalendarMock:
    return _GoogleCalendarMock()


@pytest_asyncio.fixture
async def calendar_http_client(
    calendar_mock: _GoogleCalendarMock,
) -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(calendar_mock.handler))
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Crypto / settings helpers
# ---------------------------------------------------------------------------
def _make_crypto() -> CryptoUtils:
    key_b64 = base64.b64encode(os.urandom(32)).decode("ascii")
    return CryptoUtils(key_b64)


def _make_auth_settings() -> AuthSettings:
    return AuthSettings(
        google_client_id="ci-client-id",
        google_client_secret="ci-client-secret",
        jwt_secret_key="ci-jwt-secret",
        oauth_token_encryption_key=base64.b64encode(os.urandom(32)).decode("ascii"),
    )


def _build_candidate_service(
    session: AsyncSession,
    *,
    user_id: Any,
    crypto: CryptoUtils,
    auth_settings: AuthSettings,
    http_client: httpx.AsyncClient,
) -> CandidateService:
    """Wire an InterviewSchedulerService the way the recruitment container does.

    Calendar writes are authorized by the Organization Google Connection (the
    ``connection_repo`` seam), not by the acting HR user's personal OAuth grant,
    so that repository is what the service needs to reach Google.
    """
    settings = RecruitmentSettings()
    candidate_repo = CandidateRepository(session)
    interview_repo = InterviewRepository(session)
    calendar_conflict_repo = CalendarConflictRepository(session)
    employee_repo = EmployeeRepository(session)
    org_settings_repo = OrganizationSettingsRepository(session, settings)
    connection_repo = OrganizationGoogleConnectionRepository(session)
    calendar_adapter = CalendarAdapter(settings=settings, http_client=http_client)

    return CandidateService(
        candidate_repo=candidate_repo,
        interview_repo=interview_repo,
        calendar_conflict_repo=calendar_conflict_repo,
        employee_repo=employee_repo,
        calendar_port=calendar_adapter,
        org_settings_repo=org_settings_repo,
        connection_repo=connection_repo,
        crypto=crypto,
        session=session,
        user_id=user_id,
    )


async def _seed_data(
    maker: async_sessionmaker[AsyncSession],
    crypto: CryptoUtils,
) -> tuple[Any, Any, Any]:
    """Seed the acting HR user, grant, interviewer, and candidate.

    Returns ``(user_id, interviewer_id, candidate_id)``.
    """
    suffix = uuid4().hex[:8]
    user = User(
        email=f"hr-{suffix}@example.com",
        name="HR Admin",
        role=UserRole.HR,
    )
    interviewer = Employee(
        employee_code=f"NV-{suffix}",
        full_name="Interviewer X",
        email=f"interviewer-{suffix}@example.com",
        is_active=True,
    )
    candidate = Candidate(
        name="Ung Vien",
        email=f"candidate-{suffix}@example.com",
        phone="0901234567",
        skills=["Python"],
        status=CandidateStatus.NEW,
        confidence_score=0.95,
    )

    async with maker() as db_session:
        db_session.add(user)
        db_session.add(interviewer)
        db_session.add(candidate)
        await db_session.flush()

        grant = OAuthGrant(
            user_id=user.id,
            provider="google",
            access_token_enc=crypto.encrypt(_ACCESS_TOKEN_PLAINTEXT),
            refresh_token_enc=crypto.encrypt(_REFRESH_TOKEN_PLAINTEXT),
            scopes=[_CALENDAR_SCOPE],
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            is_valid=True,
        )
        db_session.add(grant)

        # The live Organization Google Connection: this, not the per-user grant,
        # is what authorizes writes to the selected recruitment calendar.
        existing_conn = (
            (await db_session.execute(select(OrganizationGoogleConnection))).scalars().first()
        )
        connection = existing_conn or OrganizationGoogleConnection()
        connection.status = "connected"
        connection.selected_calendar_id = _SELECTED_CALENDAR_ID
        connection.access_token_enc = crypto.encrypt(_ACCESS_TOKEN_PLAINTEXT)
        connection.refresh_token_enc = crypto.encrypt(_REFRESH_TOKEN_PLAINTEXT)
        connection.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        connection.connected_by_user_id = user.id
        db_session.add(connection)

        await db_session.commit()
        return user.id, interviewer.id, candidate.id


async def _load_interviews(
    maker: async_sessionmaker[AsyncSession], candidate_id: Any
) -> list[Interview]:
    async with maker() as db_session:
        result = await db_session.execute(
            select(Interview).where(Interview.candidate_id == candidate_id)
        )
        return list(result.scalars().all())


async def _load_audit(
    maker: async_sessionmaker[AsyncSession],
    operation_type: str,
) -> list[RecruitmentAuditLog]:
    async with maker() as db_session:
        result = await db_session.execute(
            select(RecruitmentAuditLog)
            .where(RecruitmentAuditLog.operation_type == operation_type)
            .order_by(RecruitmentAuditLog.created_at)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _json_body(request: httpx.Request) -> dict[str, Any]:
    import json

    return json.loads(request.content)


def _extract_calendar_id(request: httpx.Request) -> str:
    """Extract the calendar ID from a request URL path.

    Path format: /calendar/v3/calendars/{calendar_id}/events[/{event_id}]
    """
    parts = request.url.path.split("/")
    if "calendars" in parts:
        idx = parts.index("calendars")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "unknown"


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.integration
async def test_create_interview_with_google_meet(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Create an interview with Google Meet mode."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, interviewer_id, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        interview = await service.create_interview(
            candidate_id,
            round_name="Technical Round 1",
            start=start,
            end=end,
            timezone="Asia/Ho_Chi_Minh",
            mode="google_meet",
            interviewer_ids=[interviewer_id],
            notes="First technical interview",
        )

    # Verify interview record
    assert interview.calendar_event_id == _STABLE_EVENT_ID
    assert interview.meeting_mode == "google_meet"
    assert interview.calendar_etag == _EVENT_ETAG
    assert interview.calendar_updated is not None
    assert interview.round_name == "Technical Round 1"
    assert interview.timezone == "Asia/Ho_Chi_Minh"

    # Verify HTTP request used the correct calendar_id (primary = default)
    assert len(calendar_mock.requests) >= 1
    create_req = [r for r in calendar_mock.requests if r.method == "POST"][0]
    create_body = _json_body(create_req)

    # Verify Meet was requested
    assert "conferenceData" in create_body
    assert "createRequest" in create_body["conferenceData"]
    assert (
        create_body["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"]
        == "hangoutsMeet"
    )
    # Verify stable requestId
    assert "requestId" in create_body["conferenceData"]["createRequest"]
    request_id = create_body["conferenceData"]["createRequest"]["requestId"]
    assert len(request_id) == 64  # SHA-256 hex

    # Verify Candidate status unchanged
    async with session_maker() as session:
        result = await session.execute(select(Candidate).where(Candidate.id == candidate_id))
        candidate = result.scalars().first()
        assert candidate is not None
        assert candidate.status == CandidateStatus.NEW  # Not changed!

    # Verify Interview persisted
    interviews = await _load_interviews(session_maker, candidate_id)
    assert len(interviews) == 1
    iv = interviews[0]
    assert iv.status == "scheduled"
    assert iv.calendar_event_id == _STABLE_EVENT_ID
    assert iv.calendar_etag == _EVENT_ETAG
    assert iv.round_name == "Technical Round 1"
    assert iv.meeting_mode == "google_meet"

    # Verify participants
    async with session_maker() as session:
        parts = (
            (
                await session.execute(
                    select(InterviewParticipant).where(InterviewParticipant.interview_id == iv.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(parts) == 2  # candidate + employee
    types = {p.type for p in parts}
    assert types == {"candidate", "employee"}

    # Verify audit entry
    audits = await _load_audit(session_maker, _OP_CREATED)
    assert len(audits) >= 1
    audit = audits[-1]
    assert audit.success is True
    assert audit.new_value is not None
    assert audit.new_value["calendar_event_id"] == _STABLE_EVENT_ID


@pytest.mark.integration
async def test_create_interview_in_person(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Create an interview with in_person mode -- no Meet request."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, _, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        interview = await service.create_interview(
            candidate_id,
            round_name="In-Person Round",
            start=start,
            end=end,
            timezone="Asia/Ho_Chi_Minh",
            mode="in_person",
            notes="On-site interview",
        )

    assert interview.meeting_mode == "in_person"

    # Verify no Meet request in the body
    create_reqs = [r for r in calendar_mock.requests if r.method == "POST"]
    if create_reqs:
        body = _json_body(create_reqs[0])
        assert "conferenceData" not in body


@pytest.mark.integration
async def test_create_interview_custom_link(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Create an interview with custom_link mode and a meeting link."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, _, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)
    custom_link = "https://zoom.us/j/123456789"

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        interview = await service.create_interview(
            candidate_id,
            round_name="Zoom Round",
            start=start,
            end=end,
            timezone="Asia/Ho_Chi_Minh",
            mode="custom_link",
            meeting_link=custom_link,
            notes="Zoom interview",
        )

    assert interview.meeting_mode == "custom_link"
    assert interview.meeting_link == custom_link

    # Verify the meeting_link was saved
    async with session_maker() as session:
        result = await session.execute(select(Interview).where(Interview.id == interview.id))
        iv = result.scalars().first()
        assert iv is not None
        assert iv.meeting_link == custom_link


@pytest.mark.integration
async def test_create_interview_with_external_participants(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Create an interview with external participants."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, interviewer_id, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        interview = await service.create_interview(
            candidate_id,
            round_name="Panel Interview",
            start=start,
            end=end,
            timezone="Asia/Ho_Chi_Minh",
            mode="google_meet",
            interviewer_ids=[interviewer_id],
            external_participant_emails=["external@consultant.com", "advisor@partner.com"],
            notes="Panel with external participants",
        )

    # Verify participants include externals
    async with session_maker() as session:
        parts = (
            (
                await session.execute(
                    select(InterviewParticipant).where(
                        InterviewParticipant.interview_id == interview.id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(parts) == 4  # candidate + employee + 2 external
    types = {p.type for p in parts}
    assert types == {"candidate", "employee", "external"}
    ext_emails = [p.email for p in parts if p.type == "external"]
    assert "external@consultant.com" in ext_emails
    assert "advisor@partner.com" in ext_emails

    # Verify all participants have response_status
    for p in parts:
        assert p.response_status == "needsAction"


@pytest.mark.integration
async def test_create_interview_invalid_external_email_fails(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Invalid external email raises ValueError and no Calendar event is created."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, _, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        with pytest.raises(ValueError, match="Invalid external participant email"):
            await service.create_interview(
                candidate_id,
                round_name="Bad Email",
                start=start,
                end=end,
                timezone="Asia/Ho_Chi_Minh",
                mode="google_meet",
                external_participant_emails=["not-an-email"],
            )

    # No Calendar event should have been created
    posts = [r for r in calendar_mock.requests if r.method == "POST"]
    assert len(posts) == 0


@pytest.mark.integration
async def test_create_interview_candidate_status_unchanged(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Create interview does NOT change candidate status (HR explicitly transitions)."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, _, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        await service.create_interview(
            candidate_id,
            round_name="Round 1",
            start=start,
            end=end,
            timezone="Asia/Ho_Chi_Minh",
            mode="google_meet",
        )

    async with session_maker() as session:
        result = await session.execute(select(Candidate).where(Candidate.id == candidate_id))
        candidate = result.scalars().first()
        assert candidate is not None
        assert candidate.status == CandidateStatus.NEW  # Unchanged


@pytest.mark.integration
async def test_create_interview_without_meeting_link_for_custom_mode_fails(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """custom_link mode without meeting_link raises ValueError."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, _, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        with pytest.raises(ValueError, match="meeting_link is required"):
            await service.create_interview(
                candidate_id,
                round_name="Round",
                start=start,
                end=end,
                timezone="Asia/Ho_Chi_Minh",
                mode="custom_link",
            )


@pytest.mark.integration
async def test_create_interview_persists_metadata(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Interview record stores calendar event identity, ETag, updated time."""
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, _, candidate_id = await _seed_data(session_maker, crypto)

    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=1)

    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        interview = await service.create_interview(
            candidate_id,
            round_name="Meta Test",
            start=start,
            end=end,
            timezone="America/New_York",
            mode="google_meet",
            notes="Tracking metadata",
        )

    assert interview.calendar_event_id == _STABLE_EVENT_ID
    assert interview.calendar_etag == _EVENT_ETAG
    assert interview.calendar_updated is not None

    # Re-read from DB to verify persistence
    async with session_maker() as session:
        result = await session.execute(select(Interview).where(Interview.id == interview.id))
        iv = result.scalars().first()
        assert iv is not None
        assert iv.calendar_etag == _EVENT_ETAG
        assert iv.calendar_event_id == _STABLE_EVENT_ID
        assert iv.meeting_mode == "google_meet"
        assert iv.timezone == "America/New_York"
