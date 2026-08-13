"""Unit tests for request-level timeout behavior in the classify endpoint.

Validates that the POST /api/gmail/classify endpoint returns HTTP 504
with a JSON error body when the classification process exceeds the
configured request timeout.

**Validates: Requirements 2.3**
"""

import asyncio
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.identity.domain.entities import UserRole


def _create_test_app():
    """Create a minimal FastAPI app with only the gmail router for testing."""
    from fastapi import FastAPI

    from src.modules.gmail.api.router import router as gmail_router

    app = FastAPI()
    app.include_router(gmail_router)
    return app


def _make_mock_user():
    """Create a mock user with required attributes.

    ``POST /api/gmail/classify`` is gated by ``HRUserDep``, so the role has to
    be HR -- with any other role the request is rejected with 403 before the
    timeout path under test is ever reached.
    """
    user = MagicMock()
    user.id = uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.role = UserRole.HR
    return user


def _make_mock_email():
    """Create a mock EmailMessage with required attributes."""
    email = MagicMock()
    email.gmail_message_id = f"msg_{uuid4().hex[:12]}"
    email.subject = "Test email subject"
    email.sender_email = "test@example.com"
    email.sender_name = "Test Sender"
    email.snippet = "This is a test email snippet"
    email.has_attachments = False
    email.processing_status = "unprocessed"
    email.category = None
    email.user_id = uuid4()
    return email


def _make_mock_session(emails, total_remaining):
    """Build a session mock that answers every query the classify flow issues.

    The flow runs three queries before classification starts: the unclassified
    batch (``scalars().all()``), its remaining count (``scalar()``), and the
    organization AI config (``scalars().first()``). Feeding those from a fixed
    ``side_effect`` list ties the mock to the current query *order and count*,
    which is what broke this file. One result object that answers each accessor
    independently survives the flow gaining or reordering a query.
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = emails
    result.scalars.return_value.first.return_value = MagicMock()
    result.scalar.return_value = total_remaining

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _make_app(mock_user, mock_email_repo):
    """Build the gmail-router app with auth and infrastructure dependencies stubbed."""
    from src.modules.gmail.container import (
        get_connection_service,
        get_email_repository,
        get_gmail_adapter,
    )
    from src.modules.identity.container import get_current_user

    mock_connection_service = AsyncMock()
    mock_connection_service.get_status = AsyncMock(return_value=MagicMock(status="connected"))

    async def _mock_get_connection_service():
        return mock_connection_service

    app = _create_test_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_email_repository] = lambda: mock_email_repo
    app.dependency_overrides[get_connection_service] = _mock_get_connection_service
    app.dependency_overrides[get_gmail_adapter] = lambda: MagicMock()
    return app


@contextmanager
def _classification_stubbed(test_settings, classify_batch):
    """Pin the request timeout and replace the classification batch call.

    ``_build_ai_classifier`` is stubbed too: it raises unless the organization
    has a real encrypted provider key, and these tests are about the endpoint's
    timeout wrapper, not about AI provider configuration.
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "src.modules.gmail.infrastructure.config.GmailSettings",
                return_value=test_settings,
            )
        )
        stack.enter_context(
            patch("src.modules.gmail.container._build_ai_classifier", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.modules.gmail.application.classification_service"
                ".ClassificationService.classify_batch",
                side_effect=classify_batch,
            )
        )
        yield


async def _post_classify(app):
    """POST /api/gmail/classify against ``app`` and return the response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/gmail/classify")


class TestClassifyEndpointTimeout:
    """Tests that the classify endpoint returns HTTP 504 when timeout is exceeded."""

    async def test_returns_504_when_classification_exceeds_timeout(self) -> None:
        """When AI classification takes longer than the request timeout,
        the endpoint should return HTTP 504 with a JSON error body."""
        mock_user = _make_mock_user()
        mock_emails = [_make_mock_email() for _ in range(3)]

        mock_email_repo = MagicMock()
        mock_email_repo.session = _make_mock_session(mock_emails, total_remaining=3)

        # Create settings with a very short timeout (1 second)
        test_settings = GmailSettings(
            classification_request_timeout_seconds=1,
            classification_batch_concurrency=3,
            classification_confidence_threshold=0.75,
        )

        # Mock ClassificationService.classify_batch to never complete.
        async def slow_classify_batch(*args, **kwargs):
            # Avoid waiting three real seconds for the endpoint timeout.
            await asyncio.Future()
            return 3

        app = _make_app(mock_user, mock_email_repo)

        with _classification_stubbed(test_settings, slow_classify_batch):
            response = await _post_classify(app)

        assert response.status_code == 504
        body = response.json()
        assert "detail" in body

    async def test_504_response_body_contains_timeout_message(self) -> None:
        """The 504 response body should contain a 'detail' field with a
        meaningful timeout message."""
        mock_user = _make_mock_user()
        mock_emails = [_make_mock_email() for _ in range(2)]

        mock_email_repo = MagicMock()
        mock_email_repo.session = _make_mock_session(mock_emails, total_remaining=2)

        test_settings = GmailSettings(
            classification_request_timeout_seconds=1,
            classification_batch_concurrency=3,
            classification_confidence_threshold=0.75,
        )

        async def slow_classify_batch(*args, **kwargs):
            await asyncio.Future()
            return 2

        app = _make_app(mock_user, mock_email_repo)

        with _classification_stubbed(test_settings, slow_classify_batch):
            response = await _post_classify(app)

        assert response.status_code == 504
        body = response.json()
        assert "detail" in body
        # The detail message should mention timeout
        assert "timeout" in body["detail"].lower() or "Timeout" in body["detail"]

    async def test_successful_classification_within_timeout(self) -> None:
        """When classification completes within the timeout, the endpoint
        should return HTTP 200 with the normal response schema."""
        mock_user = _make_mock_user()
        mock_emails = [_make_mock_email() for _ in range(2)]
        # Set category on emails so results_summary works
        for email in mock_emails:
            email.category = "recruitment"

        mock_email_repo = MagicMock()
        mock_email_repo.session = _make_mock_session(mock_emails, total_remaining=2)

        # Use a generous timeout so classification succeeds
        test_settings = GmailSettings(
            classification_request_timeout_seconds=10,
            classification_batch_concurrency=3,
            classification_confidence_threshold=0.75,
        )

        async def fast_classify_batch(*args, **kwargs):
            await asyncio.sleep(0.1)  # Fast — well within 10s timeout
            return 2

        app = _make_app(mock_user, mock_email_repo)

        with _classification_stubbed(test_settings, fast_classify_batch):
            response = await _post_classify(app)

        assert response.status_code == 200
        body = response.json()
        assert "classified_count" in body
        assert body["classified_count"] == 2
        assert "total" in body
        assert "remaining" in body
        assert "message" in body
        assert "results" in body
