"""Index both knowledge-base ``embedding`` columns with HNSW / vector_cosine_ops.

Revision ID: 088
Revises: 087
Create Date: 2026-08-14 00:00:00.000000+00:00

``hr_knowledge_base_chunks.embedding`` and
``employee_knowledge_base_chunks.embedding`` are ``vector(1024)`` and, since
078/079 created them, have carried no index at all -- a schema-wide sweep of
``pg_am`` found zero non-btree indexes. Every similarity search reads the whole
table and detoasts a 4 KB vector per row.

**Why hnsw and not ivfflat.** Both tables are empty or nearly so on every
environment today, and this migration runs in the startup pipeline -- so the
index is built *before* the data arrives. That is the case the two methods
disagree on. Measured on 50 000 synthetic 1024-dim rows (pgvector 0.8.6,
``pgvector/pgvector:pg15``), recall@3 against an exact scan:

    index built on the empty table, then 50 000 rows ingested
      ivfflat lists=100 ....... recall 0.433   <- and pgvector says so at build
      hnsw defaults ........... recall 1.000      time: "ivfflat index created
                                                   with little data / This will
                                                   cause low recall / Drop the
                                                   index until the table has
                                                   more data."
    index built after the same 50 000 rows were ingested
      ivfflat lists=224 ....... recall 0.947
      hnsw defaults ........... recall 1.000

ivfflat assigns rows to centroids computed at build time. Built empty it gets
one centroid, and it never recomputes -- so it silently returns wrong answers
for as long as it exists, and the planner does use it. hnsw builds its graph
incrementally, so a build on an empty table is both instant and correct.

**Why the default m=16 / ef_construction=64.** m=24 / ef_construction=100 was
also measured: identical recall (1.000 at both settings on 50 000 rows) for
2.3x the build time (43.5 s vs 18.8 s). There is nothing to buy.

**Cost of this migration.** On an empty table the build is ~10 ms, which is what
every environment will actually pay. On a table that already holds rows it is
roughly 0.6 ms/row (5 000 rows 1.9 s; 20 000 rows 10.0 s; 50 000 rows 27.4 s) at
``maintenance_work_mem = 1GB``, and ``CREATE INDEX`` takes a SHARE lock -- reads
continue, ingest waits.

The ongoing cost is write-side: inserting 50 000 chunks took 11.1 s with no
index and 162.0 s with the hnsw index present, i.e. ~3 ms per chunk. Chunk
writes happen in the kb-worker behind the embedding service, whose per-chunk
latency is far larger, so this is absorbed there rather than on a request path.

``vector_cosine_ops`` and not ``vector_l2_ops``/``vector_ip_ops`` because
``KnowledgeBaseRepository.search_similar_chunks`` orders by
``Column.cosine_distance()``, which renders ``<=>``. pgvector only considers an
index whose opclass matches the operator, so the other two would never be used.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "088"
down_revision: str | None = "087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table). The names follow the ``ix_kb_chunks_*`` / ``ix_emp_kb_chunks_*``
# convention 078/079 established for these two tables, which does not repeat the
# table name -- see the comment at the top of the knowledge_base entities module.
INDEXES = [
    ("ix_kb_chunks_embedding_hnsw", "hr_knowledge_base_chunks"),
    ("ix_emp_kb_chunks_embedding_hnsw", "employee_knowledge_base_chunks"),
]


def upgrade() -> None:
    for index_name, table in INDEXES:
        op.create_index(
            index_name,
            table,
            ["embedding"],
            if_not_exists=True,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    for index_name, table in INDEXES:
        op.drop_index(index_name, table_name=table, if_exists=True)
