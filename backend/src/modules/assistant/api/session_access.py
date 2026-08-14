"""Turning a client-supplied session id into a session the caller may touch.

Handlers depend on :func:`get_chat_session_guard` and ask the guard for the
session instead of querying for it.  That keeps one copy of three decisions
that were previously re-made (and mis-made) per handler:

* which column expresses ownership — ``user_id``, the login account;
* what a caller who does not own the session is told — the same 404, with the
  same body, as a session id that was never issued;
* what a malformed id means — also not-found, rather than the 500 that
  ``uuid.UUID(...)`` used to raise straight out of the handler body.

Both routers share the guard.  The HR router authenticates a ``User`` and the
ESS router an ``Employee``, but the owning column is ``users.id`` either way, so
the guard depends on ``get_current_user`` directly and both sides get identical
semantics rather than two implementations that drift.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.assistant.infrastructure.quality_models import AssistantChatSession
from src.modules.assistant.infrastructure.session_repository import (
    AssistantSessionRepository,
)
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User

# Deliberately identical to the message a genuinely unknown id has always
# produced. Anything more specific for the "not yours" case would tell an
# attacker which session ids are real.
SESSION_NOT_FOUND_DETAIL = "Session not found"


def session_not_found() -> HTTPException:
    """Build the one refusal every unusable session id gets.

    Shared rather than raised inline so a caller that has to refuse outside
    :meth:`ChatSessionGuard.require` — the HR feedback handler audits the
    refusal first — cannot word it differently and reopen the existence leak.
    """
    return HTTPException(status_code=404, detail=SESSION_NOT_FOUND_DETAIL)


class ChatSessionGuard:
    """Resolves session ids for one authenticated caller."""

    def __init__(self, repository: AssistantSessionRepository, owner_user_id: UUID) -> None:
        """Bind the guard to the caller whose sessions it will resolve."""
        self._repository = repository
        self._owner_user_id = owner_user_id

    async def require(self, session_id: str | UUID) -> AssistantChatSession:
        """Return the caller's session, or raise 404.

        Args:
            session_id: The id as it arrived from the client.

        Returns:
            The chat session owned by the caller.

        Raises:
            HTTPException: 404 if the id is malformed, unknown, or owned by
                somebody else. The three are indistinguishable by design.
        """
        chat_session = await self.resolve_optional(session_id)
        if chat_session is None:
            raise session_not_found()
        return chat_session

    async def resolve_optional(
        self,
        session_id: str | UUID | None,
    ) -> AssistantChatSession | None:
        """Return the caller's session, or ``None`` if it is not theirs to use.

        For the chat endpoints, where ``session_id`` only correlates telemetry
        and the conversation itself does not depend on it. Chat has always
        tolerated an id that resolves to nothing — the frontend can hold a
        stale one across a restart — so an unusable id keeps meaning "record no
        telemetry" rather than becoming a mid-conversation error. The security
        property is that telemetry is never attributed to a session the caller
        does not own; refusing the chat turn is not needed to get that, and
        would break a legitimate client holding a stale id.
        """
        if session_id is None:
            return None
        try:
            parsed = UUID(str(session_id))
        except (ValueError, AttributeError, TypeError):
            return None
        return await self._repository.get_owned(parsed, self._owner_user_id)


async def get_chat_session_guard(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ChatSessionGuard:
    """Provide a :class:`ChatSessionGuard` scoped to the authenticated caller."""
    return ChatSessionGuard(
        repository=AssistantSessionRepository(session),
        owner_user_id=current_user.id,
    )


ChatSessionGuardDep = Annotated[ChatSessionGuard, Depends(get_chat_session_guard)]
