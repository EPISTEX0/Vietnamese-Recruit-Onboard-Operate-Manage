"""Custom SQLAlchemy column types shared across modules.

Usage:
    from src.shared.sql_types import EnumAsString, EnumAsText

    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(EnumAsString(UserRole, 20), nullable=False, index=True),
    )
    status: RequestStatus = Field(
        default=RequestStatus.SUBMITTED,
        sa_column=Column(EnumAsText(RequestStatus), nullable=False),
    )
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class _EnumCoercion:
    """Two-way conversion between an ``Enum`` member and its stored string.

    Mixed into the concrete column types below rather than inherited from a
    shared ``TypeDecorator`` base, so each concrete type declares its own
    ``impl`` and nothing has to supply a placeholder one.
    """

    enum_class: type[Enum]

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


class EnumAsString(_EnumCoercion, TypeDecorator[Enum]):
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


class EnumAsText(_EnumCoercion, TypeDecorator[Enum]):
    """Store a string-valued ``Enum`` as ``TEXT`` and load it back as the enum.

    The ``TEXT`` counterpart of :class:`EnumAsString`, carrying the identical
    read/write behaviour and the identical trade-off on out-of-enum values.

    It exists because ``EnumAsString`` cannot express an unbounded column:
    its ``String`` impl renders ``VARCHAR(length)``, and passing no length
    renders bare ``VARCHAR`` -- neither of which is ``TEXT``. Reusing it for a
    ``TEXT`` column would therefore make the next ``alembic revision
    --autogenerate`` emit a ``modify_type`` narrowing real columns, which is
    the whole class of accident this module was introduced to avoid. Keeping
    the storage type exactly as deployed is what makes the conversion
    migration-free.

    There is no length argument to validate against: ``TEXT`` holds any member
    value.

    Args:
        enum_class: The ``Enum`` subclass whose ``.value`` is persisted.
    """

    impl = Text
    cache_ok = True

    def __init__(self, enum_class: type[Enum]) -> None:
        """Bind this column type to ``enum_class``."""
        # Named `enum_class` to match the constructor parameter, for the same
        # compiled-statement cache-key reason documented in EnumAsString.
        self.enum_class = enum_class
        super().__init__()
