"""Guard: both KB ``embedding`` columns carry an HNSW index with the right opclass.

pgvector picks an index by opclass -- ``vector_cosine_ops`` serves ``<=>`` and
nothing else. ``KnowledgeBaseRepository.search_similar_chunks`` ranks with
``Column.cosine_distance()``, which renders ``<=>``, so an ``l2`` or ``ip``
index would sit there unused while every similarity search stayed a sequential
scan over 4 KB-per-row vectors. Existing-and-named is therefore not enough to
assert; the access method and the opclass are the point.

Two halves, because the drift fence holds neither of them:

* the **database** side reads ``pg_am``/``pg_opclass`` on a database at
  ``alembic upgrade head``, so deleting revision 088 turns it red;
* the **model** side reads the ``Index`` declarations, because
  ``compare_metadata`` compares an index by name, columns and uniqueness only.
  Dropping ``postgresql_using``, dropping ``postgresql_ops`` or swapping the
  opclass to ``vector_l2_ops`` each measures 0 diffs against a database holding
  the real hnsw/cosine index. See ``docs/schema-drift-audit.md`` section 7.1.

Whether the query is *shaped* so the planner can use these indexes is a
separate question, and a separate guard.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.knowledge_base.domain.entities import (
    EmployeeKnowledgeBaseChunk,
    KnowledgeBaseChunk,
)

pytestmark = pytest.mark.asyncio

# (table, index name) for both knowledge bases. Both are `vector(1024)` columns
# queried through the same repository method, so anything true of one must hold
# for the other -- the Employee KB is a physically separate copy (Issue #260),
# which is exactly how one of them could quietly go without an index.
EMBEDDING_INDEXES = [
    ("hr_knowledge_base_chunks", "ix_kb_chunks_embedding_hnsw"),
    ("employee_knowledge_base_chunks", "ix_emp_kb_chunks_embedding_hnsw"),
]

MODEL_INDEXES = [
    (KnowledgeBaseChunk, "ix_kb_chunks_embedding_hnsw"),
    (EmployeeKnowledgeBaseChunk, "ix_emp_kb_chunks_embedding_hnsw"),
]

# What the index must be, not merely that some index is there. `pg_am.amname`
# is the access method; `pg_opclass.opcname` is what ties it to `<=>`.
EXPECTED_ACCESS_METHOD = "hnsw"
EXPECTED_OPCLASS = "vector_cosine_ops"

INDEX_SHAPE_SQL = text("""
SELECT am.amname AS access_method,
       opc.opcname AS opclass,
       att.attname AS column_name
FROM pg_index i
JOIN pg_class idx ON idx.oid = i.indexrelid
JOIN pg_am am ON am.oid = idx.relam
JOIN pg_opclass opc ON opc.oid = i.indclass[0]
JOIN pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = i.indkey[0]
WHERE idx.relname = :index_name
""")


@pytest_asyncio.fixture
async def session(postgres_async_url: str):
    """A session on the migrated container database, rolled back afterwards.

    ``postgres_async_url`` is session-scoped and shared with every other
    database test, so nothing written through this session may outlive it.
    """
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


@pytest.mark.parametrize(("table", "index_name"), EMBEDDING_INDEXES)
async def test_embedding_columns_carry_an_hnsw_cosine_index(
    session: AsyncSession, table: str, index_name: str
) -> None:
    """Both ``embedding`` columns must be indexed for the operator the query uses."""
    row = (await session.execute(INDEX_SHAPE_SQL, {"index_name": index_name})).one_or_none()

    assert row is not None, (
        f"{table}.embedding has no index named {index_name}. Every similarity "
        "search over this table is a sequential scan that detoasts one 4 KB "
        "vector per row."
    )
    assert row.column_name == "embedding", (
        f"{index_name} indexes {row.column_name!r}, not `embedding`."
    )
    assert row.access_method == EXPECTED_ACCESS_METHOD, (
        f"{index_name} uses access method {row.access_method!r}, expected "
        f"{EXPECTED_ACCESS_METHOD!r}."
    )
    assert row.opclass == EXPECTED_OPCLASS, (
        f"{index_name} uses opclass {row.opclass!r}, but "
        "`search_similar_chunks` queries with `<=>` (cosine distance), which "
        f"only {EXPECTED_OPCLASS!r} serves. The index would never be chosen."
    )


@pytest.mark.parametrize(("entity", "index_name"), MODEL_INDEXES)
async def test_model_declares_the_access_method_and_opclass(entity: type, index_name: str) -> None:
    """The model must say hnsw/cosine too -- ``compare_metadata`` will not notice.

    Measured against a database at ``alembic upgrade head`` holding the real
    hnsw index: removing ``postgresql_using``, removing ``postgresql_ops``, or
    changing the opclass to ``vector_l2_ops`` each leaves the drift probe
    reporting **0 diffs**. Only deleting the ``Index(...)`` entirely shows up
    (as ``remove_index``). The drift fence therefore cannot hold this, and
    without something that does, the next
    ``alembic revision --autogenerate`` touching these tables would render a
    plain btree index on a 1024-dimensional vector column.
    """
    index = next(
        (candidate for candidate in entity.__table__.indexes if candidate.name == index_name),
        None,
    )

    assert index is not None, (
        f"{entity.__name__} no longer declares {index_name}; the database still "
        "has it, so autogenerate would now propose dropping it."
    )
    assert index.dialect_options["postgresql"]["using"] == EXPECTED_ACCESS_METHOD, (
        f'{index_name} is declared without `postgresql_using="{EXPECTED_ACCESS_METHOD}"`, '
        "so the model describes a btree index on a vector(1024) column."
    )
    assert index.dialect_options["postgresql"]["ops"] == {"embedding": EXPECTED_OPCLASS}, (
        f"{index_name} is declared with opclass "
        f"{index.dialect_options['postgresql']['ops']!r}, but the query uses "
        f"`<=>`, which only {EXPECTED_OPCLASS!r} serves."
    )
