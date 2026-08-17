"""Composite whitelist manager with cache.

Merges database-backed entries into a unified whitelist with in-memory caching.
Supports exact email matching and domain pattern matching, both case-insensitive.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from src.modules.identity.domain.entities import (
    User,
    WhitelistEntry,
    WhitelistEntryType,
)
from src.modules.identity.infrastructure.whitelist_repository import WhitelistRepository

# Simple email regex for validation (RFC 5322 simplified)
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Domain pattern regex: must start with @ followed by valid domain
_DOMAIN_PATTERN_REGEX = re.compile(
    r"^@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


@dataclass
class WhitelistEntryResponse:
    """Response representation of a whitelist entry.

    Attributes:
        id: Unique identifier (None for file-based entries).
        value: The email or domain pattern value.
        entry_type: Whether this is an exact email or domain pattern.
        added_by_email: Email of the admin who added the entry.
        created_at: When the entry was created.
        source: Origin of the entry ('database' or 'file').
        is_readonly: Whether the entry can be modified (file entries are read-only).
    """

    id: UUID | None
    value: str
    entry_type: WhitelistEntryType
    added_by_email: str
    created_at: datetime | None
    source: Literal["database", "file"]
    is_readonly: bool


class WhitelistManager:
    """Composite whitelist manager merging database entries.

    Provides email access control by checking both exact email matches and
    domain pattern matches from a database-backed whitelist (CRUD-capable).
    Results are cached in memory for fast lookups.

    Args:
        repo: The WhitelistRepository for database operations.
    """

    def __init__(
        self,
        repo: WhitelistRepository,
    ) -> None:
        self._repo = repo
        self._cache_exact: set[str] = set()
        self._cache_domains: set[str] = set()
        self._cache_timestamp: float = 0.0
        self._cache_loaded: bool = False

    async def _ensure_cache(self) -> None:
        """Load cache if not yet initialized."""
        if not self._cache_loaded:
            await self.refresh_cache()

    async def refresh_cache(self) -> None:
        """Reload the in-memory cache from database.

        Rebuilds the exact email set and domain pattern set from database entries.
        """
        exact_emails: set[str] = set()
        domain_patterns: set[str] = set()

        # Load database entries
        db_entries = await self._repo.get_all()
        for entry in db_entries:
            normalized = entry.value.lower()
            if entry.entry_type == WhitelistEntryType.DOMAIN_PATTERN:
                domain_patterns.add(normalized)
            else:
                exact_emails.add(normalized)

        self._cache_exact = exact_emails
        self._cache_domains = domain_patterns
        self._cache_timestamp = time.time()
        self._cache_loaded = True

    def is_allowed(self, email: str) -> bool:
        """Check if an email is allowed by the whitelist.

        Performs case-insensitive matching against both exact email entries
        and domain pattern entries. The cache must be loaded before calling
        this method (call refresh_cache() or any async method first).

        Args:
            email: The email address to check.

        Returns:
            True if the email matches an exact entry or a domain pattern.
        """
        normalized = email.lower()

        # Check exact email match
        if normalized in self._cache_exact:
            return True

        # Check domain pattern match
        if "@" in normalized:
            domain_part = normalized[normalized.index("@") :]
            if domain_part in self._cache_domains:
                return True

        return False

    async def is_allowed_async(self, email: str) -> bool:
        """Async version of is_allowed that ensures cache is loaded.

        Args:
            email: The email address to check.

        Returns:
            True if the email matches an exact entry or a domain pattern.
        """
        await self._ensure_cache()
        return self.is_allowed(email)

    async def add_entry(self, value: str, admin: User) -> WhitelistEntry:
        """Add a new whitelist entry with auto-detection of entry type.

        Validates the input format, detects whether it's an exact email or
        domain pattern, checks for duplicates, persists to database, and
        updates the cache.

        Args:
            value: The email address or domain pattern (starting with @) to add.
            admin: The admin user performing the action.

        Returns:
            The persisted WhitelistEntry entity.

        Raises:
            HTTPException: 422 if the format is invalid, 409 if duplicate.
        """
        stripped = value.strip()
        entry_type = self._detect_entry_type(stripped)

        # Validate format
        if entry_type is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "WHITELIST_INVALID_FORMAT",
                    "message": (
                        f"'{stripped}' không đúng định dạng. "
                        "Vui lòng nhập email hợp lệ (vd: ten@congty.com) "
                        "hoặc tên miền (vd: @congty.com)."
                    ),
                },
            )

        # Check for duplicates (case-insensitive)
        if await self._repo.exists(stripped):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WHITELIST_DUPLICATE",
                    "message": f"'{stripped}' đã có trong danh sách truy cập.",
                },
            )

        # Create and persist the entry
        entry = WhitelistEntry(
            value=stripped.lower(),
            entry_type=entry_type,
            added_by_user_id=admin.id,
        )
        persisted = await self._repo.add(entry)

        # Update cache immediately
        normalized = stripped.lower()
        if entry_type == WhitelistEntryType.DOMAIN_PATTERN:
            self._cache_domains.add(normalized)
        else:
            self._cache_exact.add(normalized)

        return persisted

    async def remove_entry(self, entry_id: UUID, admin: User) -> None:
        """Remove a whitelist entry by its ID.

        Removes the entry from the database and updates the in-memory cache.

        Args:
            entry_id: The UUID of the entry to remove.
            admin: The admin user performing the action.

        Raises:
            HTTPException: 404 if the entry does not exist.
        """
        # Verify entry exists before removal
        db_entries = await self._repo.get_all()
        target_entry = None
        for entry in db_entries:
            if entry.id == entry_id:
                target_entry = entry
                break

        if target_entry is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "WHITELIST_NOT_FOUND",
                    "message": f"Whitelist entry not found: {entry_id}",
                },
            )

        await self._repo.remove(entry_id)

        # Update cache: remove the value
        normalized = target_entry.value.lower()
        if target_entry.entry_type == WhitelistEntryType.DOMAIN_PATTERN:
            self._cache_domains.discard(normalized)
        else:
            self._cache_exact.discard(normalized)

    async def list_entries(self) -> list[WhitelistEntryResponse]:
        """List all whitelist entries from both file and database sources.

        Merges entries from both sources. File-based entries are marked as
        read-only. When an entry exists in both sources, the database entry
        takes precedence for metadata display (deduplication).

        Returns:
            A list of WhitelistEntryResponse objects representing all entries.
        """
        responses: list[WhitelistEntryResponse] = []
        seen_values: set[str] = set()

        # Database entries take precedence
        db_entries = await self._repo.get_all()
        for entry in db_entries:
            normalized = entry.value.lower()
            seen_values.add(normalized)

            # Look up the admin email (we store user_id, but for response we need email)
            added_by_email = await self._get_admin_email(entry.added_by_user_id)

            responses.append(
                WhitelistEntryResponse(
                    id=entry.id,
                    value=entry.value,
                    entry_type=entry.entry_type,
                    added_by_email=added_by_email,
                    created_at=entry.created_at,
                    source="database",
                    is_readonly=False,
                )
            )

        return responses

    async def _get_admin_email(self, user_id: UUID) -> str:
        """Look up admin email by user ID.

        Args:
            user_id: The UUID of the admin user.

        Returns:
            The admin's email address, or 'unknown' if not found.
        """
        email = await self._repo.get_user_email(user_id)
        return email if email is not None else "unknown"

    @staticmethod
    def _detect_entry_type(value: str) -> WhitelistEntryType | None:
        """Detect whether a value is an exact email or domain pattern.

        Args:
            value: The input string to classify.

        Returns:
            WhitelistEntryType.DOMAIN_PATTERN if it starts with @ and is valid,
            WhitelistEntryType.EXACT_EMAIL if it's a valid email address,
            None if the format is invalid.
        """
        if value.startswith("@"):
            if _DOMAIN_PATTERN_REGEX.match(value):
                return WhitelistEntryType.DOMAIN_PATTERN
            return None
        else:
            if _EMAIL_REGEX.match(value):
                return WhitelistEntryType.EXACT_EMAIL
            return None
