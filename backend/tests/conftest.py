"""Shared pytest configuration, test-tier classification, and the PostgreSQL fixture.

Test tiers are derived from file names so individual test modules do not need
repetitive decorators.  A test can still add a more specific marker locally.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# backend/ -- the directory holding alembic.ini and the alembic/ package.
BACKEND_DIR = Path(__file__).resolve().parents[1]

# The migration chain runs ``CREATE EXTENSION vector`` for the knowledge base,
# so a stock postgres image cannot reach head.
PGVECTOR_IMAGE = "pgvector/pgvector:pg15"

_SLOW_FILES = {
    "test_classify_concurrency.py",
    "test_classify_timeout.py",
    "test_cv_processor.py",
    "test_gmail_adapter.py",
    "test_review_service.py",
}


def pytest_collection_modifyitems(items: list[object]) -> None:
    """Classify collected tests for fast PR and full-suite CI runs."""
    tests_root = Path(__file__).parent

    for item in items:
        path = Path(str(item.fspath))
        relative_path = path.relative_to(tests_root).as_posix().lower()
        filename = path.name.lower()

        if "property" in filename:
            item.add_marker("property")
        if filename in _SLOW_FILES:
            item.add_marker("slow")

        if any(token in relative_path for token in ("integration", "e2e", "migration")):
            item.add_marker("integration")
        if "migration" in relative_path:
            item.add_marker("migration")
        if "e2e" in relative_path:
            item.add_marker("e2e")


def _docker_available(docker_module: object) -> bool:
    """Return True if a Docker daemon is reachable, else False."""
    try:
        client = docker_module.from_env()  # type: ignore[attr-defined]
        client.ping()
    except Exception:  # noqa: BLE001 - any docker error means "not available"
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against ``async_url`` using the real env."""
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


@pytest.fixture(scope="session")
def postgres_async_url() -> Iterator[str]:
    """Start PostgreSQL 15 once per session, migrate it, yield the asyncpg URL.

    Session-scoped on purpose: reaching head replays the whole migration chain,
    so a per-module container would pay that cost once per test file.

    The schema comes from ``alembic upgrade head`` rather than
    ``SQLModel.metadata.create_all`` so tests read through the columns
    production actually has, not the ones the models would create.
    """
    docker = pytest.importorskip("docker")
    postgres_container = pytest.importorskip("testcontainers.postgres")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for database round-trip tests")

    # Credentials are pinned rather than left to testcontainers' defaults,
    # which fall back to POSTGRES_USER/PASSWORD/DB from the ambient
    # environment. Earlier tests in a full-suite run leave the deployment
    # values there, and that password contains a '!' -- which arrives
    # percent-encoded in the URL and makes Alembic's configparser choke on
    # "invalid interpolation syntax". Pinning them keeps this fixture
    # independent of test ordering.
    with postgres_container.PostgresContainer(
        PGVECTOR_IMAGE, username="test", password="test", dbname="test"
    ) as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url
