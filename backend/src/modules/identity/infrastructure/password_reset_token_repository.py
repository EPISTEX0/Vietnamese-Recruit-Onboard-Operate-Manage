"""Repository for PasswordResetToken entity CRUD operations.

Provides async database access for password reset token creation,
lookup, invalidation, and usage tracking using SQLAlchemy async
sessions with SQLModel.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.identity.domain.entities import PasswordResetToken


class PasswordResetTokenRepository:
    """Handles PasswordResetToken entity persistence using async SQLAlchemy sessions.

    Enforces the single active reset token invariant: a user may have at
    most one usable reset token at a time, so requesting a new token
    invalidates all previously active ones.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(
        self,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
        created_by_ip: str | None = None,
    ) -> PasswordResetToken:
        """Create a new password reset token record in the database.

        Persists the SHA-256 hash of the raw reset token along with its
        expiry and the optional client IP that requested the reset. The
        raw token itself is never stored.

        Args:
            user_id: The UUID of the user requesting the reset.
            token_hash: The SHA-256 hex digest of the raw token.
            expires_at: When the token should expire.
            created_by_ip: Optional client IP address from the request.

        Returns:
            The newly created PasswordResetToken entity.
        """
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by_ip=created_by_ip,
        )
        self.session.add(token)
        await self.session.flush()
        return token

    async def find_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        """Look up a password reset token by its SHA-256 hash.

        Args:
            token_hash: The SHA-256 hex digest of the raw token.

        Returns:
            The PasswordResetToken entity if found, None otherwise.
        """
        statement = select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def invalidate_all_active_for_user(self, user_id: UUID) -> None:
        """Invalidate all active password reset tokens for a user.

        Sets used_at to the current UTC time on every token belonging to
        the specified user that has not already been used. This enforces
        the single active reset token invariant.

        Args:
            user_id: The UUID of the user whose tokens to invalidate.
        """
        statement = select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at == None,  # noqa: E711
        )
        result = await self.session.execute(statement)
        tokens = result.scalars().all()

        now = datetime.now(UTC)
        for token in tokens:
            token.used_at = now
            self.session.add(token)

        if tokens:
            await self.session.flush()

    async def mark_used(self, token_id: UUID) -> None:
        """Mark a password reset token as used.

        Sets used_at to the current UTC time on the token matching the
        given id. No-op when no such token exists.

        Args:
            token_id: The UUID primary key of the token to mark used.
        """
        statement = select(PasswordResetToken).where(PasswordResetToken.id == token_id)
        result = await self.session.execute(statement)
        token = result.scalars().first()

        if token is not None:
            token.used_at = datetime.now(UTC)
            self.session.add(token)
            await self.session.flush()
