"""Knowledge Base domain entities — SQLModel tables for document and chunk storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, DateTime, Index, Text
from sqlmodel import Field, SQLModel

from src.modules.knowledge_base.domain.enums import KnowledgeBaseDocumentStatus

# The six ``ix_kb_*`` / ``ix_emp_kb_*`` indexes below are all declared through
# ``__table_args__`` rather than ``Field(index=True)``. Their names predate the
# tables' current names — 078/079 created them as ``ix_kb_chunks_*`` while the
# tables are ``hr_knowledge_base_chunks`` — so ``index=True`` would render
# ``ix_hr_knowledge_base_chunks_document_id`` instead, and autogenerate would
# propose dropping the real index to create a differently-named twin.
#
# The two ``embedding`` indexes are pgvector HNSW, which needs both
# ``postgresql_using`` (the access method) and ``postgresql_ops`` (the opclass
# tying the index to the ``<=>`` operator ``search_similar_chunks`` uses). 088
# created them; that revision has the numbers behind hnsw-not-ivfflat and the
# default build parameters.
#
# **Neither keyword is checked by the drift fence.** ``compare_metadata``
# compares an index by name, columns and uniqueness only; dropping
# ``postgresql_using``, dropping ``postgresql_ops``, or swapping the opclass to
# ``vector_l2_ops`` each still measures 0 diffs against a database holding the
# real hnsw/cosine index (verified by mutating this file against a migrated
# database). Only deleting the ``Index(...)`` outright registers, as
# ``remove_index``. So these two keywords are load-bearing but unfenced by
# `tests/test_schema_drift_ceiling.py`, and
# `tests/modules/knowledge_base/test_embedding_index.py` guards them directly
# instead -- on this side by reading the declarations below, and on the database
# side by reading ``pg_am``/``pg_opclass``.
#
# They are not decorative: nothing calls ``SQLModel.metadata.create_all``, so
# they never build anything, but ``alembic revision --autogenerate`` renders
# them into any future migration it writes for these tables. Without them it
# would emit a plain btree index on a 1024-dimensional vector column.
#
# ``sa_type=Text`` below is load-bearing: a bare ``str`` renders SQLModel's
# ``AutoString``, i.e. ``VARCHAR`` with no length. That behaves exactly like
# ``TEXT`` but is a different type name, so autogenerate proposes an ALTER for
# every such column. The migrations wrote ``sa.Text()`` and the database has
# ``TEXT`` -- the model is the imprecise side. docs/schema-drift-audit.md 5.5.
#
# The ``status`` field on both document entities below stays annotated ``str``
# rather than ``KnowledgeBaseDocumentStatus`` even though its default is now a
# member of that enum: SQLAlchemy renders an ``Enum``-annotated attribute as a
# native Postgres ENUM type, not ``VARCHAR``, which would trigger an unwanted
# schema-drift migration (verified: annotating the field as the enum changes
# the inferred column type from ``AutoString(length=20)`` to
# ``Enum('PENDING', 'READY', ...)``). The enum is a Python-side contract only,
# matching ``CalendarConflictStatus`` (#356).


class KnowledgeBaseDocument(SQLModel, table=True):
    """HR knowledge base document metadata.

    Tracks uploaded PDF/DOCX files and their ingestion status.
    The actual file bytes are stored in MinIO (bucket ``knowledge-base``).
    Chunks are stored in :class:`KnowledgeBaseChunk`.
    """

    __tablename__ = "hr_knowledge_base_documents"
    __table_args__ = (
        Index("ix_kb_documents_kb_type", "kb_type"),
        Index("ix_kb_documents_status", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    display_name: str = Field(max_length=500)
    category: str = Field(default="general", max_length=100)
    file_name: str = Field(max_length=500)
    storage_path: str = Field(max_length=1000)
    file_size: int = Field(sa_type=BigInteger)
    mime_type: str = Field(max_length=200)
    # Values: KnowledgeBaseDocumentStatus. Stays ``str``-typed -- see file header comment above.
    status: str = Field(
        default=KnowledgeBaseDocumentStatus.PENDING,
        max_length=20,
        sa_column_kwargs={"comment": "pending | processing | ready | error"},
    )
    error_message: str | None = Field(default=None, sa_type=Text)
    chunk_count: int = Field(default=0)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    kb_type: str = Field(
        default="hr",
        max_length=20,
        sa_column_kwargs={
            "comment": "hr (default) — the type of knowledge base this doc belongs to"
        },
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class KnowledgeBaseChunk(SQLModel, table=True):
    """A text chunk with its pgvector embedding for a knowledge base document.

    Each document is split into chunks of ~512 tokens (overlap 50).
    The ``embedding`` column stores a 1024-dimensional vector produced by the
    vroom-embedding service, which hosts no model of its own: it proxies to the
    OpenAI-compatible endpoint and model the operator configures via
    ``EMBEDDING_API_BASE_URL`` / ``EMBEDDING_MODEL_NAME`` (ADR 0012). The column
    width is fixed at 1024, so ``EMBEDDING_DIMENSIONS`` must stay 1024 and the
    service refuses to start otherwise — changing models means picking one that
    emits 1024 dimensions, or migrating this column.
    """

    __tablename__ = "hr_knowledge_base_chunks"
    __table_args__ = (
        Index("ix_kb_chunks_document_id", "document_id"),
        Index(
            "ix_kb_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="hr_knowledge_base_documents.id",
        ondelete="CASCADE",
    )
    chunk_index: int
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(1024), nullable=True),
    )
    token_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EmployeeKnowledgeBaseDocument(SQLModel, table=True):
    """Employee knowledge base document metadata.

    Tracks uploaded PDF/DOCX files and their ingestion status for the Employee KB.
    The actual file bytes are stored in MinIO (bucket ``knowledge-base``).
    Chunks are stored in :class:`EmployeeKnowledgeBaseChunk`.

    Physically separate from HR KB tables for security isolation (Issue #260).
    """

    __tablename__ = "employee_knowledge_base_documents"
    __table_args__ = (
        Index("ix_emp_kb_documents_kb_type", "kb_type"),
        Index("ix_emp_kb_documents_status", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    display_name: str = Field(max_length=500)
    category: str = Field(default="general", max_length=100)
    file_name: str = Field(max_length=500)
    storage_path: str = Field(max_length=1000)
    file_size: int = Field(sa_type=BigInteger)
    mime_type: str = Field(max_length=200)
    # Values: KnowledgeBaseDocumentStatus. Stays ``str``-typed -- see file header comment above.
    status: str = Field(
        default=KnowledgeBaseDocumentStatus.PENDING,
        max_length=20,
        sa_column_kwargs={"comment": "pending | processing | ready | error"},
    )
    error_message: str | None = Field(default=None, sa_type=Text)
    chunk_count: int = Field(default=0)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    kb_type: str = Field(
        default="employee",
        max_length=20,
        sa_column_kwargs={"comment": "employee — the type of knowledge base this doc belongs to"},
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EmployeeKnowledgeBaseChunk(SQLModel, table=True):
    """A text chunk with its pgvector embedding for an Employee KB document.

    Each document is split into chunks of ~512 tokens (overlap 50).
    The ``embedding`` column stores a 1024-dimensional vector produced by the
    vroom-embedding service, which hosts no model of its own: it proxies to the
    OpenAI-compatible endpoint and model the operator configures via
    ``EMBEDDING_API_BASE_URL`` / ``EMBEDDING_MODEL_NAME`` (ADR 0012). Same fixed
    1024-wide column as :class:`KnowledgeBaseChunk`.
    """

    __tablename__ = "employee_knowledge_base_chunks"
    __table_args__ = (
        Index("ix_emp_kb_chunks_document_id", "document_id"),
        Index(
            "ix_emp_kb_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="employee_knowledge_base_documents.id",
        ondelete="CASCADE",
    )
    chunk_index: int
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(1024), nullable=True),
    )
    token_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
