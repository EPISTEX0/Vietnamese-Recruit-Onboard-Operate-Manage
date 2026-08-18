"""Unit tests for OutboundEmailService."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from src.modules.gmail.application.outbound_email_service import (
    OutboundEmailService,
    _make_idempotency_key,
)
from src.modules.gmail.domain.entities import OutboundEmail
from src.modules.gmail.domain.enums import OutboundEmailStatus
from src.modules.gmail.domain.exceptions import (
    GmailFetchError,
    GmailSendFailedException,
    OrganizationNotConnectedError,
    OutboundEmailAlreadySentError,
    OutboundEmailMaxRetriesExceededError,
    OutboundEmailNotFoundError,
)
from src.modules.gmail.infrastructure.gmail_adapter import SentMessageInfo


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def outbound_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def connection_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def candidate_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def gmail_adapter() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def crypto() -> MagicMock:
    mock = MagicMock()
    mock.encrypt.side_effect = lambda x: f"enc_{x}"
    mock.decrypt.side_effect = lambda x: (
        x.replace("enc_", "") if isinstance(x, str) and x.startswith("enc_") else x
    )
    return mock


@pytest.fixture
def audit_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def http_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def audit_action_type() -> type:
    """Mock AuditActionType enum with just the outbound email action types."""
    from enum import StrEnum

    class MockAuditActionType(StrEnum):
        OUTBOUND_EMAIL_CREATED = "outbound_email_created"
        OUTBOUND_EMAIL_SENT = "outbound_email_sent"
        OUTBOUND_EMAIL_FAILED = "outbound_email_failed"
        OUTBOUND_EMAIL_RETRY = "outbound_email_retry"

    return MockAuditActionType


@pytest.fixture
def service(
    session: AsyncMock,
    outbound_repo: AsyncMock,
    connection_repo: AsyncMock,
    candidate_repo: AsyncMock,
    gmail_adapter: AsyncMock,
    crypto: MagicMock,
    audit_service: AsyncMock,
    http_client: AsyncMock,
    audit_action_type: type,
) -> OutboundEmailService:
    return OutboundEmailService(
        session=session,
        outbound_repo=outbound_repo,
        connection_repo=connection_repo,
        candidate_repo=candidate_repo,
        gmail_adapter=gmail_adapter,
        crypto=crypto,
        audit_service=audit_service,
        oauth_config_client_id="test-client-id",
        http_client=http_client,
        audit_action_type=audit_action_type,
    )


def _make_outbound(
    *,
    outbound_id: UUID | None = None,
    status: str = "pending",
    retry_count: int = 0,
    max_retries: int = 3,
    recipient_email: str = "candidate@example.com",
    subject: str = "Test Subject",
    body_html: str = "<p>Hello</p>",
    gmail_message_id: str | None = None,
    gmail_thread_id: str | None = None,
    error_message: str | None = None,
) -> OutboundEmail:
    uid = outbound_id or uuid4()
    return OutboundEmail(
        id=uid,
        idempotency_key=_make_idempotency_key(
            candidate_id=None,
            recipient_email=recipient_email,
            subject=subject,
            body_hash="abc123",
        ),
        candidate_id=None,
        subject=subject,
        body_html=body_html,
        recipient_email=recipient_email,
        status=status,
        retry_count=retry_count,
        max_retries=max_retries,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
        error_message=error_message,
        created_by_user_id=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_connection(*, status: str = "connected", email: str = "org@example.com") -> MagicMock:
    conn = MagicMock()
    conn.status = status
    conn.email = email
    conn.access_token_enc = "enc_access_token_123"
    conn.refresh_token_enc = "enc_refresh_token_123"
    conn.client_secret_enc = "enc_client_secret"
    conn.google_sub = "sub_123"
    conn.email_domain = "example.com"
    conn.selected_calendar_id = None
    conn.credential_format_version = 1
    conn.credential_key_version = 1
    conn.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn.connected_by_user_id = uuid4()
    return conn


# ── Idempotency key tests ──────────────────────────────────────────


def test_make_idempotency_key_deterministic() -> None:
    """Same inputs produce same key."""
    key1 = _make_idempotency_key(
        candidate_id=uuid4(),
        recipient_email="a@b.com",
        subject="Hello",
        body_hash="abc",
    )
    key2 = _make_idempotency_key(
        candidate_id=uuid4(),
        recipient_email="a@b.com",
        subject="Hello",
        body_hash="abc",
    )
    assert key1 != key2  # different candidate_id

    # Same everything
    cid = uuid4()
    key3 = _make_idempotency_key(
        candidate_id=cid, recipient_email="a@b.com", subject="Hello", body_hash="abc"
    )
    key4 = _make_idempotency_key(
        candidate_id=cid, recipient_email="a@b.com", subject="Hello", body_hash="abc"
    )
    assert key3 == key4

    # Different body hash
    key5 = _make_idempotency_key(
        candidate_id=cid, recipient_email="a@b.com", subject="Hello", body_hash="xyz"
    )
    assert key3 != key5


# ── create_outbound tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_outbound_success(
    service: OutboundEmailService,
    outbound_repo: AsyncMock,
) -> None:
    """Creating an outbound email persists with pending status."""
    hr_user = MagicMock()
    hr_user.id = uuid4()
    hr_user.email = "admin@example.com"
    hr_user.name = "Admin"

    # get_by_idempotency_key should return None (no existing)
    outbound_repo.get_by_idempotency_key.return_value = None

    # create should return the entity - use real entity
    async def _fake_create(entity: OutboundEmail) -> OutboundEmail:
        entity.idempotency_key = entity.idempotency_key or "fake_key"
        return entity

    outbound_repo.create.side_effect = _fake_create

    outbound = await service.create_outbound(
        candidate_id=None,
        recipient_email="candidate@example.com",
        subject="Test Subject",
        body_html="<p>Hello</p>",
        created_by_user_id=uuid4(),
        hr_user=hr_user,
    )

    assert outbound.status == OutboundEmailStatus.pending
    assert outbound.recipient_email == "candidate@example.com"
    assert outbound.subject == "Test Subject"
    assert len(outbound.idempotency_key) == 64  # SHA-256 hex

    service._session.commit.assert_called_once()
    service._audit_service.log_action.assert_called_once()


@pytest.mark.asyncio
async def test_create_outbound_duplicate_idempotency_key(
    service: OutboundEmailService,
) -> None:
    """Creating with the same content raises conflict."""
    from src.modules.gmail.domain.exceptions import OutboundEmailIdempotencyConflictError

    existing = _make_outbound()
    service._outbound_repo.get_by_idempotency_key.return_value = existing

    with pytest.raises(OutboundEmailIdempotencyConflictError):
        await service.create_outbound(
            candidate_id=existing.candidate_id,
            recipient_email=existing.recipient_email,
            subject=existing.subject,
            body_html=existing.body_html,
            created_by_user_id=uuid4(),
            hr_user=MagicMock(),
        )


# ── send_outbound tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_outbound_not_found(
    service: OutboundEmailService,
) -> None:
    """Sending a non-existent outbound raises NotFound."""
    service._outbound_repo.get_by_id.return_value = None

    with pytest.raises(OutboundEmailNotFoundError):
        await service.send_outbound(uuid4())


@pytest.mark.asyncio
async def test_send_outbound_already_sent(
    service: OutboundEmailService,
) -> None:
    """Sending an already-sent outbound raises AlreadySent."""
    sent = _make_outbound(status=OutboundEmailStatus.sent)
    service._outbound_repo.get_by_id.return_value = sent

    with pytest.raises(OutboundEmailAlreadySentError):
        await service.send_outbound(sent.id)


@pytest.mark.asyncio
async def test_send_outbound_org_not_connected(
    service: OutboundEmailService,
) -> None:
    """Sending when organization is disconnected raises error."""
    pending = _make_outbound()
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = None

    with pytest.raises(OrganizationNotConnectedError):
        await service.send_outbound(pending.id)


@pytest.mark.asyncio
async def test_send_outbound_no_token(
    service: OutboundEmailService,
) -> None:
    """Sending when connection has no token raises error."""
    pending = _make_outbound()
    service._outbound_repo.get_by_id.return_value = pending
    conn = _make_connection()
    conn.access_token_enc = None
    service._connection_repo.get_singleton.return_value = conn

    with pytest.raises(OrganizationNotConnectedError):
        await service.send_outbound(pending.id)


@pytest.mark.asyncio
async def test_send_outbound_success(
    service: OutboundEmailService,
    crypto: MagicMock,
) -> None:
    """Successful send updates status to sent and records Gmail IDs."""
    pending = _make_outbound()
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()

    sent_info = SentMessageInfo(message_id="msg_123", thread_id="thread_456")

    # Make adapter return a sent result
    async def fake_send(token, mime):
        return sent_info

    service._gmail_adapter.send_message = fake_send

    # mock update_status to return updated entity
    updated = _make_outbound(
        status=OutboundEmailStatus.sent,
        gmail_message_id="msg_123",
        gmail_thread_id="thread_456",
    )
    service._outbound_repo.update_status.return_value = updated

    result = await service.send_outbound(pending.id, hr_user=MagicMock())

    assert result.status == OutboundEmailStatus.sent
    assert result.gmail_message_id == "msg_123"
    service._session.commit.assert_called()


@pytest.mark.asyncio
async def test_send_outbound_auth_failure_sets_reauthorization(
    service: OutboundEmailService,
) -> None:
    """401 from Gmail sets org connection to reauthorization_required."""
    pending = _make_outbound(retry_count=2, max_retries=2)
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()
    service._outbound_repo.update_status.return_value = _make_outbound(
        status=OutboundEmailStatus.failed,
    )

    # Simulate 401 from adapter
    response = MagicMock()
    response.status_code = 401
    response.text = "Invalid credentials"
    http_error = httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=response)

    async def fake_send(token, mime):
        raise http_error

    service._gmail_adapter.send_message = fake_send

    with pytest.raises(GmailSendFailedException):
        await service.send_outbound(pending.id)

    # Connection should be set to reauthorization_required
    service._connection_repo.update_status.assert_called_with("reauthorization_required")


@pytest.mark.asyncio
async def test_send_outbound_http_status_error_is_permanent(
    service: OutboundEmailService,
) -> None:
    """An httpx.HTTPStatusError (401 is the only status that reaches this
    branch — GmailAdapter wraps every other HTTP error into
    GmailSendFailedException) is classified by exception type, not status
    code, and is never auto-retried.
    """
    pending = _make_outbound(retry_count=0, max_retries=3)
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()
    service._outbound_repo.update_status.return_value = _make_outbound(
        status=OutboundEmailStatus.failed,
    )

    response = MagicMock()
    response.status_code = 401
    response.text = "Invalid Credentials"
    http_error = httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=response)

    async def fake_send(token, mime):
        raise http_error

    service._gmail_adapter.send_message = fake_send

    with pytest.raises(GmailSendFailedException):
        await service.send_outbound(pending.id)

    service._connection_repo.update_status.assert_called_with("reauthorization_required")
    _, kwargs = service._outbound_repo.update_status.call_args
    assert kwargs["status"] == OutboundEmailStatus.failed
    assert "Invalid Credentials" in kwargs["error_message"]


@pytest.mark.asyncio
async def test_send_outbound_unexpected_exception_is_permanent_and_preserves_detail(
    service: OutboundEmailService,
) -> None:
    """An unforeseen exception (e.g. a bug) fails outright and keeps its
    original message instead of a false "will retry" promise.
    """
    pending = _make_outbound(retry_count=0, max_retries=3)
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()
    service._outbound_repo.update_status.return_value = _make_outbound(
        status=OutboundEmailStatus.failed,
    )

    async def fake_send(token, mime):
        raise TypeError("unexpected boom")

    service._gmail_adapter.send_message = fake_send

    with pytest.raises(GmailSendFailedException):
        await service.send_outbound(pending.id)

    _, kwargs = service._outbound_repo.update_status.call_args
    assert kwargs["status"] == OutboundEmailStatus.failed
    assert "unexpected boom" in kwargs["error_message"]
    assert "will retry" not in kwargs["error_message"]


@pytest.mark.asyncio
async def test_send_outbound_unexpected_exception_is_logged(
    service: OutboundEmailService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The unexpected-exception branch logs with a traceback, not silently."""
    pending = _make_outbound(retry_count=0, max_retries=3)
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()
    service._outbound_repo.update_status.return_value = _make_outbound(
        status=OutboundEmailStatus.failed,
    )

    async def fake_send(token, mime):
        raise TypeError("unexpected boom")

    service._gmail_adapter.send_message = fake_send

    logger_name = "src.modules.gmail.application.outbound_email_service"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        with pytest.raises(GmailSendFailedException):
            await service.send_outbound(pending.id)

    matching = [r for r in caplog.records if str(pending.id) in r.message]
    assert matching, "expected an error-level log mentioning the outbound id"
    assert matching[0].exc_info is not None


@pytest.mark.asyncio
async def test_send_outbound_gmail_fetch_error_stays_pending_with_detail(
    service: OutboundEmailService,
) -> None:
    """GmailFetchError (transient network failure) stays pending and keeps detail."""
    pending = _make_outbound(retry_count=0, max_retries=3)
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()
    service._outbound_repo.update_status.return_value = _make_outbound(
        status=OutboundEmailStatus.pending,
    )

    async def fake_send(token, mime):
        raise GmailFetchError("connection reset by peer")

    service._gmail_adapter.send_message = fake_send

    result = await service.send_outbound(pending.id)

    assert result.status == OutboundEmailStatus.pending
    _, kwargs = service._outbound_repo.update_status.call_args
    assert kwargs["status"] == OutboundEmailStatus.pending
    assert "connection reset by peer" in kwargs["error_message"]
    assert "will retry" not in kwargs["error_message"]


@pytest.mark.asyncio
async def test_send_outbound_gmail_send_failed_exception_is_permanent(
    service: OutboundEmailService,
) -> None:
    """GmailSendFailedException (adapter already gave up) fails outright with detail."""
    pending = _make_outbound(retry_count=0, max_retries=3)
    service._outbound_repo.get_by_id.return_value = pending
    service._connection_repo.get_singleton.return_value = _make_connection()
    service._outbound_repo.update_status.return_value = _make_outbound(
        status=OutboundEmailStatus.failed,
    )

    async def fake_send(token, mime):
        raise GmailSendFailedException("Failed to send email: 403 Forbidden")

    service._gmail_adapter.send_message = fake_send

    with pytest.raises(GmailSendFailedException):
        await service.send_outbound(pending.id)

    _, kwargs = service._outbound_repo.update_status.call_args
    assert kwargs["status"] == OutboundEmailStatus.failed
    assert "Failed to send email: 403 Forbidden" in kwargs["error_message"]


# ── retry_outbound tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_outbound_success(
    service: OutboundEmailService,
) -> None:
    """Retrying a failed outbound sends again successfully."""
    failed = _make_outbound(status=OutboundEmailStatus.failed, retry_count=1, max_retries=3)
    service._outbound_repo.get_by_id.return_value = failed
    service._connection_repo.get_singleton.return_value = _make_connection()

    sent_info = SentMessageInfo(message_id="msg_789", thread_id="thread_789")

    async def fake_send(token, mime):
        return sent_info

    service._gmail_adapter.send_message = fake_send

    updated = _make_outbound(status=OutboundEmailStatus.sent, retry_count=2)
    service._outbound_repo.update_status.return_value = updated

    result = await service.retry_outbound(failed.id)

    assert result.status == OutboundEmailStatus.sent


@pytest.mark.asyncio
async def test_retry_outbound_exceeds_max_retries(
    service: OutboundEmailService,
) -> None:
    """Retrying past max_retries raises MaxRetriesExceeded."""
    failed = _make_outbound(status=OutboundEmailStatus.failed, retry_count=3, max_retries=3)
    service._outbound_repo.get_by_id.return_value = failed

    with pytest.raises(OutboundEmailMaxRetriesExceededError):
        await service.retry_outbound(failed.id)


# ── get_outbound tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_outbound_not_found(
    service: OutboundEmailService,
) -> None:
    """Getting a non-existent outbound raises NotFound."""
    service._outbound_repo.get_by_id.return_value = None

    with pytest.raises(OutboundEmailNotFoundError):
        await service.get_outbound(uuid4())


@pytest.mark.asyncio
async def test_get_outbound_success(
    service: OutboundEmailService,
) -> None:
    """Getting an existing outbound returns it."""
    outbound = _make_outbound()
    service._outbound_repo.get_by_id.return_value = outbound

    result = await service.get_outbound(outbound.id)

    assert result.id == outbound.id
    assert result.status == outbound.status


# ── list_for_candidate tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_for_candidate(
    service: OutboundEmailService,
) -> None:
    """List for candidate delegates to repository."""
    candidate_id = uuid4()
    items = [_make_outbound(), _make_outbound()]
    service._outbound_repo.list_by_candidate.return_value = (items, 2)

    result_items, total = await service.list_for_candidate(candidate_id)

    assert total == 2
    assert len(result_items) == 2
    service._outbound_repo.list_by_candidate.assert_called_with(
        candidate_id=candidate_id, page=1, page_size=20
    )
