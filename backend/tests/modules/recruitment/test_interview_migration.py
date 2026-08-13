"""Migration smoke test for the interview calendar fields (revision 046).

This is an integration test: it spins up a real PostgreSQL 15 instance with
``testcontainers``, runs ``alembic upgrade head`` against it, and verifies that
migration 046 realizes the expanded Interview entity model:

1. The tables ``interviews`` and ``interview_participants`` exist.
2. The candidates backfill was executed.
3. Downgrade/Rollback successfully drops the interview tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from tests.postgres_support import make_postgres_container

# testcontainers / docker are integration-only dependencies. Skip the whole
# module cleanly if either the library or a running Docker daemon is absent.
docker = pytest.importorskip("docker")

# backend/
BACKEND_DIR = Path(__file__).resolve().parents[3]


def _docker_available() -> bool:
    """Return True if a Docker daemon is reachable, else False."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
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
def migrated_engine() -> Iterator[Engine]:
    if not _docker_available():
        pytest.skip("Docker is not available for the migration smoke test")

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
def test_migration_drops_legacy_candidate_columns(
    migrated_engine: Engine,
) -> None:
    """Verify legacy calendar columns are dropped after migration 074."""
    inspector = inspect(migrated_engine)
    columns = {col["name"] for col in inspector.get_columns("candidates")}
    assert "calendar_event_id" not in columns
    assert "interview_start_at" not in columns
    assert "interview_timezone" not in columns


@pytest.mark.integration
def test_migration_creates_interview_tables(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    tables = set(inspector.get_table_names())

    assert "interviews" in tables, "interviews table is missing"
    assert "interview_participants" in tables, "interview_participants table is missing"

    columns = {col["name"] for col in inspector.get_columns("interviews")}
    expected = {
        "id",
        "candidate_id",
        "status",
        "round_name",
        "start_at",
        "end_at",
        "timezone",
        "calendar_event_id",
        "needs_relink",
    }
    assert expected.issubset(columns)

    part_columns = {col["name"] for col in inspector.get_columns("interview_participants")}
    part_expected = {"id", "interview_id", "type", "email", "name", "employee_id"}
    assert part_expected.issubset(part_columns)


@pytest.mark.integration
def test_migration_backfill_and_rollback(migrated_engine: Engine) -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    url = migrated_engine.url.render_as_string(hide_password=False)
    async_url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        # 1. Downgrade to 045
        command.downgrade(config, "045")

        # 2. Insert candidate with calendar data
        cand_id = uuid4()
        with migrated_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO candidates (id, name, email, phone, status, "
                    "confidence_score, calendar_event_id, interview_start_at, "
                    "interview_timezone, created_at, updated_at, skills, "
                    "experience, education) "
                    "VALUES (:id, :name, :email, :phone, :status, "
                    ":confidence_score, :calendar_event_id, :interview_start_at, "
                    ":interview_timezone, now(), now(), '[]', '[]', '[]')"
                ),
                {
                    "id": cand_id,
                    "name": "Test Candidate",
                    "email": "test@example.com",
                    "phone": "123456",
                    "status": "interview_scheduled",
                    "confidence_score": 1.0,
                    "calendar_event_id": "evt_legacy_123",
                    "interview_start_at": datetime.now(UTC),
                    "interview_timezone": "Asia/Ho_Chi_Minh",
                },
            )

        # 3. Upgrade to head
        command.upgrade(config, "head")

        # 4. Verify backfilled interview and participant exist
        with migrated_engine.begin() as conn:
            interviews = conn.execute(
                sa.text("SELECT * FROM interviews WHERE candidate_id = :candidate_id"),
                {"candidate_id": cand_id},
            ).fetchall()
            assert len(interviews) == 1
            iv = interviews[0]
            assert iv.calendar_event_id == "evt_legacy_123"
            assert iv.status == "scheduled"

            participants = conn.execute(
                sa.text("SELECT * FROM interview_participants WHERE interview_id = :interview_id"),
                {"interview_id": iv.id},
            ).fetchall()
            assert len(participants) == 1
            part = participants[0]
            assert part.type == "candidate"
            assert part.email == "test@example.com"
            assert part.name == "Test Candidate"

        # 5. Rollback again to verify clean downgrade
        command.downgrade(config, "045")
        inspector = inspect(migrated_engine)
        tables = set(inspector.get_table_names())
        assert "interviews" not in tables
        assert "interview_participants" not in tables

        # Re-upgrade to head so database is left in a clean upgraded state
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
