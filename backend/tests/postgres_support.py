"""One place to construct the PostgreSQL container the test suite needs.

Two things went wrong often enough to be worth centralizing:

*Image.* The migration chain runs ``CREATE EXTENSION vector`` for the
knowledge base, so any fixture that reaches ``alembic upgrade head`` needs
pgvector. A stock ``postgres:15-alpine`` fails with "extension \"vector\" is
not available" -- and because that happens in fixture setup it surfaces as a
collection *error*, not a readable test failure.

*Credentials.* ``PostgresContainer`` falls back to ``POSTGRES_USER`` /
``POSTGRES_PASSWORD`` / ``POSTGRES_DB`` from the ambient environment. The
backend calls ``load_dotenv()`` at import time, so importing ``src.main`` or
any worker pulls the deployment values from the repo-root ``.env`` into
``os.environ``. That password contains a ``!``, which arrives percent-encoded
as ``%21`` in the connection URL, and Alembic's configparser then rejects it
with "invalid interpolation syntax". Pinning the credentials keeps every
fixture independent of import order and of whatever ``.env`` happens to hold.
"""

from __future__ import annotations

from typing import Any

PGVECTOR_IMAGE = "pgvector/pgvector:pg15"

# Deliberately boring and free of characters that need percent-encoding.
POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"


def make_postgres_container(image: str = PGVECTOR_IMAGE) -> Any:
    """Build a ``PostgresContainer`` with a pgvector image and pinned credentials.

    Args:
        image: Container image to run. Defaults to the pgvector build, which
            is what any fixture running the full migration chain needs.

    Returns:
        An unstarted ``PostgresContainer`` ready for use as a context manager.
    """
    import pytest

    postgres_container = pytest.importorskip("testcontainers.postgres")

    return postgres_container.PostgresContainer(
        image,
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )
