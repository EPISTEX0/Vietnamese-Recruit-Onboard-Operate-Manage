"""Repository for WhitelistEntry entity CRUD operations.

Provides async database access for whitelist entry creation, removal,
listing, and existence checks using SQLAlchemy async sessions with SQLModel.
"""

from uuid import UUID

from sqlalchemy import desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.identity.domain.entities import User, WhitelistEntry


class WhitelistRepository:
    """Handles WhitelistEntry entity persistence using async SQLAlchemy sessions.

    Provides methods for adding, removing, listing, and checking existence
    of whitelist entries (exact emails and domain patterns).

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def add(self, entry: WhitelistEntry) -> WhitelistEntry:
        """Add a new whitelist entry to the database.

        Persists the given WhitelistEntry and flushes to obtain
        any database-generated defaults.

        Args:
            entry: The WhitelistEntry entity to persist.

        Returns:
            The persisted WhitelistEntry entity.
        """
        self.session.add(entry)
        await self.session.flush()
        await self.session.commit()
        return entry

    async def remove(self, entry_id: UUID) -> None:
        """Remove a whitelist entry by its unique identifier.

        If no entry with the given ID exists, this method is a no-op.

        Args:
            entry_id: The UUID primary key of the entry to remove.
        """
        statement = select(WhitelistEntry).where(WhitelistEntry.id == entry_id)
        result = await self.session.execute(statement)
        entry = result.scalars().first()

        if entry is not None:
            await self.session.delete(entry)
            await self.session.flush()
            await self.session.commit()

    async def get_all(self) -> list[WhitelistEntry]:
        """Retrieve all whitelist entries from the database.

        Returns entries ordered by creation timestamp descending
        (most recent first).

        Returns:
            A list of all WhitelistEntry entities in the database.
        """
        statement = select(WhitelistEntry).order_by(
            desc(WhitelistEntry.created_at)  # type: ignore[arg-type]
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def exists(self, value: str) -> bool:
        """Check if a whitelist entry with the given value already exists.

        Performs a case-insensitive comparison against the entry value
        to detect duplicates.

        Args:
            value: The email address or domain pattern to check.

        Returns:
            True if an entry with the given value exists, False otherwise.
        """
        statement = select(WhitelistEntry).where(func.lower(WhitelistEntry.value) == value.lower())
        result = await self.session.execute(statement)
        return result.scalars().first() is not None

    async def get_user_email(self, user_id: UUID) -> str | None:
        """Look up a User's email by id, for display alongside whitelist entries.

        Args:
            user_id: The UUID of the admin user who added an entry.

        Returns:
            The user's email address, or None if no such user exists.
        """
        statement = select(User).where(User.id == user_id)
        result = await self.session.execute(statement)
        user = result.scalars().first()
        return user.email if user else None
