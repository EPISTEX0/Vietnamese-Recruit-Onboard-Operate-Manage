from unittest.mock import ANY

"""Unit tests for HistoricalImportService.

Tests cover the public API surface of the historical email import service:
preview, start, status, cancel, and the worker-side process_import_job.
All infrastructure dependencies are mocked; no real Gmail or Redis calls.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from src.modules.gmail.application.import_service import (
    HistoricalImportService,
    ImportPreview,
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
    """Create a mock async Redis client.

    All Redis methods return string values (matching decode_responses=True).
    By default ``get`` and ``hgetall`` return empty/null values.
    """
    redis = AsyncMock()
    redis.get.return_value = None
    redis.hgetall.return_value = {}
    return redis


@pytest.fixture
def http_client() -> AsyncMock:
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def user_id() -> UUID:
    """Create a test user ID."""
    return uuid4()


@pytest.fixture
def session() -> AsyncMock:
    """Create a mock async database session."""
    return AsyncMock()


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
def import_service(
    session: AsyncMock,
    gmail_adapter: AsyncMock,
    email_repo: AsyncMock,
    sync_cursor_repo: AsyncMock,
    mock_redis: AsyncMock,
    crypto: MagicMock,
    audit_logger: AsyncMock,
    settings: GmailSettings,
    http_client: AsyncMock,
    mock_connection_repo: AsyncMock,
) -> HistoricalImportService:
    """Create a HistoricalImportService with mocked dependencies."""
    return HistoricalImportService(
        session=session,
        gmail_adapter=gmail_adapter,
        email_repo=email_repo,
        sync_cursor_repo=sync_cursor_repo,
        connection_repo=mock_connection_repo,
        crypto=crypto,
        audit_logger=audit_logger,
        settings=settings,
        redis_client=mock_redis,
        http_client=http_client,
        client_id="test-client-id",
        client_secret="test-client-secret",
    )


@pytest.fixture(autouse=True)
def classification_configured(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Make the AI-config pre-flight guard pass by default.

    Only ``TestStartImportClassificationGuard`` cares about Organization AI
    Configuration; every other test in this module exercises unrelated
    behavior and would otherwise trip the guard through nothing more than the
    ``session`` fixture's default mock attribute chain.
    """
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.modules.gmail.container.raise_if_classification_not_configured", mock)
    return mock


def _make_message_metadata(
    msg_id: str = "msg_001",
    thread_id: str = "thread_001",
    label_ids: list[str] | None = None,
) -> MagicMock:
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
    metadata.label_ids = label_ids or ["INBOX"]
    metadata.has_attachments = False
    return metadata


def _make_email_entity(user_id: UUID, gmail_msg_id: str, category: str | None = None):
    """Create a mock EmailMessage entity."""
    entity = MagicMock()
    entity.id = uuid4()
    entity.user_id = user_id
    entity.gmail_message_id = gmail_msg_id
    entity.subject = "Test Subject"
    entity.category = category
    entity.processing_status = "unprocessed"
    return entity


# ---------------------------------------------------------------------------
# Tests: preview_import
# ---------------------------------------------------------------------------


class TestPreviewImport:
    """Tests for HistoricalImportService.preview_import."""

    async def test_preview_with_estimated_new_emails(
        self,
        import_service: HistoricalImportService,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Preview should return estimated counts when new emails exist."""
        # Simulate Gmail returning message IDs.
        gmail_adapter.list_message_ids.return_value = (
            [{"id": "msg_001"}, {"id": "msg_002"}, {"id": "msg_003"}],
            None,
        )
        # Simulate one already-imported message.
        email_repo.get_by_gmail_ids.return_value = [_make_email_entity(user_id, "msg_001")]

        result = await import_service.preview_import(7, user_id)

        assert isinstance(result, ImportPreview)
        assert result.days == 7
        assert result.estimated_count == 2  # 3 total - 1 already imported
        assert result.already_imported_count == 1
        assert result.query_window_start is not None
        assert result.query_window_end is not None

    async def test_preview_no_access_token(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Preview should raise when no valid access token."""
        connection = mock_connection_repo.get_singleton.return_value
        connection.access_token_enc = None

        from src.modules.gmail.domain.exceptions import GmailImportException

        with pytest.raises(GmailImportException, match="No valid access token"):
            await import_service.preview_import(7, user_id)

    async def test_preview_disconnected(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Preview should raise when connection is disconnected."""
        connection = mock_connection_repo.get_singleton.return_value
        connection.status = "disconnected"

        from src.modules.gmail.domain.exceptions import GmailImportException

        with pytest.raises(GmailImportException, match="No valid access token"):
            await import_service.preview_import(7, user_id)

    async def test_preview_no_messages(
        self,
        import_service: HistoricalImportService,
        gmail_adapter: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Preview should return zero counts when no messages match."""
        gmail_adapter.list_message_ids.return_value = ([], None)

        result = await import_service.preview_import(7, user_id)

        assert result.estimated_count == 0
        assert result.already_imported_count == 0


# ---------------------------------------------------------------------------
# Tests: start_import
# ---------------------------------------------------------------------------


class TestStartImport:
    """Tests for HistoricalImportService.start_import."""

    async def test_start_import_success(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Start import should create job metadata and return a job_id."""
        mock_redis.hgetall.return_value = {}  # No existing job.

        job_id = await import_service.start_import(7, user_id)

        assert isinstance(job_id, str)
        assert len(job_id) > 0
        # Should set job metadata in Redis.
        mock_redis.hset.assert_called_once()
        mock_redis.expire.assert_called_once()
        mock_redis.delete.assert_called_once()

    async def test_start_import_already_running(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Start import should raise when a job is already running."""
        mock_redis.hgetall.return_value = {
            "job_id": "existing-job-id",
            "status": "running",
        }

        from src.modules.gmail.domain.exceptions import GmailImportException

        with pytest.raises(GmailImportException, match="already running"):
            await import_service.start_import(7, user_id)

    async def test_start_import_completed_ok(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Start import should succeed if previous job completed."""
        mock_redis.hgetall.return_value = {
            "job_id": "prev-job",
            "status": "completed",
        }

        job_id = await import_service.start_import(7, user_id)

        assert isinstance(job_id, str)
        assert len(job_id) > 0

    async def test_start_import_invalid_days(
        self,
        import_service: HistoricalImportService,
        user_id: UUID,
    ) -> None:
        """Start import should raise for invalid time window."""
        from src.modules.gmail.domain.exceptions import GmailImportException

        with pytest.raises(GmailImportException, match="Invalid time window"):
            await import_service.start_import(15, user_id)

    async def test_start_import_no_connection(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        mock_connection_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Start import should raise when no valid connection available."""
        mock_redis.hgetall.return_value = {}  # No job.
        mock_connection_repo.get_singleton.return_value = None

        from src.modules.gmail.domain.exceptions import GmailImportException

        with pytest.raises(GmailImportException, match="No valid Google connection"):
            await import_service.start_import(7, user_id)


class TestStartImportClassificationGuard:
    """Tests for the AI-config pre-flight guard added in start_import (#352).

    ``raise_if_classification_not_configured`` itself is proven against real
    Postgres in ``test_classification_preflight_guard.py``; these tests are
    only about start_import's wiring to it.
    """

    async def test_blocked_when_classification_not_configured(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        classification_configured: AsyncMock,
        user_id: UUID,
    ) -> None:
        """A RuntimeError from the guard surfaces as a 4xx before any job is created."""
        mock_redis.hgetall.return_value = {}  # No existing job.
        classification_configured.side_effect = RuntimeError(
            "AI classification is not configured. "
            "Set up your AI provider in Admin → AI Settings first."
        )

        from src.modules.gmail.domain.exceptions import GmailImportException

        with pytest.raises(GmailImportException, match="AI classification is not configured"):
            await import_service.start_import(7, user_id)

        mock_redis.hset.assert_not_called()

    async def test_guard_skipped_when_classification_disabled(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        classification_configured: AsyncMock,
        user_id: UUID,
    ) -> None:
        """No AI config to block on if classification would never run anyway."""
        mock_redis.hgetall.return_value = {}
        import_service._settings.classification_enabled = False  # noqa: SLF001
        # If the guard ran despite being disabled, this would fail the job.
        classification_configured.side_effect = RuntimeError("should not be called")

        job_id = await import_service.start_import(7, user_id)

        assert isinstance(job_id, str)
        classification_configured.assert_not_called()

    async def test_guard_called_with_session_and_settings_when_enabled(
        self,
        import_service: HistoricalImportService,
        session: AsyncMock,
        mock_redis: AsyncMock,
        classification_configured: AsyncMock,
        user_id: UUID,
    ) -> None:
        """The guard runs, and against the session/settings the job actually uses."""
        mock_redis.hgetall.return_value = {}

        await import_service.start_import(7, user_id)

        classification_configured.assert_awaited_once_with(session, import_service._settings)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Tests: get_import_status
# ---------------------------------------------------------------------------


class TestGetImportStatus:
    """Tests for HistoricalImportService.get_import_status."""

    async def test_status_no_job(
        self, import_service: HistoricalImportService, mock_redis: AsyncMock
    ) -> None:
        """Status should return 'none' when no job exists."""
        mock_redis.hgetall.return_value = {}

        status = await import_service.get_import_status()

        assert status.status == "none"
        assert status.job_id is None

    async def test_status_running(
        self, import_service: HistoricalImportService, mock_redis: AsyncMock
    ) -> None:
        """Status should reflect a running job."""
        mock_redis.hgetall.side_effect = [
            {
                "job_id": "job-123",
                "status": "running",
                "days": "7",
                "user_id": str(uuid4()),
                "started_at": "1234567890.0",
                "total_count": "100",
                "processed_count": "25",
                "job_application_count": "3",
                "errors": "1",
            },
            # Second call for progress
            {
                "total_count": "100",
                "processed_count": "25",
                "job_application_count": "3",
                "errors": "1",
            },
        ]

        status = await import_service.get_import_status()

        assert status.job_id == "job-123"
        assert status.status == "running"
        assert status.days == 7
        assert status.total_count == 100
        assert status.processed_count == 25
        assert status.job_application_count == 3
        assert status.errors == 1
        assert status.started_at == "1234567890.0"
        assert status.completed_at is None
        assert status.error_message is None

    async def test_status_failed(
        self, import_service: HistoricalImportService, mock_redis: AsyncMock
    ) -> None:
        """Status should reflect a failed job with error message."""
        mock_redis.hgetall.side_effect = [
            {
                "job_id": "job-456",
                "status": "failed",
                "completed_at": "1234567899.0",
            },
            {
                "error_message": "Token expired",
            },
        ]

        status = await import_service.get_import_status()

        assert status.status == "failed"
        assert status.error_message == "Token expired"
        assert status.completed_at == "1234567899.0"


# ---------------------------------------------------------------------------
# Tests: cancel_import
# ---------------------------------------------------------------------------


class TestCancelImport:
    """Tests for HistoricalImportService.cancel_import."""

    async def test_cancel_running_job(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Cancel should set flag for a running job."""
        mock_redis.hgetall.return_value = {
            "job_id": "job-123",
            "status": "running",
        }

        result = await import_service.cancel_import(user_id)

        assert result is True
        mock_redis.set.assert_called_once()

    async def test_cancel_no_job(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Cancel should return False when no job exists."""
        mock_redis.hgetall.return_value = {}

        result = await import_service.cancel_import(user_id)

        assert result is False

    async def test_cancel_completed_job(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Cancel should return False for a completed job."""
        mock_redis.hgetall.return_value = {
            "job_id": "job-123",
            "status": "completed",
        }

        result = await import_service.cancel_import(user_id)

        assert result is False


# ---------------------------------------------------------------------------
# Tests: process_import_job (worker-side execution)
# ---------------------------------------------------------------------------


class TestProcessImportJob:
    """Tests for HistoricalImportService.process_import_job."""

    async def test_no_job_metadata(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
    ) -> None:
        """Process should return early when no job metadata in Redis."""
        mock_redis.hgetall.return_value = {}

        result = await import_service.process_import_job()

        assert result == {"status": "no_job"}

    async def test_skip_non_running(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
    ) -> None:
        """Process should skip jobs that aren't in 'running' state."""
        mock_redis.hgetall.return_value = {
            "job_id": "job-123",
            "status": "completed",
        }

        result = await import_service.process_import_job()

        assert result == {"status": "completed"}

    async def test_failed_no_user_id(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
    ) -> None:
        """Process should fail when user_id is missing."""
        mock_redis.hgetall.return_value = {
            "job_id": "job-123",
            "status": "running",
            "days": "7",
            "user_id": "",
        }

        result = await import_service.process_import_job()

        assert result["status"] == "failed"
        assert "Invalid" in result.get("error", "")

    async def test_process_empty_window(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        gmail_adapter: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Process should complete successfully when no messages found."""
        mock_redis.hgetall.side_effect = [
            {  # Job metadata
                "job_id": "job-123",
                "status": "running",
                "days": "7",
                "user_id": str(user_id),
                "started_at": "1234567890",
            },
            {},  # Second hgetall won't be called if we return early
        ]
        gmail_adapter.list_message_ids.return_value = ([], None)

        result = await import_service.process_import_job()

        assert result["status"] == "completed"
        assert result["total"] == 0

    async def test_process_with_messages(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Process should fetch, dedupe, and persist messages."""
        # Job metadata read
        mock_redis.hgetall.side_effect = [
            {
                "job_id": "job-123",
                "status": "running",
                "days": "7",
                "user_id": str(user_id),
                "started_at": "1234567890",
            },
            {},  # Progress key (empty)
        ]

        # Gmail returns two message IDs
        gmail_adapter.list_message_ids.return_value = (
            [{"id": "msg_001"}, {"id": "msg_002"}],
            None,
        )

        # Neither has been imported yet
        email_repo.get_by_gmail_ids.return_value = []

        # Gmail returns metadata for each message
        gmail_adapter.get_single_message_metadata.side_effect = [
            _make_message_metadata(msg_id="msg_001"),
            _make_message_metadata(msg_id="msg_002"),
        ]

        # Classification disabled
        import_service._settings.classification_enabled = False

        result = await import_service.process_import_job()

        assert result["status"] == "completed"
        assert result["processed"] == 2

        # Should have upserted both messages
        assert email_repo.batch_upsert.call_count == 2

    async def test_classification_step_failure_surfaces_as_errors(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """A whole-classification-step failure must not report completed/errors: 0 (#352).

        Unlike a single email failing classification -- which classify_batch
        already catches internally and never raises for -- this is the step
        itself dying (e.g. Organization AI Config removed mid-job). Before the
        fix this was swallowed silently: status stayed 'completed' and errors
        stayed 0, indistinguishable from an import with zero recruitment
        emails.

        Proof by mutation: revert the except branch to only ``logger.error(...)``
        (dropping the ``total_errors += total_processed`` and the
        ``error_message`` write) and this goes red.
        """
        mock_redis.hgetall.side_effect = [
            {
                "job_id": "job-123",
                "status": "running",
                "days": "7",
                "user_id": str(user_id),
                "started_at": "1234567890",
            },
            {},
        ]
        gmail_adapter.list_message_ids.return_value = ([{"id": "msg_001"}], None)
        email_repo.get_by_gmail_ids.return_value = []
        gmail_adapter.get_single_message_metadata.return_value = _make_message_metadata(
            msg_id="msg_001"
        )

        import_service._settings.classification_enabled = True  # noqa: SLF001
        import_service._classify_recent_emails = AsyncMock(  # noqa: SLF001
            side_effect=RuntimeError(
                "AI classification is not configured. "
                "Set up your AI provider in Admin → AI Settings first."
            )
        )

        result = await import_service.process_import_job()

        assert result["status"] == "completed"
        assert result["processed"] == 1
        assert result["errors"] == 1
        assert result["job_applications"] == 0

        from src.modules.gmail.application.import_service import _REDIS_PROGRESS_KEY

        mock_redis.hset.assert_any_call(_REDIS_PROGRESS_KEY, "error_message", ANY)
        error_message_call = next(
            c
            for c in mock_redis.hset.mock_calls
            if c.args[:2] == (_REDIS_PROGRESS_KEY, "error_message")
        )
        assert "Classification failed" in error_message_call.args[2]

    async def test_cancellation_during_processing(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Process should stop and report cancelled when cancel flag is set."""
        mock_redis.hgetall.side_effect = [
            {
                "job_id": "job-123",
                "status": "running",
                "days": "7",
                "user_id": str(user_id),
                "started_at": "1234567890",
            },
        ]

        # Many messages so processing takes multiple batches
        many_ids = [{"id": f"msg_{i:03d}"} for i in range(150)]
        gmail_adapter.list_message_ids.return_value = (many_ids, None)

        # Simulate cancellation after the first batch reads
        # _is_cancelled returns True after the first call
        mock_redis.get.side_effect = [None, "1"]

        import_service._settings.classification_enabled = False

        result = await import_service.process_import_job()

        assert result["status"] == "cancelled"

    async def test_deduplication(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
        gmail_adapter: AsyncMock,
        email_repo: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Process should skip already-imported messages."""
        mock_redis.hgetall.side_effect = [
            {
                "job_id": "job-123",
                "status": "running",
                "days": "7",
                "user_id": str(user_id),
                "started_at": "1234567890",
            },
        ]

        gmail_adapter.list_message_ids.return_value = (
            [{"id": "msg_001"}, {"id": "msg_002"}],
            None,
        )

        # msg_001 already imported
        email_repo.get_by_gmail_ids.return_value = [_make_email_entity(user_id, "msg_001")]

        # Only msg_002 should be fetched
        gmail_adapter.get_single_message_metadata.return_value = _make_message_metadata(
            msg_id="msg_002"
        )

        import_service._settings.classification_enabled = False

        result = await import_service.process_import_job()

        assert result["processed"] == 1
        gmail_adapter.get_single_message_metadata.assert_called_once_with(ANY, "msg_002")


# ---------------------------------------------------------------------------
# Tests: _build_query includes INBOX constraint
# ---------------------------------------------------------------------------


class TestBuildQuery:
    """Tests for HistoricalImportService._build_query.

    Verifies that Gmail queries explicitly constrain to INBOX.
    """

    def test_inbox_in_query(self) -> None:
        """Query should include 'in:inbox' prefix."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        window_start = now
        query = HistoricalImportService._build_query(window_start, now)
        assert "in:inbox" in query
        assert "after:" in query
        assert "before:" in query

    def test_query_format(self) -> None:
        """Query should have correct format."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        window_start = now - timedelta(days=7)
        query = HistoricalImportService._build_query(window_start, now)
        parts = query.split()
        assert len(parts) == 3
        assert parts[0] == "in:inbox"
        assert parts[1].startswith("after:")
        assert parts[2].startswith("before:")


# ---------------------------------------------------------------------------
# Tests: connection integrity verification
# ---------------------------------------------------------------------------


class TestConnectionIntegrity:
    """Tests for connection identity/generation tracking."""

    async def test_capture_generation_valid(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
    ) -> None:
        """Should capture connection identity for a valid connection."""
        gen = await import_service._capture_connection_generation()
        assert gen is not None
        assert len(gen) == 3
        updated_at, status, email = gen
        assert status == "connected"

    async def test_capture_generation_no_connection(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
    ) -> None:
        """Should return None when no connection exists."""
        mock_connection_repo.get_singleton.return_value = None
        gen = await import_service._capture_connection_generation()
        assert gen is None

    async def test_verify_integrity_pass(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
    ) -> None:
        """Should return True when connection hasn't changed."""
        import_service._connection_generation = (
            await import_service._capture_connection_generation()
        )
        assert await import_service._verify_connection_integrity() is True

    async def test_verify_integrity_fail_disconnected(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
    ) -> None:
        """Should return False when connection is disconnected."""
        import_service._connection_generation = (
            await import_service._capture_connection_generation()
        )
        connection = mock_connection_repo.get_singleton.return_value
        connection.status = "disconnected"
        assert await import_service._verify_connection_integrity() is False

    async def test_verify_integrity_fail_account_switch(
        self,
        import_service: HistoricalImportService,
        mock_connection_repo: AsyncMock,
    ) -> None:
        """Should return False when connection account changes."""
        import_service._connection_generation = (
            await import_service._capture_connection_generation()
        )
        connection = mock_connection_repo.get_singleton.return_value
        connection.email = "different@example.com"
        assert await import_service._verify_connection_integrity() is False

    async def test_verify_integrity_no_generation(
        self,
        import_service: HistoricalImportService,
    ) -> None:
        """Should return False when generation was never captured."""
        import_service._connection_generation = None
        assert await import_service._verify_connection_integrity() is False


# ---------------------------------------------------------------------------
# Tests: job state cleanup
# ---------------------------------------------------------------------------


class TestJobStateCleanup:
    """Tests for HistoricalImportService._cleanup_job_state."""

    async def test_cleanup_job_state_deletes_keys(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
    ) -> None:
        """Should delete all import keys and clear generation."""
        import_service._connection_generation = ("some", "value", "here")
        await import_service._cleanup_job_state()
        assert mock_redis.delete.call_count >= 1
        call_args = mock_redis.delete.call_args
        assert call_args is not None
        # Should include all 4 key prefixes
        all_keys = " ".join(str(k) for k in call_args)
        assert "gmail:historical_import:job" in all_keys
        assert "gmail:historical_import:cancel" in all_keys
        assert import_service._connection_generation is None

    async def test_cleanup_job_state_clears_generation(
        self,
        import_service: HistoricalImportService,
        mock_redis: AsyncMock,
    ) -> None:
        """Should reset _connection_generation to None."""
        import_service._connection_generation = ("x", "y", "z")
        await import_service._cleanup_job_state()
        assert import_service._connection_generation is None
