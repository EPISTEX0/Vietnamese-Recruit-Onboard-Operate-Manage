"""Guards for the two halves of "KB similarity search uses an index".

An index on a ``vector`` column only earns its keep if **both** of these hold,
and each fails silently on its own -- the query keeps returning the right rows
either way, just by reading every chunk in the table.

1. **The index exists, with the opclass that matches the operator.** pgvector
   picks an index by opclass: ``vector_cosine_ops`` serves ``<=>`` and nothing
   else. ``KnowledgeBaseRepository.search_similar_chunks`` calls
   ``Column.cosine_distance()``, which renders ``<=>``, so an ``l2`` or
   ``ip`` index would sit there unused. ``test_embedding_columns_carry_...``
   reads ``pg_am``/``pg_opclass`` on a database at ``alembic upgrade head``,
   so deleting the migration turns it red.

2. **The query is shaped so the planner may use it.** This is the half that is
   easy to lose. pgvector can only answer ``ORDER BY <indexed column> <operator>
   <constant>``, ascending, with nothing wrapped around it. Measured on a
   50k-row table (1024-dim vectors, ``pgvector/pgvector:pg15``, pgvector 0.8.6):

   =========================================================  ========
   query shape (index present for all three)                  median
   =========================================================  ========
   ``ORDER BY 1.0 - (embedding <=> q) DESC``  (the original)   87.2 ms
   ``ORDER BY embedding <=> q`` **and** ``WHERE ... <=> q <``   90.6 ms
   ``ORDER BY embedding <=> q``, threshold applied after        1.34 ms
   =========================================================  ========

   The first two are sequential scans, for two independent reasons, and fixing
   only one of them buys nothing: ``1.0 - x DESC`` is not an ordering pgvector
   recognises even though it is the same order, and the planner does not
   rewrite it; and the distance predicate in ``WHERE`` cuts the row estimate
   far enough (50 000 down to 16 667) that the seq-scan path wins on estimated
   cost. Only the third form is index-servable, which is why
   ``search_similar_chunks`` orders by the raw distance and applies
   ``similarity_threshold`` to the already-limited rows.

   ``test_first_statement_orders_by_the_bare_distance_operator`` reads the
   compiled SQL rather than a query plan. Estimated costs for the two paths sit
   close together, because a ``vector(1024)`` is 4 KB and therefore lives in
   TOAST: the chunk table's own heap is 714 pages against 33 334 TOAST pages,
   and a sequential scan is costed on the 714 -- so the estimate misses the
   47x larger read the scan actually performs. In the original shape the two
   paths sat within 0.5% of each other (2539.3 against 2552.7 at 50 000 rows),
   which is the planner's mood on the day rather than a decision; the shape
   below widens that to the index path being 8.1% cheaper. Either way the query
   *shape* is the part this repository controls, and it is the part that
   regresses.

3. **The exact fallback still runs when the index scan comes up short.** Using
   the index means accepting an approximate scan, and an approximate scan
   yields a bounded candidate set that ``documents.status = 'ready'`` is
   applied to *afterwards*. When every candidate belongs to a document that is
   not ready, the branch returns nothing at all -- not a slightly worse third
   result, an empty answer where the sequential scan found three rows. So
   ``search_similar_chunks`` re-asks exactly whenever a branch is short, and
   ``test_chunks_under_a_non_ready_document_cannot_hide_the_real_matches``
   holds that. It is the piece that keeps the speed-up from being paid for in
   silent wrong answers.

Filtering after ``LIMIT`` returns the same rows as filtering before it. The
threshold is a predicate on distance and the rows are ordered by distance, so
the kept set is a prefix of that order: if at least ``top_k`` rows pass the
threshold, the ``top_k`` nearest overall are exactly those rows; if fewer pass,
both forms return all of them.
"""

from __future__ import annotations

import math
import re
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.knowledge_base.domain.entities import (
    EmployeeKnowledgeBaseChunk,
    KnowledgeBaseChunk,
    KnowledgeBaseDocument,
)
from src.modules.knowledge_base.infrastructure.repository import KnowledgeBaseRepository

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

# Enough non-ready chunks that an HNSW scan's whole candidate set is filtered
# away by the document-status join. Found by measurement, not by arithmetic on
# `hnsw.ef_search`: at 60 the graph is small enough that the scan still reaches
# the ready chunk, at 400 it does not.
_POISON_CHUNKS = 400

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
    database test, so the rows the round-trip test writes must not outlive it.
    Nothing here commits: ``flush()`` makes the rows visible to queries on this
    same connection, which is all the test needs, and the rollback leaves the
    knowledge-base tables as it found them.
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


@pytest.mark.parametrize("kb_types", [["hr"], ["employee"], ["hr", "employee"]])
async def test_first_statement_orders_by_the_bare_distance_operator(
    kb_types: list[str],
) -> None:
    """The statement issued first must rank by the raw ``<=>`` ascending.

    One such ORDER BY per requested knowledge base: each is a separate table
    with a separate index, and each has to be ranked in its own branch to use
    it.
    """
    sql = (await _compiled_search_sql(kb_types))[0]
    indexable = _INDEXABLE_ORDER_BY.findall(sql)

    assert len(indexable) == len(kb_types), (
        f"expected one `ORDER BY <table>.embedding <=> :param` per knowledge base "
        f"({len(kb_types)}), found {len(indexable)}. pgvector matches the bare "
        "`column <=> constant` ascending and nothing else -- wrapping it (say as "
        "`1 - distance` sorted descending) is the same order but forces a full "
        f"scan and a sort:\n{sql}"
    )


@pytest.mark.parametrize("kb_types", [["hr"], ["employee"], ["hr", "employee"]])
async def test_fallback_statement_is_deliberately_not_indexable(
    kb_types: list[str],
) -> None:
    """The fallback must be a form no pgvector index can serve.

    It exists to be exact, and the only way to *guarantee* exactness is to make
    the ordering unmatchable -- ``1 - (embedding <=> q)`` descending. Anything
    that leaves the choice to the planner (``enable_seqscan``, a distance
    predicate in ``WHERE``) would let the fallback silently become another
    approximate scan, which is the one thing it must never be.

    ``_compiled_search_sql`` drives the repository with a session that returns
    no rows, so the short-branch fallback always fires and both statements are
    captured.
    """
    statements = await _compiled_search_sql(kb_types)

    assert len(statements) == 2, (
        "the repository issued "
        f"{len(statements)} statement(s); the approximate scan returned no rows, "
        "so the exact fallback should have followed it"
    )
    assert not _INDEXABLE_ORDER_BY.search(statements[1]), (
        "the fallback statement is now index-servable, so it is no longer "
        f"guaranteed to be exact:\n{statements[1]}"
    )


@pytest.mark.parametrize("kb_types", [["hr"], ["employee"], ["hr", "employee"]])
async def test_distance_threshold_is_not_a_where_predicate(kb_types: list[str]) -> None:
    """``similarity_threshold`` must not become a ``WHERE ... <=> ... <`` predicate."""
    sql = (await _compiled_search_sql(kb_types))[0]
    clauses = _where_clauses(sql)

    assert len(clauses) == len(kb_types), (
        f"expected one WHERE clause per branch ({len(kb_types)}), parsed "
        f"{len(clauses)} -- the assertion below would pass vacuously:\n{sql}"
    )
    for clause in clauses:
        assert "<=>" not in clause, (
            "the distance threshold is back in the WHERE clause. Measured on 50k "
            "rows this alone costs the index path (90.6 ms against 1.34 ms), "
            "even with the ORDER BY correct: the predicate cuts the planner's "
            "row estimate from 50 000 to 16 667 and the seq-scan path then wins on "
            f"estimated cost:\n{clause}"
        )


async def test_search_still_ranks_filters_and_limits_after_the_reshape(
    session: AsyncSession,
) -> None:
    """The reshaped query returns what the old one did, against a real database.

    Moving ``similarity_threshold`` from a ``WHERE`` predicate to a filter on the
    already-limited rows is the change most able to alter results, and nothing
    in the suite went near it -- ``test_retrieval.py`` mocks the repository, so
    every existing test of this behaviour was asserting on a stub.

    Four rows with hand-picked cosine similarities to the query vector cover the
    three things the reshape could have broken: ordering (1.0 before 0.8),
    thresholding (0.0 dropped), and the document-status join (a perfect match
    under a non-``ready`` document stays out).
    """
    document_id, other_document_id = uuid4(), uuid4()
    await _insert_document(session, document_id, status="ready")
    await _insert_document(session, other_document_id, status="error")

    # e0 is the query. Similarity to it is just the first coordinate of a unit
    # vector, so each row's expected score is written directly into its vector.
    query = _unit_vector(1.0)
    await _insert_chunk(session, document_id, 0, "perfect", _unit_vector(1.0))
    await _insert_chunk(session, document_id, 1, "close", _unit_vector(0.8))
    await _insert_chunk(session, document_id, 2, "unrelated", _unit_vector(0.0))
    await _insert_chunk(session, other_document_id, 0, "hidden", _unit_vector(1.0))

    repository = KnowledgeBaseRepository(session)
    results = await repository.search_similar_chunks(
        query, kb_types=["hr"], top_k=3, similarity_threshold=0.7
    )

    assert [chunk.content for chunk, _, _ in results] == ["perfect", "close"], (
        "expected the two rows above the 0.7 threshold, nearest first"
    )
    assert [round(score, 3) for _, _, score in results] == [1.0, 0.8]

    limited = await repository.search_similar_chunks(
        query, kb_types=["hr"], top_k=1, similarity_threshold=0.7
    )
    assert [chunk.content for chunk, _, _ in limited] == ["perfect"]

    none_pass = await repository.search_similar_chunks(
        query, kb_types=["hr"], top_k=3, similarity_threshold=0.99999
    )
    assert [chunk.content for chunk, _, _ in none_pass] == ["perfect"], (
        "a threshold above every score but the exact match must leave only it"
    )


async def test_chunks_under_a_non_ready_document_cannot_hide_the_real_matches(
    session: AsyncSession,
) -> None:
    """Nearer chunks under a non-``ready`` document must not empty the result.

    An HNSW scan hands back a bounded candidate set, and ``status = 'ready'`` is
    applied to those candidates *after* the index has picked them. If they all
    belong to a document that is not ready, the approximate branch returns
    nothing while the right answer exists further out. Reproduced on pgvector
    0.8.6 with 1 000 chunks under an ``error`` document sitting nearer the query
    than any ready chunk: 0 rows approximate against 3 exact.
    ``hnsw.iterative_scan`` does not close it -- at ``strict_order`` and
    ``relaxed_order``, ``scan_mem_multiplier`` up to 64, the scan stopped at 532
    candidates and still returned 0 rows.

    That state is reachable: ``IngestionService.ingest`` inserts chunks and only
    then marks the document ``ready``, and its ``except`` handler sets ``error``
    and returns normally -- so the worker commits chunks alongside an ``error``
    status.

    **Two things make the seam real at test size.** The table holds enough
    non-ready chunks that the index scan's whole candidate set is filtered away;
    and the two planner settings make it pick the index, which it will not do
    for a few hundred rows on cost alone -- ``enable_seqscan = off`` is not
    sufficient by itself, the sequential path still wins. Forcing the plan is
    legitimate here in a way it would not be in a benchmark: it reproduces the
    plan production gets at scale rather than manufacturing a speed claim, and
    the numbers this module quotes were all measured without it. It also does
    not weaken the assertion, because the fallback's ordering is unmatchable by
    any pgvector index and so still runs as an exact scan.

    Verified to force the seam: with the fallback removed, this returns nothing.
    """
    ready_id, failed_id = uuid4(), uuid4()
    await _insert_document(session, ready_id, status="ready")
    await _insert_document(session, failed_id, status="error")

    query = _unit_vector(1.0)
    session.add_all(
        [
            KnowledgeBaseChunk(
                document_id=failed_id,
                chunk_index=index,
                content=f"failed {index}",
                embedding=_unit_vector(1.0),
            )
            for index in range(_POISON_CHUNKS)
        ]
    )
    await _insert_chunk(session, ready_id, 0, "the real answer", _unit_vector(0.9))
    await session.execute(text("ANALYZE hr_knowledge_base_chunks"))
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL seq_page_cost = 1e7"))

    repository = KnowledgeBaseRepository(session)
    results = await repository.search_similar_chunks(
        query, kb_types=["hr"], top_k=3, similarity_threshold=0.7
    )

    assert [chunk.content for chunk, _, _ in results] == ["the real answer"], (
        "the chunks under the non-ready document swallowed the result"
    )


def _unit_vector(first: float) -> list[float]:
    """A unit vector whose cosine similarity to ``_unit_vector(1.0)`` is ``first``."""
    rest = math.sqrt(max(0.0, 1.0 - first * first))
    return [first, rest] + [0.0] * 1022


async def _insert_document(session: AsyncSession, document_id: UUID, status: str) -> None:
    """Insert an HR knowledge-base document with the given ingestion status."""
    session.add(
        KnowledgeBaseDocument(
            id=document_id,
            display_name=f"doc-{status}",
            file_name="doc.pdf",
            storage_path=f"kb/{document_id}.pdf",
            file_size=1024,
            mime_type="application/pdf",
            status=status,
        )
    )
    await session.flush()


async def _insert_chunk(
    session: AsyncSession,
    document_id: UUID,
    chunk_index: int,
    content: str,
    embedding: list[float],
) -> None:
    """Insert one chunk with a known embedding."""
    session.add(
        KnowledgeBaseChunk(
            document_id=document_id,
            chunk_index=chunk_index,
            content=content,
            embedding=embedding,
        )
    )
    await session.flush()


async def _compiled_search_sql(kb_types: list[str]) -> list[str]:
    """Every statement ``search_similar_chunks`` sends, in order, each on one line.

    The stub returns no rows, which is a short branch, so the exact fallback
    always runs and the list holds the approximate statement then the exact one.
    """
    captured: list[object] = []

    class _CapturingSession:
        async def execute(self, statement: object) -> object:
            captured.append(statement)

            class _Result:
                @staticmethod
                def all() -> list[object]:
                    return []

            return _Result()

    repository = KnowledgeBaseRepository(_CapturingSession())  # type: ignore[arg-type]
    await repository.search_similar_chunks([0.1] * 1024, kb_types=kb_types, top_k=3)
    return [
        " ".join(str(statement.compile(dialect=postgresql.dialect())).split())  # type: ignore[attr-defined]
        for statement in captured
    ]


# `ORDER BY <table>.embedding <=> :param`, ascending, with nothing wrapped
# around it -- the one ordering pgvector can answer from an index. The negative
# lookahead is what makes a re-introduced `DESC` fail this rather than slip by:
# a descending sort on a distance is the same row order and an unusable index.
_INDEXABLE_ORDER_BY = re.compile(r"ORDER BY \w+\.embedding <=> %\(\w+\)s(?!\s*DESC)")

# A WHERE clause runs to the next top-level keyword or to the close of the
# UNION subquery. It cannot simply stop at the first `)`: bind parameters render
# as `%(status_1)s`, so that would cut the clause off before the part under
# test and pass vacuously.
_WHERE_CLAUSE = re.compile(r"WHERE (?P<clause>.+?)(?= ORDER BY | UNION ALL | LIMIT |\) AS |$)")


def _where_clauses(sql: str) -> list[str]:
    """Every ``WHERE ...`` fragment in the statement, one per UNION branch."""
    return [m.group("clause") for m in _WHERE_CLAUSE.finditer(sql)]
