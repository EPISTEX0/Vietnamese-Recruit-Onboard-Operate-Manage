"""Intent Classifier Service for the Recruitment module.

Orchestrates email intent classification using LLM with PII redaction.
    Enqueues CV processing for emails classified as containing CVs.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10
"""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.recruitment.domain.enums import EmailIntent
from src.modules.recruitment.domain.exceptions import LLMParseError
from src.modules.recruitment.infrastructure.audit_repository import log_audit
from src.modules.recruitment.infrastructure.llm_adapter import IntentResult, LLMAdapter
from src.modules.recruitment.infrastructure.pii_redactor import PIIRedactor

logger = logging.getLogger(__name__)


# ─── Protocols for cross-module communication ──────────────────────────


@runtime_checkable
class EmailMetadataProvider(Protocol):
    """Protocol for fetching email metadata needed for classification.

    Provides access to email message data without importing Gmail entities.
    """

    async def get_email_metadata(self, email_message_id: UUID) -> EmailMetadata | None:
        """Retrieve email metadata by internal message ID."""
        ...


class EmailMetadata:
    """Data class holding email metadata for intent classification.

    Attributes:
        email_message_id: Internal UUID of the email message.
        gmail_message_id: Gmail's message ID string.
        user_id: UUID of the user who owns the email.
        subject: Email subject line.
        sender_email: Sender's email address.
        sender_name: Sender's display name.
        snippet: First 200 characters of email body.
        attachment_filenames: List of attachment filenames.
        attachment_mime_types: List of attachment MIME types.
        attachment_count: Number of attachments.
    """

    def __init__(
        self,
        email_message_id: UUID,
        gmail_message_id: str,
        user_id: UUID,
        subject: str,
        sender_email: str,
        sender_name: str,
        snippet: str,
        attachment_filenames: list[str],
        attachment_mime_types: list[str],
        attachment_count: int,
    ) -> None:
        self.email_message_id = email_message_id
        self.gmail_message_id = gmail_message_id
        self.user_id = user_id
        self.subject = subject
        self.sender_email = sender_email
        self.sender_name = sender_name
        self.snippet = snippet
        self.attachment_filenames = attachment_filenames
        self.attachment_mime_types = attachment_mime_types
        self.attachment_count = attachment_count


# Type alias for the ARQ enqueue callable
EnqueueFunc = type[None]  # placeholder, defined below as a Protocol


@runtime_checkable
class EnqueueProtocol(Protocol):
    """Protocol for enqueuing background tasks (e.g., via ARQ)."""

    async def __call__(self, task_name: str, email_message_id: UUID) -> None:
        """Enqueue a background task by name with the email message ID."""
        ...


# ─── Service ───────────────────────────────────────────────────────────


class IntentClassifierService:
    """Classifies emails by intent using LLM with PII redaction.

    Orchestrates the full classification flow:
    1. Fetch email metadata
    2. Apply PII redaction to snippet
    3. Call LLM for intent classification
    4. Log audit entry
        5. Process result (enqueue CV processing)

    Args:
        llm_adapter: LLM adapter for intent classification calls.
        pii_redactor: PII redactor for sanitizing text before LLM.
        session: Async database session for persistence operations.
        email_metadata_provider: Protocol-based email metadata provider.
        enqueue_func: Callable to enqueue background tasks via ARQ.
        access_token_provider: Callable to get the current OAuth access token.
    """

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        pii_redactor: PIIRedactor,
        session: AsyncSession,
        email_metadata_provider: EmailMetadataProvider | None = None,
        enqueue_func: EnqueueProtocol | None = None,
        access_token_provider: object | None = None,
    ) -> None:
        self._llm_adapter = llm_adapter
        self._pii_redactor = pii_redactor
        self._session = session
        self._email_metadata_provider = email_metadata_provider
        self._enqueue_func = enqueue_func
        self._access_token_provider = access_token_provider

    async def classify_email(
        self,
        subject: str,
        sender: str,
        snippet: str,
        attachment_filenames: list[str],
        gmail_message_id: str,
        email_message_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> IntentResult:
        """Classify a single email's intent using LLM.

        Applies PII redaction to the snippet before sending to the LLM.
        Handles classification failures gracefully by defaulting to OTHER.
        Logs an audit entry on completion.

        Args:
            subject: Email subject line.
            sender: Sender email address or name.
            snippet: First 200 characters of email body.
            attachment_filenames: List of attachment filenames.
            gmail_message_id: Gmail's message ID string.
            email_message_id: Optional internal UUID of the email message.
            user_id: Optional UUID of the user who owns the email.

        Returns:
            IntentResult containing the classified intent and token usage.
        """
        start_time = time.monotonic()

        # Step 1: Apply PII redaction to snippet before sending to LLM
        try:
            redacted_snippet = self._pii_redactor.redact(snippet)
        except Exception as exc:
            # Requirement 1.7: If PII redaction fails, mark as classification_failed
            logger.error(
                "PII redaction failed for email %s: %s",
                gmail_message_id,
                exc,
                extra={"gmail_message_id": gmail_message_id},
            )
            await self._mark_classification_failed(email_message_id, reason="pii_redaction_failed")
            # Log audit entry for the failure
            await self._log_audit(
                email_message_id=email_message_id,
                user_id=user_id,
                intent=None,
                token_usage=None,
                latency_ms=self._elapsed_ms(start_time),
                success=False,
                model_name=self._llm_adapter._model,
            )
            # Return OTHER as fallback
            return IntentResult(
                intent=EmailIntent.OTHER,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )

        # Step 2: Call LLM for intent classification
        try:
            result = await self._llm_adapter.classify_intent(
                subject=subject,
                sender=sender,
                snippet=redacted_snippet,
                attachment_filenames=attachment_filenames,
            )
        except LLMParseError as exc:
            # Requirement 1.9: LLM failed after retries, mark as classification_failed
            logger.error(
                "LLM classification failed for email %s after retries: %s",
                gmail_message_id,
                exc,
                extra={"gmail_message_id": gmail_message_id},
            )
            await self._mark_classification_failed(
                email_message_id, reason="llm_classification_failed"
            )
            # Log audit entry for the failure
            await self._log_audit(
                email_message_id=email_message_id,
                user_id=user_id,
                intent=None,
                token_usage=None,
                latency_ms=self._elapsed_ms(start_time),
                success=False,
                model_name=self._llm_adapter._model,
            )
            # Return OTHER as fallback
            return IntentResult(
                intent=EmailIntent.OTHER,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )

        # Step 3: Log audit entry for successful classification
        latency_ms = self._elapsed_ms(start_time)
        await self._log_audit(
            email_message_id=email_message_id,
            user_id=user_id,
            intent=result.intent,
            token_usage=result.token_usage,
            latency_ms=latency_ms,
            success=True,
            model_name=self._llm_adapter._model,
        )

        logger.info(
            "Email %s classified as %s (latency: %dms)",
            gmail_message_id,
            result.intent.value,
            latency_ms,
            extra={
                "gmail_message_id": gmail_message_id,
                "intent": result.intent.value,
                "latency_ms": latency_ms,
            },
        )

        return result

    async def process_classification_result(
        self,
        intent_result: IntentResult,
        gmail_message_id: str,
        email_message_id: UUID | None = None,
        user_id: UUID | None = None,
        access_token: str | None = None,
    ) -> None:
        """Process the classification result and enqueue Job Application attachments.

        For ``job_application`` intent:
        - Enqueue CV attachment processing via ARQ

        For other intents:
        - Store the classified intent on the email record (via category update)

        Args:
            intent_result: The IntentResult from classify_email.
            gmail_message_id: Gmail's message ID string.
            email_message_id: Optional internal UUID of the email message.
            user_id: Optional UUID of the user who owns the email.
            access_token: Optional OAuth access token for Gmail API calls.
        """
        intent = intent_result.intent

        if intent == EmailIntent.JOB_APPLICATION:
            # Enqueue CV attachment processing via ARQ
            if self._enqueue_func and email_message_id:
                try:
                    await self._enqueue_func("process_cv_from_email", email_message_id)
                    logger.info(
                        "Enqueued CV processing for email %s",
                        gmail_message_id,
                        extra={"gmail_message_id": gmail_message_id},
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to enqueue CV processing for email %s: %s",
                        gmail_message_id,
                        exc,
                        extra={"gmail_message_id": gmail_message_id},
                    )
        else:
            # Requirement 1.5: Store classified intent without triggering CV pipeline
            logger.info(
                "Email %s classified as %s, no attachment processing needed",
                gmail_message_id,
                intent.value,
                extra={
                    "gmail_message_id": gmail_message_id,
                    "intent": intent.value,
                },
            )

        # Update email message category in database
        await self._update_email_category(email_message_id, intent.value)

    # ─── Private helpers ───────────────────────────────────────────────

    async def _mark_classification_failed(
        self,
        email_message_id: UUID | None,
        reason: str,
    ) -> None:
        """Mark an email as classification_failed in the database.

        Updates the email_messages record processing_status to
        'classification_failed' for manual review by HR.

        Args:
            email_message_id: Internal UUID of the email message.
            reason: The reason for classification failure.
        """
        if email_message_id is None:
            return

        try:
            from src.modules.gmail.infrastructure.email_repository import EmailRepository

            email_msg = await EmailRepository(self._session).update_processing_status(
                email_message_id,
                processing_status="classification_failed",
                category=reason,
            )

            if email_msg:
                logger.info(
                    "Marked email %s as classification_failed (reason: %s)",
                    email_message_id,
                    reason,
                )
        except Exception as exc:
            logger.error(
                "Failed to mark email %s as classification_failed: %s",
                email_message_id,
                exc,
            )

    async def _update_email_category(
        self,
        email_message_id: UUID | None,
        category: str,
    ) -> None:
        """Update the email message category field with the classified intent.

        Args:
            email_message_id: Internal UUID of the email message.
            category: The classified intent value string.
        """
        if email_message_id is None:
            return

        try:
            from src.modules.gmail.infrastructure.email_repository import EmailRepository

            await EmailRepository(self._session).update_processing_status(
                email_message_id,
                processing_status="classified",
                category=category,
            )
        except Exception as exc:
            logger.error(
                "Failed to update email category for %s: %s",
                email_message_id,
                exc,
            )

    async def _log_audit(
        self,
        email_message_id: UUID | None,
        user_id: UUID | None,
        intent: EmailIntent | None,
        token_usage: dict[str, int] | None,
        latency_ms: int,
        success: bool,
        model_name: str,
    ) -> None:
        """Log an audit entry for the intent classification operation.

        Requirement 1.8: Log audit with operation_type "intent_classify",
        user_id, timestamp, email_message_id, classified_intent, model_name.

        Args:
            email_message_id: Internal UUID of the email message.
            user_id: UUID of the user who owns the email.
            intent: The classified intent (None if classification failed).
            token_usage: Token usage dict from LLM response.
            latency_ms: Classification latency in milliseconds.
            success: Whether classification succeeded.
            model_name: LLM model name used.
        """
        change_summary = (
            f"Classified email as '{intent.value}'" if intent else "Classification failed"
        )

        await log_audit(
            session=self._session,
            operation_type="intent_classify",
            entity_type="email_message",
            entity_id=email_message_id,
            user_id=user_id,
            new_value={
                "intent": intent.value if intent else None,
                "gmail_message_id": str(email_message_id) if email_message_id else None,
            },
            change_summary=change_summary,
            model_name=model_name,
            token_usage=token_usage,
            latency_ms=latency_ms,
            success=success,
        )

    @staticmethod
    def _elapsed_ms(start_time: float) -> int:
        """Calculate elapsed time in milliseconds from a monotonic start time.

        Args:
            start_time: The start time from time.monotonic().

        Returns:
            Elapsed time in milliseconds as an integer.
        """
        return int((time.monotonic() - start_time) * 1000)
