"""Custom SQLAlchemy column types shared across modules.

Usage:
    from src.shared.sql_types import EnumAsString

    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(EnumAsString(UserRole, 20), nullable=False, index=True),
    )
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class EnumAsString(TypeDecorator[Enum]):
    """Store a string-valued ``Enum`` as ``VARCHAR`` and load it back as the enum.

    A bare ``Column(String(n))`` under an ``Enum`` annotation only works in one
    direction. Writes succeed because a ``str``-mixin enum member *is* a string,
    but reads hand back a plain ``str``: SQLAlchemy has no type information to
    reconstruct the member with. The annotation then lies for every row loaded
    from the database, and the mismatch stays invisible as long as the code only
    compares (``member == "hr"`` is ``True`` for a ``str``-mixin enum) -- until
    something touches ``.name``/``.value`` and gets ``AttributeError``.

    This decorator closes that gap without changing the DDL: the emitted column
    is still ``VARCHAR(length)``, so it needs no migration and Alembic
    autogenerate sees no drift.

    Values outside the enum raise ``ValueError`` on read rather than degrading
    to ``str``. This is deliberate but it is not a per-row failure: one bad
    value fails the whole result set, so a single unmigrated row would break
    every query selecting that table, not just the record holding it. The
    trade is accepted because the application only ever writes enum members,
    the sole documented producer of a foreign value is a migration
    ``downgrade()`` (which leaves the schema off head anyway), and a row whose
    role no guard recognises is a silently unauthorised account -- a state
    worth surfacing rather than tolerating.

    Args:
        enum_class: The ``Enum`` subclass whose ``.value`` is persisted.
        length: The ``VARCHAR`` length. Must fit the longest member value.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[Enum], length: int) -> None:
        """Bind this column type to ``enum_class`` at ``length`` characters."""
        longest = max(enum_class, key=lambda member: len(str(member.value)))
        if len(str(longest.value)) > length:
            raise ValueError(
                f"{enum_class.__name__}.{longest.name} = '{longest.value}' is "
                f"{len(str(longest.value))} characters but the column holds only {length}."
            )
        # Named `enum_class` to match the constructor parameter: SQLAlchemy
        # builds this type's compiled-statement cache key by reading
        # `__init__` argument names off the instance, and an argument with no
        # matching attribute downgrades caching. (`length` is not in the key --
        # it lives on the impl -- so two instances of the same enum at
        # different widths share a key. Harmless: width does not affect any
        # emitted SQL, only DDL.)
        self.enum_class = enum_class
        super().__init__(length)

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        """Convert an enum member (or its raw value) to the stored string."""
        if value is None:
            return None
        return str(self.enum_class(value).value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Enum | None:
        """Rebuild the enum member from the stored string."""
        if value is None:
            return None
        return self.enum_class(value)
