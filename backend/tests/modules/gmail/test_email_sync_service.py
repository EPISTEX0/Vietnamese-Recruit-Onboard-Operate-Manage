"""Unit tests for EmailSyncService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from src.modules.gmail.application.email_sync_service import EmailSyncService
from src.modules.gmail.domain.entities import SyncCursor
from src.modules.gmail.domain.exceptions import (
    GmailNotConnectedException,
    RateLimitedException,
)
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.identity.domain.entities import OrganizationGoogleConnection
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils


@pytest.fixture
def settings() -> GmailSettings:
    """Create GmailSettings with defaults."""
    return GmailSettings()


@pytest.fixture
def gmail_adapter() -> AsyncMock:
    """Create a mocked GmailAdapter."""
    return AsyncMock()


@pytest.fixture
def email_repo() -> AsyncMock:
    """Create a mocked EmailRepository."""
    repo = AsyncMock()
    return repo


@pytest.fixture
def session() -> AsyncMock:
    """Create a mocked AsyncSession."""
    return AsyncMock()


@pytest.fixture
def sync_cursor_repo() -> AsyncMock:
    """Create a mocked SyncCursorRepository."""
    return AsyncMock()


@pytest.fixture
def crypto() -> MagicMock:
    """Create a mocked CryptoUtils."""
    mock = MagicMock(spec=CryptoUtils)
    mock.encrypt.side_effect = lambda x: f"encrypted_{x}"
    mock.decrypt.side_effect = lambda x: x.replace("encrypted_", "")
    return mock


@pytest.fixture
def audit_logger() -> AsyncMock:
    """Create a mocked AuditLogger."""
    return AsyncMock()


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock async Redis client."""
    return AsyncMock()


@pytest.fixture
def user_id():
    """Create a test user ID."""
    return uuid4()


@pytest.fixture
def mock_connection_repo(user_id: UUID) -> AsyncMock:
    """Mock the singleton Organization Google Connection."""
    connection = MagicMock(spec=OrganizationGoogleConnection)
    connection.status = "connected"
    connection.access_token_enc = "encrypted_test_access_token"
    connection.refresh_token_enc = "encrypted_test_refresh_token"
    connection.client_secret_enc = None
    connection.connected_by_user_id = user_id
    connection.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    repo = AsyncMock()
    repo.get_singleton = AsyncMock(return_value=connection)
    repo.upsert_singleton = AsyncMock()
    return repo


@pytest.fixture
def sync_service(
    session: AsyncMock,
    gmail_adapter: AsyncMock,
    email_repo: AsyncMock,
    sync_cursor_repo: AsyncMock,
    crypto: MagicMock,
    audit_logger: AsyncMock,
    settings: GmailSettings,
    mock_redis: AsyncMock,
    mock_connection_repo: AsyncMock,
) -> EmailSyncService:
    """Create an EmailSyncService with mocked dependencies."""
    return EmailSyncService(
        session=session,
        gmail_adapter=gmail_adapter,
        email_repo=email_repo,
        sync_cursor_repo=sync_cursor_repo,
        crypto=crypto,
        audit_logger=audit_logger,
        settings=settings,
        redis_client=mock_redis,
        client_id="test-client-id",
        client_secret="test-client-secret",
        connection_repo=mock_connection_repo,
    )


def _make_connection(
    *,
    status: str = "connected",
    access_token_enc: str = "encrypted_test_access_token",
    refresh_token_enc: str = "encrypted_test_refresh_token",
    token_expires_at: datetime | None = None,
):
    """Create a mock Organization Google Connection."""
    conn = MagicMock()
    conn.status = status
    conn.access_token_enc = access_token_enc
    conn.refresh_token_enc = refresh_token_enc
    conn.client_secret_enc = None
    conn.token_expires_at = token_expires_at or (datetime.now(UTC) + timedelta(hours=1))
    return conn


def _make_message_metadata(
    msg_id: str = "msg_001",
    thread_id: str = "thread_001",
    history_id: str = "12345",
):
    """Create a mock GmailMessageMetadata."""
    metadata = MagicMock()
    metadata.id = msg_id
    metadata.thread_id = thread_id
    metadata.subject = "Test Subject"
    metadata.sender_email = "sender@example.com"
    metadata.sender_name = "Sender Name"
    metadata.recipient_emails = ["recipient@example.com"]
    metadata.cc_emails = []
    metadata.received_at = datetime.now(UTC)
    metadata.snippet = "Test snippet"
    metadata.label_ids = ["INBOX"]
    metadata.has_attachments = False
    metadata.history_id = history_id
    return metadata


class TestPollEmails:
    """Tests for poll_emails method."""

    async def test_raises_when_connection_disconnected(
        self, sync_service: EmailSyncService, mock_connection_repo: AsyncMock, user_id
    ) -> None:
        """Should raise when the singleton connection is disconnected."""
        connection = mock_connection_repo.get_singleton.return_value
        connection.status = "disconnected"

        with pytest.raises(GmailNotConnectedException):
            await sync_service.poll_emails(user_id)

    async def test_raises_when_connection_requires_reauthorization(
        self, sync_service: EmailSyncService, mock_connection_repo: AsyncMock, user_id
    ) -> None:
        """Should raise when the singleton connection needs reauthorization."""
        connection = mock_connection_repo.get_singleton.return_value
        connection.status = "reauthorization_required"

        with pytest.raises(GmailNotConnectedException):
            await sync_service.poll_emails(user_id)

    async def test_first_poll_establishes_baseline(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id,
    ) -> None:
        """First poll (no cursor) should establish baseline and not fetch emails."""
        # Connection is set up via mock_connection_repo fixture
        sync_cursor_repo.get_cursor.return_value = None
        gmail_adapter.get_latest_history_id.return_value = "55555"

        result = await sync_service.poll_emails(user_id)

        assert result == 0
        gmail_adapter.get_latest_history_id.assert_called_once_with("test_access_token")
        sync_cursor_repo.upsert_cursor.assert_called_once_with("55555")
        email_repo.batch_upsert.assert_not_called()

    async def test_no_new_emails_returns_zero(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id,
    ) -> None:
        """Should return 0 when no new emails are found."""
        # Connection is set up via mock_connection_repo fixture
        sync_cursor_repo.get_cursor.return_value = None
        gmail_adapter.fetch_messages.return_value = []

        result = await sync_service.poll_emails(user_id)

        assert result == 0
        email_repo.batch_upsert.assert_not_called()

    async def test_incremental_sync_uses_history(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id,
    ) -> None:
        """Incremental sync (cursor exists) should use fetch_history."""
        # Connection is set up via mock_connection_repo fixture
        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "99999"
        sync_cursor_repo.get_cursor.return_value = cursor
        gmail_adapter.fetch_history.return_value = (
            [_make_message_metadata()],
            "100000",
        )
        email_repo.batch_upsert.return_value = 1

        result = await sync_service.poll_emails(user_id)

        assert result == 1
        gmail_adapter.fetch_history.assert_called_once_with(
            access_token="test_access_token",
            start_history_id="99999",
            max_results=100,
        )

    async def test_history_404_clears_cursor_before_bounded_recovery(
        self,
        sync_service: EmailSyncService,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id,
    ) -> None:
        """Expired history must reset the org cursor before bounded recovery."""
        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "expired"
        sync_cursor_repo.get_cursor.return_value = cursor
        response_404 = MagicMock(status_code=404)
        gmail_adapter.fetch_history.side_effect = httpx.HTTPStatusError(
            "history expired", request=MagicMock(), response=response_404
        )
        gmail_adapter.fetch_messages.return_value = [_make_message_metadata(history_id="200000")]
        email_repo.batch_upsert.return_value = 1

        result = await sync_service.poll_emails(user_id)

        assert result == 1
        sync_cursor_repo.clear_cursor.assert_awaited_once_with()
        email_repo.clear_incomplete_ingestion_state.assert_awaited_once_with()
        gmail_adapter.fetch_messages.assert_awaited_once()
        assert gmail_adapter.fetch_messages.call_args.kwargs["query"].startswith("after:")
        sync_cursor_repo.upsert_cursor.assert_awaited_once_with("200000")

    async def test_token_refresh_on_401(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id,
    ) -> None:
        """Should attempt token refresh on 401 and retry."""

        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        # First call raises 401, second call succeeds
        response_401 = MagicMock()
        response_401.status_code = 401
        gmail_adapter.fetch_history.side_effect = [
            httpx.HTTPStatusError("401", request=MagicMock(), response=response_401),
            ([_make_message_metadata()], "12346"),
        ]
        gmail_adapter.refresh_access_token.return_value = (
            "new_access_token",
            datetime.now(UTC) + timedelta(hours=1),
        )
        email_repo.batch_upsert.return_value = 1

        result = await sync_service.poll_emails(user_id)

        assert result == 1
        gmail_adapter.refresh_access_token.assert_called_once()

    async def test_token_refresh_network_failure_keeps_connection_connected(
        self,
        sync_service: EmailSyncService,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        mock_connection_repo: AsyncMock,
        user_id,
    ) -> None:
        """Transient refresh failure must not require reauthorization."""
        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        response_401 = MagicMock()
        response_401.status_code = 401
        gmail_adapter.fetch_history.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=response_401
        )
        gmail_adapter.refresh_access_token.side_effect = Exception("Refresh failed")

        result = await sync_service.poll_emails(user_id)

        assert result == 0
        assert mock_connection_repo.get_singleton.return_value.status == "connected"

    async def test_logs_audit_on_success(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        audit_logger: AsyncMock,
        user_id,
    ) -> None:
        """Should log audit entry on successful poll."""
        # Connection is set up via mock_connection_repo fixture

        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        gmail_adapter.fetch_history.return_value = ([_make_message_metadata()], "12346")
        email_repo.batch_upsert.return_value = 1

        await sync_service.poll_emails(user_id)

        audit_logger.log_operation.assert_called_once_with(
            operation_type="fetch",
            user_id=user_id,
            message_count=1,
            success=True,
            metadata={"sync_type": "poll"},
        )


class TestManualSync:
    """Tests for manual_sync method."""

    async def test_raises_rate_limited_within_cooldown(
        self,
        sync_service: EmailSyncService,
        mock_redis: AsyncMock,
        user_id,
    ) -> None:
        """Should raise RateLimitedException within cooldown period."""
        import time

        # Simulate a recent manual sync (5 seconds ago)
        mock_redis.get.return_value = str(time.time() - 5).encode()

        with pytest.raises(RateLimitedException):
            await sync_service.manual_sync(user_id)

    async def test_allows_sync_after_cooldown(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        mock_redis: AsyncMock,
        user_id,
    ) -> None:
        """Should allow manual sync after cooldown period."""
        import time

        # Simulate last sync was 60 seconds ago (beyond 30s cooldown)
        mock_redis.get.return_value = str(time.time() - 60).encode()
        # Connection is set up via mock_connection_repo fixture

        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        gmail_adapter.fetch_history.return_value = ([_make_message_metadata()], "12346")
        email_repo.batch_upsert.return_value = 1

        result = await sync_service.manual_sync(user_id)

        assert result == 1

    async def test_allows_first_manual_sync(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        mock_redis: AsyncMock,
        user_id,
    ) -> None:
        """Should allow manual sync when no previous sync recorded."""
        mock_redis.get.return_value = None
        # Connection is set up via mock_connection_repo fixture

        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        gmail_adapter.fetch_history.return_value = ([_make_message_metadata()], "12346")
        email_repo.batch_upsert.return_value = 1

        result = await sync_service.manual_sync(user_id)

        assert result == 1

    async def test_records_timestamp_after_sync(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        mock_redis: AsyncMock,
        user_id,
    ) -> None:
        """Should record manual sync timestamp in Redis after success."""
        mock_redis.get.return_value = None
        # Connection is set up via mock_connection_repo fixture

        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        gmail_adapter.fetch_history.return_value = ([], "12345")

        await sync_service.manual_sync(user_id)

        calls = mock_redis.set.call_args_list
        manual_call = next(call for call in calls if call.args[0] == f"gmail:manual_sync:{user_id}")
        assert manual_call.kwargs["ex"] == 30

    async def test_raises_not_connected(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        mock_redis: AsyncMock,
        user_id,
    ) -> None:
        """Should raise GmailNotConnectedException when not connected."""
        mock_redis.get.return_value = None
        mock_connection_repo.get_singleton.return_value.status = "disconnected"

        with pytest.raises(GmailNotConnectedException):
            await sync_service.manual_sync(user_id)

    async def test_logs_audit_with_manual_type(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        mock_redis: AsyncMock,
        audit_logger: AsyncMock,
        user_id,
    ) -> None:
        """Should log audit entry with sync_type=manual."""
        mock_redis.get.return_value = None
        # Connection is set up via mock_connection_repo fixture

        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        sync_cursor_repo.get_cursor.return_value = cursor

        gmail_adapter.fetch_history.return_value = ([_make_message_metadata()], "12346")
        email_repo.batch_upsert.return_value = 1

        await sync_service.manual_sync(user_id)

        audit_logger.log_operation.assert_called_once_with(
            operation_type="fetch",
            user_id=user_id,
            message_count=1,
            success=True,
            metadata={"sync_type": "manual"},
        )


class TestFetchAndPersist:
    """Tests for _fetch_and_persist method."""

    async def test_first_poll_uses_after_query(
        self,
        sync_service: EmailSyncService,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        user_id,
    ) -> None:
        """First poll should set baseline using get_latest_history_id."""
        gmail_adapter.get_latest_history_id.return_value = "55555"

        result = await sync_service._fetch_and_persist(user_id, "token", None)

        assert result == 0
        gmail_adapter.get_latest_history_id.assert_called_once_with("token")
        sync_cursor_repo.upsert_cursor.assert_called_once_with("55555")

    async def test_incremental_sync_uses_fetch_history(
        self,
        sync_service: EmailSyncService,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        user_id,
    ) -> None:
        """Incremental sync should use fetch_history with stored history_id."""
        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "12345"
        gmail_adapter.fetch_history.return_value = ([], "12345")

        await sync_service._fetch_and_persist(user_id, "token", cursor)

        gmail_adapter.fetch_history.assert_called_once_with(
            access_token="token",
            start_history_id="12345",
            max_results=100,
        )

    async def test_persists_converted_entities(
        self,
        sync_service: EmailSyncService,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        user_id,
    ) -> None:
        """Should convert metadata to entities and batch upsert during incremental sync."""
        cursor = MagicMock(spec=SyncCursor)
        cursor.history_id = "99"

        msg1 = _make_message_metadata("msg_1", history_id="100")
        msg1.label_ids = ["INBOX"]
        msg2 = _make_message_metadata("msg_2", history_id="101")
        msg2.label_ids = ["INBOX"]

        gmail_adapter.fetch_history.return_value = ([msg1, msg2], "101")
        email_repo.batch_upsert.return_value = 2

        result = await sync_service._fetch_and_persist(user_id, "token", cursor)

        assert result == 2
        # Verify batch_upsert was called with 2 entities
        call_args = email_repo.batch_upsert.call_args
        entities = call_args.args[0]
        assert len(entities) == 2
        assert entities[0].gmail_message_id == "msg_1"
        assert entities[1].gmail_message_id == "msg_2"


class TestHandleConnectionTokenRefresh:
    """Tests for _handle_connection_token_refresh method."""

    async def test_successful_refresh(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        crypto: MagicMock,
        user_id,
    ) -> None:
        """Should return new access token on successful refresh."""
        connection = mock_connection_repo.get_singleton.return_value
        new_expires = datetime.now(UTC) + timedelta(hours=1)
        gmail_adapter.refresh_access_token.return_value = (
            "new_token",
            new_expires,
        )

        result = await sync_service._handle_connection_token_refresh(
            connection, mock_connection_repo
        )

        assert result == "new_token"
        mock_connection_repo.upsert_singleton.assert_called_once()

    async def test_refresh_failure_marks_reauthorization_required(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        gmail_adapter: AsyncMock,
        audit_logger: AsyncMock,
        user_id,
    ) -> None:
        """Should mark connection reauthorization_required and return None on refresh failure."""
        connection = mock_connection_repo.get_singleton.return_value
        gmail_adapter.refresh_access_token.side_effect = Exception("Failed")

        result = await sync_service._handle_connection_token_refresh(
            connection, mock_connection_repo
        )

        assert result is None

    async def test_returns_none_when_no_refresh_token(
        self,
        sync_service: EmailSyncService,
        mock_connection_repo: AsyncMock,
        user_id,
    ) -> None:
        """Should return None when connection has no refresh token."""
        connection = mock_connection_repo.get_singleton.return_value
        connection.refresh_token_enc = None

        result = await sync_service._handle_connection_token_refresh(
            connection, mock_connection_repo
        )

        assert result is None


class TestPartialFailureHandling:
    """Tests for partial failure and permanent failure handling."""

    async def test_increments_retry_count_for_failed_messages(
        self,
        sync_service: EmailSyncService,
        email_repo: AsyncMock,
    ) -> None:
        """Should increment retry count for each failed message."""
        msg1 = MagicMock(retry_count=0, is_permanently_failed=False)
        msg2 = MagicMock(retry_count=0, is_permanently_failed=False)
        email_repo.get_by_gmail_ids.return_value = [msg1, msg2]

        await sync_service._handle_failed_messages(["msg_1", "msg_2"])

        email_repo.get_by_gmail_ids.assert_called_once_with(["msg_1", "msg_2"])
        assert msg1.retry_count == 1
        assert msg2.retry_count == 1
        email_repo.save_all.assert_called_once_with([msg1, msg2])

    async def test_marks_permanently_failed_at_threshold(
        self,
        sync_service: EmailSyncService,
        email_repo: AsyncMock,
    ) -> None:
        """Should mark message as permanently failed at 5 retries."""
        msg = MagicMock(retry_count=4, is_permanently_failed=False)
        email_repo.get_by_gmail_ids.return_value = [msg]

        await sync_service._handle_failed_messages(["msg_1"])

        assert msg.retry_count == 5
        assert msg.is_permanently_failed is True
        email_repo.save_all.assert_called_once_with([msg])

    async def test_does_not_mark_permanent_below_threshold(
        self,
        sync_service: EmailSyncService,
        email_repo: AsyncMock,
    ) -> None:
        """Should not mark as permanently failed below threshold."""
        msg = MagicMock(retry_count=3, is_permanently_failed=False)
        email_repo.get_by_gmail_ids.return_value = [msg]

        await sync_service._handle_failed_messages(["msg_1"])

        assert msg.retry_count == 4
        assert msg.is_permanently_failed is False
        email_repo.save_all.assert_called_once_with([msg])

    async def test_does_not_raise_on_repository_error(
        self,
        sync_service: EmailSyncService,
        email_repo: AsyncMock,
    ) -> None:
        """Should not raise exception if repository operations fail."""
        email_repo.get_by_gmail_ids.side_effect = Exception("DB error")

        # Should not raise
        await sync_service._handle_failed_messages(["msg_1", "msg_2"])

        email_repo.get_by_gmail_ids.assert_called_once_with(["msg_1", "msg_2"])
        email_repo.save_all.assert_not_called()


class TestMetadataToEntity:
    """Tests for _metadata_to_entity conversion."""

    def test_converts_metadata_to_entity(self, sync_service: EmailSyncService) -> None:
        """Should correctly map metadata fields to entity."""
        user_id = uuid4()
        metadata = _make_message_metadata("msg_123", "thread_456")

        entity = sync_service._metadata_to_entity(user_id, metadata)

        assert entity.user_id == user_id
        assert entity.gmail_message_id == "msg_123"
        assert entity.gmail_thread_id == "thread_456"
        assert entity.subject == "Test Subject"
        assert entity.sender_email == "sender@example.com"
        assert entity.sender_name == "Sender Name"
        assert entity.recipient_emails == ["recipient@example.com"]
        assert entity.has_attachments is False

    def test_truncates_subject_to_998_chars(self, sync_service: EmailSyncService) -> None:
        """Should truncate subject to 998 characters."""
        user_id = uuid4()
        metadata = _make_message_metadata()
        metadata.subject = "x" * 2000

        entity = sync_service._metadata_to_entity(user_id, metadata)

        assert len(entity.subject) == 998

    def test_defaults_empty_string_for_missing_fields(self, sync_service: EmailSyncService) -> None:
        """Should use empty string for None/empty fields."""
        user_id = uuid4()
        metadata = _make_message_metadata()
        metadata.subject = None
        metadata.sender_email = None
        metadata.sender_name = None
        metadata.snippet = None

        entity = sync_service._metadata_to_entity(user_id, metadata)

        assert entity.subject == ""
        assert entity.sender_email == ""
        assert entity.sender_name == ""
        assert entity.snippet == ""

    def test_limits_recipients_to_50(self, sync_service: EmailSyncService) -> None:
        """Should limit recipient_emails to 50 entries."""
        user_id = uuid4()
        metadata = _make_message_metadata()
        metadata.recipient_emails = [f"user{i}@example.com" for i in range(100)]

        entity = sync_service._metadata_to_entity(user_id, metadata)

        assert len(entity.recipient_emails) == 50


class TestOrganizationConnectionSync:
    """Tests for Organization Google Connection sync flows."""

    def _make_org_service(
        self,
        gmail_adapter,
        email_repo,
        sync_cursor_repo,
        crypto,
        audit_logger,
        settings,
        mock_redis,
        connection,
    ) -> EmailSyncService:
        """Build a service with an injected connection_repo returning the given connection."""
        connection_repo = AsyncMock()
        connection_repo.get_singleton = AsyncMock(return_value=connection)
        connection_repo.upsert_singleton = AsyncMock()
        return EmailSyncService(
            session=AsyncMock(),
            gmail_adapter=gmail_adapter,
            email_repo=email_repo,
            sync_cursor_repo=sync_cursor_repo,
            crypto=crypto,
            audit_logger=audit_logger,
            settings=settings,
            redis_client=mock_redis,
            client_id="test-client-id",
            client_secret="test-client-secret",
            connection_repo=connection_repo,
        )

    @pytest.mark.asyncio
    async def test_organization_sync_establishes_baseline_first_poll(
        self,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        sync_cursor_repo: AsyncMock,
        crypto: MagicMock,
        audit_logger: AsyncMock,
        settings: GmailSettings,
        mock_redis: AsyncMock,
    ) -> None:
        """First poll establishes baseline history ID and returns 0 messages."""
        user_id = uuid4()
        connection = OrganizationGoogleConnection(
            email="org@vroom.hr",
            status="connected",
            connected_by_user_id=user_id,
            access_token_enc=crypto.encrypt("token"),
        )

        svc = self._make_org_service(
            gmail_adapter,
            email_repo,
            sync_cursor_repo,
            crypto,
            audit_logger,
            settings,
            mock_redis,
            connection,
        )

        sync_cursor_repo.get_cursor.return_value = None
        gmail_adapter.get_latest_history_id.return_value = "88888"

        result = await svc.poll_emails(user_id)

        assert result == 0
        gmail_adapter.get_latest_history_id.assert_called_once_with("token")
        sync_cursor_repo.upsert_cursor.assert_called_once_with("88888")
        email_repo.batch_upsert.assert_not_called()
