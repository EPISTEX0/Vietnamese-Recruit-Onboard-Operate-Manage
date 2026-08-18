"""Logging when the privacy-compatible fallback AI provider also fails (#386 C3).

``ClassificationService._classify_single`` already logs a warning when the
*primary* AI provider fails and no usable fallback result exists (see
``AI classification unavailable for email ...``), but that warning only
carries the primary provider's exception — the fallback provider's own
failure reason was swallowed with no trace at all.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.gmail.application.classification_service import ClassificationService
from src.modules.gmail.application.provider_fallback import ProviderFallbackPolicy
from src.modules.gmail.domain.enums import EmailCategory
from src.modules.gmail.infrastructure.ai_classifier import ClassificationResult
from src.modules.gmail.infrastructure.config import GmailSettings

LOGGER = "src.modules.gmail.application.classification_service"


def _make_mock_email() -> MagicMock:
    email = MagicMock()
    email.gmail_message_id = f"msg_{uuid4().hex[:12]}"
    email.subject = "Test email subject"
    email.sender_email = "test@example.com"
    email.sender_name = "Test Sender"
    email.snippet = "This is a test email snippet for classification"
    email.has_attachments = False
    email.processing_status = "unprocessed"
    email.category = None
    return email


def _make_low_confidence_rules_result() -> ClassificationResult:
    return ClassificationResult(
        category=EmailCategory.uncategorized,
        confidence=0.3,
        source="rules",
        matched_signals=[],
    )


@pytest.fixture
def settings() -> GmailSettings:
    return GmailSettings(
        classification_batch_concurrency=3,
        classification_confidence_threshold=0.75,
    )


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.flush = AsyncMock()
    return mock


@pytest.fixture
def audit_logger() -> AsyncMock:
    mock = AsyncMock()
    mock.log_operation = AsyncMock()
    return mock


@pytest.fixture
def email_repo() -> AsyncMock:
    return AsyncMock()


async def test_fallback_provider_failure_is_logged(
    settings: GmailSettings,
    session: AsyncMock,
    audit_logger: AsyncMock,
    email_repo: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both the primary and the fallback provider raise -> the fallback's own
    exception is logged, not just swallowed on the way to the generic
    'AI classification unavailable' outcome."""
    rules_classifier = MagicMock()
    rules_classifier.classify = MagicMock(return_value=_make_low_confidence_rules_result())

    ai_classifier = AsyncMock()
    ai_classifier.classify = AsyncMock(side_effect=RuntimeError("primary provider down"))

    fallback_ai_classifier = AsyncMock()
    fallback_ai_classifier.classify = AsyncMock(
        side_effect=ValueError("fallback provider also down")
    )

    service = ClassificationService(
        rules_classifier=rules_classifier,
        ai_classifier=ai_classifier,
        email_repo=email_repo,
        audit_logger=audit_logger,
        settings=settings,
        session=session,
        fallback_ai_classifier=fallback_ai_classifier,
        fallback_policy=ProviderFallbackPolicy(
            fallback_provider="backup",
            same_privacy_boundary=True,
            quality_floor_met=True,
        ),
    )

    email = _make_mock_email()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = await service._classify_single(email)

    assert result.source in {"ai_unavailable", "fallback"}, "unchanged degrade behaviour"

    fallback_records = [
        r for r in caplog.records if r.name == LOGGER and "fallback" in r.getMessage().lower()
    ]
    assert len(fallback_records) == 1
    record = fallback_records[0]
    assert record.levelno == logging.WARNING
    assert "fallback provider also down" in record.getMessage()
    assert email.gmail_message_id in record.getMessage()
