"""The single query path from a client-supplied id to a chat session.

Every assistant handler that accepts a ``session_id`` from the client used to
run its own ``select(AssistantChatSession).where(id == ...)`` and act on the
result.  That query answers "does this row exist", never "may this caller touch
it", so any authenticated user could operate on any session.

The fix is structural rather than a check bolted onto each handler: this module
owns the only lookup, and the only lookup it offers requires an owner.  There is
no ``get_by_id``.  A handler written next month cannot forget the ownership
filter, because the repository exposes no way to fetch a session without one.

Ownership means ``user_id`` — the login account that started the session.
``AssistantChatSession`` documents ``user_id`` as a NOT NULL foreign key to
``users.id`` and ``employee_id`` as an *optional* link to a personnel record;
HR sessions leave ``employee_id`` NULL entirely, so it cannot be the owner.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.assistant.infrastructure.quality_models import AssistantChatSession


class AssistantSessionRepository:
    """Resolves assistant chat sessions, always scoped to their owner."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an active database session."""
        self._session = session

    async def get_owned(
        self,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> AssistantChatSession | None:
        """Return the session with ``session_id`` iff ``owner_user_id`` owns it.

        Returns ``None`` both when no such row exists and when the row belongs
        to someone else — the two cases are deliberately not distinguished, so
        callers cannot accidentally build a response that reveals which one
        happened.

        Args:
            session_id: The session id supplied by the client.
            owner_user_id: The authenticated caller's ``users.id``.

        Returns:
            The owned session, or ``None``.
        """
        result = await self._session.execute(
            select(AssistantChatSession).where(
                AssistantChatSession.id == session_id,
                AssistantChatSession.user_id == owner_user_id,
            )
        )
        return result.scalar_one_or_none()
