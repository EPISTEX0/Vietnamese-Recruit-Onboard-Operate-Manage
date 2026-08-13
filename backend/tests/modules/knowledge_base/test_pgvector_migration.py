"""Migration smoke test for the pgvector extension (revision 077).

Verifies that the ``vector`` extension is enabled in PostgreSQL after
``alembic upgrade head``, so that tables can define ``vector(768)`` columns.

This is the infrastructure foundation for the Knowledge Base RAG feature
(Issue #256, #257).

Acceptance criteria:
- PostgreSQL có extension ``vector`` enabled
- Có thể tạo bảng với cột kiểu ``vector(768)``
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.postgres_support import make_postgres_container

# testcontainers / docker are integration-only dependencies. Skip the whole
# module cleanly if either the library or a running Docker daemon is absent.
docker = pytest.importorskip("docker")

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/knowledge_base/test_pgvector_migration.py
BACKEND_DIR = Path(__file__).resolve().parents[3]

# pgvector/pgvector:pg15 includes the vector extension pre-installed.
# We use that image so the migration can just run CREATE EXTENSION.


def _docker_available() -> bool:
    """Return True if a Docker daemon is reachable, else False."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:  # noqa: BLE001 - any docker error means "not available"
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against ``async_url`` using the real env.

    ``env.py`` reads ``DATABASE_URL`` from the environment and builds an async
    (asyncpg) engine.
    """
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[object]:
    """Start pgvector PostgreSQL, apply all migrations, yield a sync engine."""
    if not _docker_available():
        pytest.skip("Docker is not available for the pgvector migration test")

    # Use pgvector/pgvector:pg15 image which has the extension pre-installed
    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")

        _run_alembic_upgrade_head(async_url)

        engine = create_engine(sync_url)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.mark.integration
def test_vector_extension_is_enabled(migrated_engine: object) -> None:
    """AC: PostgreSQL có extension ``vector`` enabled.

    Verifies the ``vector`` extension appears in ``pg_extension``.
    """
    with migrated_engine.connect() as conn:  # type: ignore[attr-defined]
        result = conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
        row = result.fetchone()
        assert row is not None, (
            "pgvector extension 'vector' is not enabled in PostgreSQL. "
            "Did migration 077 (CREATE EXTENSION IF NOT EXISTS vector) run?"
        )


@pytest.mark.integration
def test_can_create_table_with_vector_768_column(migrated_engine: object) -> None:
    """AC: Có thể tạo bảng với cột kiểu ``vector(768)``.

    Creates a temporary table with a vector(768) column, inserts a row
    with a zero vector, queries it back, and drops the table.
    """
    with migrated_engine.begin() as conn:  # type: ignore[attr-defined]
        # Create a test table with a vector(768) column
        conn.execute(
            text("CREATE TABLE _test_vector_table (id SERIAL PRIMARY KEY, embedding vector(768))")
        )
        # Insert a row with a zero vector
        zeros = "[" + ",".join(["0"] * 768) + "]"
        conn.execute(
            text("INSERT INTO _test_vector_table (embedding) VALUES (:vec)"),
            {"vec": zeros},
        )
        # Query it back
        result = conn.execute(text("SELECT embedding FROM _test_vector_table WHERE id = 1"))
        row = result.fetchone()
        assert row is not None
        # Drop the test table
        conn.execute(text("DROP TABLE _test_vector_table"))

    assert row is not None, "Could not create and query a table with vector(768) column"
