"""Repository for EmailMessage entity persistence operations.

Provides async database access for email message batch upsert, lookup,
label updates, retry tracking, and failure management using SQLAlchemy
async sessions with SQLModel and PostgreSQL ON CONFLICT for efficient upserts.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.gmail.domain.entities import EmailMessage

logger = logging.getLogger(__name__)


class EmailRepository:
    """Handles EmailMessage entity persistence using async SQLAlchemy sessions.

    Supports batch upsert with PostgreSQL ON CONFLICT to efficiently insert
    new records or update only label_ids for existing records. Handles partial
    batch failures by logging errors per message and continuing with remaining.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def batch_upsert(self, messages: list[EmailMessage]) -> int:
        """Batch insert or update email messages using PostgreSQL ON CONFLICT.

        For new records (no matching gmail_message_id), inserts the full record.
        For existing records (same gmail_message_id), updates only the label_ids
        field to reflect the current Gmail state.

        Handles partial batch failures: logs errors per message and continues
        with remaining messages in the batch.

        Args:
            messages: List of EmailMessage entities to upsert.

        Returns:
            The number of messages successfully upserted.
        """
        if not messages:
            return 0

        success_count = 0

        for message in messages:
            try:
                async with self.session.begin_nested():
                    stmt = pg_insert(EmailMessage).values(
                        id=message.id,
                        user_id=message.user_id,
                        gmail_message_id=message.gmail_message_id,
                        gmail_thread_id=message.gmail_thread_id,
                        subject=message.subject,
                        sender_email=message.sender_email,
                        sender_name=message.sender_name,
                        recipient_emails=message.recipient_emails,
                        cc_emails=message.cc_emails,
                        received_at=message.received_at,
                        snippet=message.snippet,
                        label_ids=message.label_ids,
                        has_attachments=message.has_attachments,
                        raw_payload_enc=message.raw_payload_enc,
                        processing_status=message.processing_status,
                        category=message.category,
                        retry_count=message.retry_count,
                        is_permanently_failed=message.is_permanently_failed,
                        created_at=message.created_at,
                        updated_at=message.updated_at,
                    )

                    stmt = stmt.on_conflict_do_update(
                        index_elements=["gmail_message_id"],
                        set_={
                            "label_ids": stmt.excluded.label_ids,
                            "updated_at": datetime.now(UTC),
                        },
                    )

                    await self.session.execute(stmt)
                success_count += 1
            except Exception:
                # PostgreSQL aborts the whole transaction on a failed statement;
                # without the SAVEPOINT above, this except would only postpone
                # the failure to the unconditional flush() below, surfacing as
                # an unrelated PendingRollbackError once the caller commits.
                logger.error(
                    "Failed to upsert email message gmail_message_id=%s",
                    message.gmail_message_id,
                    exc_info=True,
                )
                continue

        await self.session.flush()
        return success_count

    async def get_by_gmail_id(self, gmail_message_id: str) -> EmailMessage | None:
        """Retrieve an email message by its Gmail message ID.

        Args:
            gmail_message_id: The unique Gmail message identifier.

        Returns:
            The EmailMessage entity if found, None otherwise.
        """
        statement = select(EmailMessage).where(EmailMessage.gmail_message_id == gmail_message_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_gmail_ids(self, gmail_message_ids: list[str]) -> list[EmailMessage]:
        """Retrieve multiple email messages by their Gmail message IDs.

        Args:
            gmail_message_ids: A list of unique Gmail message identifiers.

        Returns:
            A list of EmailMessage entities found.
        """
        if not gmail_message_ids:
            return []

        statement = select(EmailMessage).where(EmailMessage.gmail_message_id.in_(gmail_message_ids))  # type: ignore[attr-defined]
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def save_all(self, messages: list[EmailMessage]) -> None:
        """Save a list of EmailMessage entities to the database.

        Args:
            messages: A list of EmailMessage entities to save.
        """
        for message in messages:
            self.session.add(message)
        await self.session.flush()

    async def clear_incomplete_ingestion_state(self) -> None:
        """Reset transient local ingestion state before Gmail history recovery."""
        statement = (
            update(EmailMessage)
            .where(
                EmailMessage.processing_status.in_(  # type: ignore[attr-defined]
                    (
                        "unprocessed",
                        "cv_processing",
                        "ai_unavailable",
                        "classification_failed",
                    )
                )
            )
            .values(
                processing_status="unprocessed",
                category=None,
                retry_count=0,
                processing_error=None,
                next_retry_at=None,
                last_retry_at=None,
                is_permanently_failed=False,
            )
        )
        await self.session.execute(statement)
        await self.session.flush()

    async def update_labels(
        self, gmail_message_id: str, label_ids: list[str]
    ) -> EmailMessage | None:
        """Update the label_ids field for an email message.

        Args:
            gmail_message_id: The Gmail message ID of the record to update.
            label_ids: The new list of Gmail label IDs.

        Returns:
            The updated EmailMessage entity if found, None otherwise.
        """
        statement = select(EmailMessage).where(EmailMessage.gmail_message_id == gmail_message_id)
        result = await self.session.execute(statement)
        message = result.scalars().first()

        if message is None:
            return None

        message.label_ids = label_ids
        message.updated_at = datetime.now(UTC)
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_by_user(
        self, user_id: UUID, limit: int = 50, offset: int = 0, category: str | None = None
    ) -> list[EmailMessage]:
        """List email messages for a user, ordered by received_at descending.

        Args:
            user_id: The UUID of the user whose messages to list.
            limit: Maximum number of messages to return.
            offset: Number of messages to skip for pagination.
            category: Optional category to filter by.

        Returns:
            A list of EmailMessage entities ordered by most recent first.
        """
        statement = (
            select(EmailMessage)
            .where(EmailMessage.user_id == user_id)
            .order_by(EmailMessage.received_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        if category is not None:
            statement = statement.where(EmailMessage.category == category)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def count_by_user(self, user_id: UUID, category: str | None = None) -> int:
        """Count email messages for a user, optionally filtered by category.

        Args:
            user_id: The UUID of the user whose messages to count.
            category: Optional category to filter by.

        Returns:
            Total number of matching EmailMessage entities.
        """
        statement = (
            select(func.count()).select_from(EmailMessage).where(EmailMessage.user_id == user_id)
        )
        if category is not None:
            statement = statement.where(EmailMessage.category == category)
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_unprocessed_by_gmail_ids(
        self, user_id: UUID, gmail_message_ids: list[str]
    ) -> list[EmailMessage]:
        """Retrieve still-unprocessed email messages for a user by Gmail message ID.

        Re-queries by ID after a write to get session-attached instances, since
        ``batch_upsert()`` uses Core INSERT and returns transient objects that
        cannot be handed to downstream ORM operations (e.g. classification).

        Args:
            user_id: The UUID of the user who owns the emails.
            gmail_message_ids: Gmail message IDs to look up.

        Returns:
            EmailMessage entities still in "unprocessed" status.
        """
        if not gmail_message_ids:
            return []

        statement = (
            select(EmailMessage)
            .where(EmailMessage.user_id == user_id)
            .where(EmailMessage.gmail_message_id.in_(gmail_message_ids))  # type: ignore[attr-defined]
            .where(EmailMessage.processing_status == "unprocessed")
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_failed_messages(
        self, user_id: UUID, permanent_only: bool = False
    ) -> list[EmailMessage]:
        """Retrieve email messages that have failed processing.

        Args:
            user_id: The UUID of the user whose failed messages to retrieve.
            permanent_only: If True, return only permanently failed messages.
                If False, return messages with retry_count > 0 that are not
                permanently failed (eligible for retry).

        Returns:
            A list of EmailMessage entities matching the failure criteria.
        """
        statement = select(EmailMessage).where(EmailMessage.user_id == user_id)

        if permanent_only:
            statement = statement.where(
                EmailMessage.is_permanently_failed == True  # noqa: E712
            )
        else:
            statement = statement.where(
                EmailMessage.retry_count > 0,
                EmailMessage.is_permanently_failed == False,  # noqa: E712
            )

        result = await self.session.execute(statement)
        return list(result.scalars().all())
