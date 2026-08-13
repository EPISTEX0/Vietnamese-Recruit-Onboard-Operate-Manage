"""End-to-end integration test for interview calendar scheduling (task 10.4).

This is an INTEGRATION test that exercises the *real* wiring of the
interview-calendar feature as much as practical: it drives a single
schedule -> reschedule -> reject sequence through the wired
:class:`CandidateService` and asserts the persisted ``Candidate`` state and the
``recruitment_audit_logs`` rows at every step.

What is REAL here
-----------------
* The Google Calendar HTTP layer is the production :class:`CalendarAdapter`
  built over an ``httpx.AsyncClient(transport=httpx.MockTransport(handler))`` --
  i.e. the real adapter (request construction, retry/backoff, response parsing,
  401 handling, 204/404/410 delete idempotency) runs unchanged; only the
  network is mocked. This is the point of the task.
* The database is a real PostgreSQL 15 (via ``testcontainers`` + ``alembic
  upgrade head``), so the real :class:`CandidateRepository`,
  :class:`OrganizationSettingsRepository`, :class:`OAuthGrantRepository`, and
  the module-level ``log_audit`` write to and read from a real schema, and the
  audit-row assertions reflect committed state.
* The identity stack is real: :class:`OAuthService` (grant-status + refresh),
  :class:`OAuthGrantRepository`, and :class:`CryptoUtils` (AES-256-GCM) -- the
  seeded ``OAuthGrant`` carries the ``calendar.events`` scope and a genuinely
  encrypted access token, so ``_assert_calendar_grant`` and
  ``_with_calendar_token`` run their real code paths.
* :class:`CandidateService` itself is the production class, wired exactly like
  ``recruitment.container.get_candidate_service`` (a fresh session + service per
  action, mirroring request scoping).

What is SUBSTITUTED (and why)
-----------------------------
* ``minio_client`` is an ``AsyncMock`` -- the schedule/reschedule/reject paths
  never touch object storage, so MinIO is irrelevant to this flow.
* ``cv_document_repo`` is the real repository but is likewise unused by these
  paths.
* ``AuthSettings`` and the AES key are constructed with deterministic in-test
  values instead of being read from the environment, so the test is
  self-contained; the real ``OAuthService``/``CryptoUtils`` use them unchanged.

The test is marked ``integration`` and skips cleanly when ``testcontainers`` /
``docker`` or a running Docker daemon is unavailable, mirroring
``test_org_settings_repository.py`` and ``test_interview_migration.py``.

Requirements: 2.1, 7.1, 8.1, 12.1, 12.2, 12.3
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

# Importing the gmail entities registers ``email_messages`` in SQLModel.metadata
# so SQLAlchemy can resolve the ``Candidate.source_email_message_id`` foreign key
# when the ORM flushes a Candidate insert in this test.
import src.modules.gmail.domain.entities  # noqa: F401
from src.modules.employee.domain.entities import Employee
from src.modules.identity.application.oauth_service import OAuthService
from src.modules.identity.domain.entities import OAuthGrant, User, UserRole
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.identity.infrastructure.oauth_grant_repository import OAuthGrantRepository
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
    CandidateRepository,
    CVDocumentRepository,
)

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/recruitment/test_interview_integration.py
BACKEND_DIR = Path(__file__).resolve().parents[3]

# The Calendar scope that makes ``calendar_grant_valid`` true (mirrors
# identity ``OAuthService._CALENDAR_SCOPES``).
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"

# Stable identifiers / links the mocked Google layer returns.
_EVENT_ID = "evt_integration_abc123"
_HTML_LINK = f"https://www.google.com/calendar/event?eid={_EVENT_ID}"
_MEET_LINK = "https://meet.google.com/iii-jjjj-kkk"
_ORG_TIMEZONE = "Asia/Ho_Chi_Minh"

# Plaintext access token decrypted from the seeded grant by ``CryptoUtils``.
_ACCESS_TOKEN_PLAINTEXT = "ya29.integration-access-token"  # noqa: S105 - test value
_REFRESH_TOKEN_PLAINTEXT = "1//integration-refresh-token"  # noqa: S105 - test value

# Audit ``operation_type`` values written across the flow.
_OP_SCHEDULED = "interview_scheduled"
_OP_RESCHEDULED = "interview_rescheduled"
_OP_CANCELLED = "interview_event_cancelled"


# ---------------------------------------------------------------------------
# Mocked Google Calendar HTTP layer (httpx.MockTransport)
# ---------------------------------------------------------------------------
class _GoogleCalendarMock:
    """A request handler that emulates the Google Calendar events endpoints.

    Routes by HTTP method + path and records every request so the test can make
    light assertions about request construction (e.g. that create requests a
    Meet link while patch preserves it). Returns realistic event JSON so the
    real :class:`CalendarAdapter` parser produces a populated
    :class:`~src.modules.recruitment.domain.value_objects.CalendarEvent`.

    * ``POST   .../calendars/primary/events``        -> 200 created event JSON
      (id, htmlLink, ``conferenceData.entryPoints`` video Meet link).
    * ``PATCH  .../calendars/primary/events/{id}``   -> 200 updated event JSON
      (echoes the path id; carries the existing Meet link).
    * ``DELETE .../calendars/primary/events/{id}``   -> 204 No Content.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Dispatch a captured request to the matching scripted response."""
        self.requests.append(request)
        path = request.url.path
        method = request.method

        is_collection = path.endswith("/calendars/primary/events")
        is_item = "/calendars/primary/events/" in path

        if method == "POST" and is_collection:
            return httpx.Response(200, json=self._created_event_json())
        if method == "PATCH" and is_item:
            event_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self._updated_event_json(event_id))
        if method == "DELETE" and is_item:
            return httpx.Response(204)

        # Any unexpected route is a hard failure so the test surfaces drift.
        return httpx.Response(404, json={"error": {"message": f"unrouted {method} {path}"}})

    @property
    def posts(self) -> list[httpx.Request]:
        """All recorded ``POST`` (create) requests."""
        return [r for r in self.requests if r.method == "POST"]

    @property
    def patches(self) -> list[httpx.Request]:
        """All recorded ``PATCH`` (reschedule) requests."""
        return [r for r in self.requests if r.method == "PATCH"]

    @property
    def deletes(self) -> list[httpx.Request]:
        """All recorded ``DELETE`` (cancel) requests."""
        return [r for r in self.requests if r.method == "DELETE"]

    @staticmethod
    def _created_event_json() -> dict[str, Any]:
        """Build a created-event response with a Meet link entry point."""
        return {
            "id": _EVENT_ID,
            "htmlLink": _HTML_LINK,
            "hangoutLink": _MEET_LINK,
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": _MEET_LINK},
                ],
            },
            "attendees": [
                {"email": "ung.vien@example.com"},
                {"email": "interviewer@example.com"},
            ],
        }

    @staticmethod
    def _updated_event_json(event_id: str) -> dict[str, Any]:
        """Build an updated-event response echoing the patched event id."""
        return {
            "id": event_id,
            "htmlLink": _HTML_LINK,
            # The Meet link is preserved server-side across a patch that omits
            # conferenceData; the response still surfaces it.
            "hangoutLink": _MEET_LINK,
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": _MEET_LINK},
                ],
            },
            "attendees": [
                {"email": "ung.vien@example.com"},
                {"email": "interviewer@example.com"},
            ],
        }


# ---------------------------------------------------------------------------
# Migration / container helpers (mirrors test_org_settings_repository.py)
# ---------------------------------------------------------------------------
def _docker_available(docker_module: object) -> bool:
    """Return True if a Docker daemon is reachable, else False."""
    try:
        client = docker_module.from_env()  # type: ignore[attr-defined]
        client.ping()
    except Exception:  # noqa: BLE001 - any docker error means "not available"
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against ``async_url`` using the real env."""
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
    """Start PostgreSQL 15, apply all migrations, yield the asyncpg URL.

    Module-scoped so the (slow) container start + migration chain runs once.
    Skips cleanly if ``testcontainers`` / ``docker`` or a running Docker daemon
    is unavailable.
    """
    docker = pytest.importorskip("docker")
    postgres_container = pytest.importorskip("testcontainers.postgres")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for the interview integration test")

    with postgres_container.PostgresContainer("postgres:15-alpine") as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url


@pytest_asyncio.fixture
async def session_maker(
    postgres_async_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide an async session factory bound to the test engine.

    Uses ``expire_on_commit=False`` (so committed instances stay usable) and
    ``NullPool`` to match the other recruitment/onboarding integration tests.
    A fresh session is opened per service action and per re-read, so assertions
    only ever observe durably committed state.
    """
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def calendar_mock() -> _GoogleCalendarMock:
    """A fresh mocked Google Calendar HTTP layer per test."""
    return _GoogleCalendarMock()


@pytest_asyncio.fixture
async def calendar_http_client(
    calendar_mock: _GoogleCalendarMock,
) -> AsyncIterator[httpx.AsyncClient]:
    """An ``httpx.AsyncClient`` whose transport routes to the calendar mock.

    This is the *only* seam between the real :class:`CalendarAdapter` and the
    network; everything above it (request building, retries, parsing) is real.
    """
    client = httpx.AsyncClient(transport=httpx.MockTransport(calendar_mock.handler))
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Crypto / settings helpers (deterministic, env-independent)
# ---------------------------------------------------------------------------
def _make_crypto() -> CryptoUtils:
    """Build a real :class:`CryptoUtils` with a fresh 32-byte AES-256 key."""
    key_b64 = base64.b64encode(os.urandom(32)).decode("ascii")
    return CryptoUtils(key_b64)


def _make_auth_settings() -> AuthSettings:
    """Build :class:`AuthSettings` with deterministic in-test credentials.

    The Google client credentials are never used in this happy-path flow (no
    401 -> refresh occurs), but ``OAuthService`` requires a settings object to
    construct, so explicit values keep the test independent of the environment.
    """
    return AuthSettings(
        google_client_id="integration-client-id",
        google_client_secret="integration-client-secret",  # noqa: S106 - test value
        jwt_secret_key="integration-jwt-secret",  # noqa: S106 - test value
        oauth_token_encryption_key=base64.b64encode(os.urandom(32)).decode("ascii"),
    )


def _build_candidate_service(
    session: AsyncSession,
    *,
    user_id: UUID,
    crypto: CryptoUtils,
    auth_settings: AuthSettings,
    http_client: httpx.AsyncClient,
) -> CandidateService:
    """Wire a :class:`CandidateService` exactly like the recruitment container.

    Every collaborator is the production class except ``minio_client`` (an
    ``AsyncMock``; never exercised by the schedule/reschedule/reject paths). The
    ``CalendarAdapter`` is built over the MockTransport-backed ``http_client``.
    """
    settings = RecruitmentSettings()
    candidate_repo = CandidateRepository(session)
    cv_document_repo = CVDocumentRepository(session)
    org_settings_repo = OrganizationSettingsRepository(session, settings)
    oauth_grant_repo = OAuthGrantRepository(session)
    oauth_service = OAuthService(
        settings=auth_settings,
        crypto=crypto,
        grant_repository=oauth_grant_repo,
    )
    calendar_adapter = CalendarAdapter(settings=settings, http_client=http_client)

    return CandidateService(
        candidate_repo=candidate_repo,
        cv_document_repo=cv_document_repo,
        minio_client=AsyncMock(),
        session=session,
        user_id=user_id,
        calendar_port=calendar_adapter,
        org_settings_repo=org_settings_repo,
        oauth_grant_repo=oauth_grant_repo,
        oauth_service=oauth_service,
        crypto=crypto,
    )


# ---------------------------------------------------------------------------
# Seeding helpers (each commits in its own session)
# ---------------------------------------------------------------------------
async def _seed_actor_grant_employee_candidate(
    maker: async_sessionmaker[AsyncSession],
    crypto: CryptoUtils,
) -> tuple[UUID, UUID, UUID]:
    """Seed the acting HR user, their Calendar grant, an interviewer, candidate.

    Returns ``(user_id, interviewer_id, candidate_id)``. The grant carries the
    ``calendar.events`` scope and a genuinely encrypted access token, so the
    service's grant guard and token decryption run their real code paths.
    """
    suffix = uuid4().hex[:8]
    user = User(
        email=f"hr-{suffix}@example.com",
        name="HR Admin",
        google_sub=f"google-sub-{suffix}",
        role=UserRole.HR,
    )
    interviewer = Employee(
        employee_code=f"NV-{suffix}",
        full_name="Interviewer Person",
        email="interviewer@example.com",
        is_active=True,
    )
    candidate = Candidate(
        name="Ung Vien",
        email="ung.vien@example.com",
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
        await db_session.commit()
        return user.id, interviewer.id, candidate.id


# ---------------------------------------------------------------------------
# Re-read helpers (fresh session => committed state only)
# ---------------------------------------------------------------------------
async def _load_candidate(maker: async_sessionmaker[AsyncSession], candidate_id: UUID) -> Candidate:
    """Re-read the Candidate by id from a fresh session (committed state)."""
    async with maker() as db_session:
        result = await db_session.execute(select(Candidate).where(Candidate.id == candidate_id))
        candidate = result.scalars().first()
    assert candidate is not None, "candidate row must exist"
    return candidate


async def _load_audit(
    maker: async_sessionmaker[AsyncSession],
    candidate_id: UUID,
    operation_type: str,
) -> list[RecruitmentAuditLog]:
    """Return committed audit rows for a candidate + operation type."""
    async with maker() as db_session:
        result = await db_session.execute(
            select(RecruitmentAuditLog)
            .where(RecruitmentAuditLog.entity_id == candidate_id)
            .where(RecruitmentAuditLog.operation_type == operation_type)
            .order_by(RecruitmentAuditLog.created_at)  # type: ignore[arg-type]
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# The end-to-end integration test
# ---------------------------------------------------------------------------
@pytest.mark.integration
async def test_schedule_reschedule_reject_against_mocked_google_layer(
    session_maker: async_sessionmaker[AsyncSession],
    calendar_mock: _GoogleCalendarMock,
    calendar_http_client: httpx.AsyncClient,
) -> None:
    """Drive schedule -> reschedule -> reject through the wired service.

    Asserts the persisted ``Candidate`` state and ``recruitment_audit_logs``
    rows at each step, with the real :class:`CalendarAdapter` talking to a
    mocked Google HTTP layer.

    Requirements: 2.1, 7.1, 8.1, 12.1, 12.2, 12.3
    """
    crypto = _make_crypto()
    auth_settings = _make_auth_settings()
    user_id, interviewer_id, candidate_id = await _seed_actor_grant_employee_candidate(
        session_maker, crypto
    )

    schedule_start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    reschedule_start = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    duration_minutes = 60

    # --- Stage 1: schedule (POST .../events -> created event JSON). ----------
    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        scheduled = await service.schedule_interview(
            candidate_id,
            start=schedule_start,
            duration_minutes=duration_minutes,
            interviewer_ids=[interviewer_id],
            notes="First round interview",
        )
        assert scheduled.calendar_event_id == _EVENT_ID

    # The real adapter must have issued exactly one create POST that requested a
    # Meet link (conferenceData.createRequest), confirming the create path ran.
    assert len(calendar_mock.posts) == 1
    create_body = _json_body(calendar_mock.posts[0])
    assert "createRequest" in create_body["conferenceData"]
    assert (
        create_body["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"]
        == "hangoutsMeet"
    )

    # Persisted Candidate state after schedule (R2.1, R4.1-R4.3).
    after_schedule = await _load_candidate(session_maker, candidate_id)
    assert after_schedule.status == CandidateStatus.INTERVIEW_SCHEDULED
    assert after_schedule.calendar_event_id == _EVENT_ID
    assert after_schedule.interview_timezone == _ORG_TIMEZONE
    assert after_schedule.interview_start_at is not None
    assert _same_instant(after_schedule.interview_start_at, schedule_start)

    # Assert Interview created (GH issue 150)
    async with session_maker() as session:
        interviews = (
            (await session.execute(select(Interview).where(Interview.candidate_id == candidate_id)))
            .scalars()
            .all()
        )
        assert len(interviews) == 1
        iv = interviews[0]
        assert iv.status == "scheduled"
        assert iv.calendar_event_id == _EVENT_ID
        assert _same_instant(iv.start_at, schedule_start)

        participants = (
            (
                await session.execute(
                    select(InterviewParticipant).where(InterviewParticipant.interview_id == iv.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(participants) == 2
        assert {p.type for p in participants} == {"candidate", "employee"}

    # An interview_scheduled audit row exists, recording actor + event (R12.1).
    scheduled_audit = await _load_audit(session_maker, candidate_id, _OP_SCHEDULED)
    assert len(scheduled_audit) == 1
    sched_entry = scheduled_audit[0]
    assert sched_entry.user_id == user_id
    assert sched_entry.success is True
    assert sched_entry.new_value is not None
    assert sched_entry.new_value["calendar_event_id"] == _EVENT_ID
    assert sched_entry.new_value["status"] == CandidateStatus.INTERVIEW_SCHEDULED

    # --- Stage 2: reschedule (PATCH .../events/{id} -> updated event JSON). --
    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        await service.reschedule_interview(
            candidate_id,
            start=reschedule_start,
            duration_minutes=duration_minutes,
            interviewer_ids=[interviewer_id],
            notes="Rescheduled interview",
        )

    # The real adapter must have patched the EXACT stored event id and omitted
    # conferenceData so the Meet link is preserved (R7.1, R7.2).
    assert len(calendar_mock.patches) == 1
    patch_request = calendar_mock.patches[0]
    assert patch_request.url.path.endswith(f"/calendars/primary/events/{_EVENT_ID}")
    assert "conferenceData" not in _json_body(patch_request)

    # Persisted Candidate state after reschedule: start updated, event id and
    # status unchanged (R7.1, R7.3).
    after_reschedule = await _load_candidate(session_maker, candidate_id)
    assert after_reschedule.status == CandidateStatus.INTERVIEW_SCHEDULED
    assert after_reschedule.calendar_event_id == _EVENT_ID  # unchanged
    assert after_reschedule.interview_start_at is not None
    assert _same_instant(after_reschedule.interview_start_at, reschedule_start)
    assert not _same_instant(after_reschedule.interview_start_at, schedule_start)

    # Assert Interview updated (GH issue 150)
    async with session_maker() as session:
        interviews = (
            (await session.execute(select(Interview).where(Interview.candidate_id == candidate_id)))
            .scalars()
            .all()
        )
        assert len(interviews) == 1
        iv = interviews[0]
        assert iv.status == "scheduled"
        assert _same_instant(iv.start_at, reschedule_start)

    # An interview_rescheduled audit row exists with previous + new start (R12.2).
    reschedule_audit = await _load_audit(session_maker, candidate_id, _OP_RESCHEDULED)
    assert len(reschedule_audit) == 1
    resched_entry = reschedule_audit[0]
    assert resched_entry.user_id == user_id
    assert resched_entry.success is True
    assert resched_entry.new_value is not None
    assert resched_entry.new_value["calendar_event_id"] == _EVENT_ID
    assert resched_entry.new_value["previous_start"] is not None
    assert resched_entry.new_value["new_start"] is not None

    # --- Stage 3: reject (DELETE .../events/{id} -> 204). --------------------
    async with session_maker() as svc_session:
        service = _build_candidate_service(
            svc_session,
            user_id=user_id,
            crypto=crypto,
            auth_settings=auth_settings,
            http_client=calendar_http_client,
        )
        rejected = await service.reject_candidate(candidate_id, reason="Not a fit")
        assert rejected.status == CandidateStatus.REJECTED

    # The real adapter must have deleted the EXACT stored event id (R8.1).
    assert len(calendar_mock.deletes) == 1
    delete_request = calendar_mock.deletes[0]
    assert delete_request.url.path.endswith(f"/calendars/primary/events/{_EVENT_ID}")

    # Persisted Candidate state after reject: terminal rejected status.
    after_reject = await _load_candidate(session_maker, candidate_id)
    assert after_reject.status == CandidateStatus.REJECTED
    assert after_reject.rejection_reason == "Not a fit"

    # Assert Interview cancelled (GH issue 150)
    async with session_maker() as session:
        interviews = (
            (await session.execute(select(Interview).where(Interview.candidate_id == candidate_id)))
            .scalars()
            .all()
        )
        assert len(interviews) == 1
        iv = interviews[0]
        assert iv.status == "cancelled"

    # An interview_event_cancelled audit row exists, recording the cancelled
    # event id and the reject trigger (R12.3).
    cancelled_audit = await _load_audit(session_maker, candidate_id, _OP_CANCELLED)
    assert len(cancelled_audit) == 1
    cancel_entry = cancelled_audit[0]
    assert cancel_entry.user_id == user_id
    assert cancel_entry.success is True
    assert cancel_entry.new_value is not None
    assert cancel_entry.new_value["calendar_event_id"] == _EVENT_ID
    assert cancel_entry.new_value["trigger"] == "reject"


# ---------------------------------------------------------------------------
# Small assertion helpers
# ---------------------------------------------------------------------------
def _json_body(request: httpx.Request) -> dict[str, Any]:
    """Decode a captured request body as JSON."""
    import json

    body: dict[str, Any] = json.loads(request.content)
    return body


def _same_instant(left: datetime, right: datetime) -> bool:
    """Return True when two tz-aware datetimes denote the same instant.

    The service stores ``start`` re-expressed in the Organization timezone;
    PostgreSQL ``timestamptz`` round-trips it as the same UTC instant, so the
    comparison is on the absolute point in time rather than the wall clock.
    """
    return left.astimezone(UTC) == right.astimezone(UTC)
