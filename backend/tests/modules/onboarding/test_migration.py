"""Migration smoke test for the onboarding tables (revision 028).

These are integration tests: they spin up a real PostgreSQL 15 instance with
``testcontainers``, run ``alembic upgrade head`` against it, and verify two
things:

1. The migration applies cleanly and the three onboarding tables exist.
2. The ``UNIQUE`` index on ``onboarding_processes.candidate_id`` rejects a
   second process for the same candidate (the database invariant that backs
   idempotent event consumption — R1.3, R2.7, R3.4).

The test reuses the project's real Alembic environment (``backend/alembic`` +
``alembic.ini``), driving the async (asyncpg) engine that ``env.py`` builds via
the ``DATABASE_URL`` override, exactly as production migrations run.

Requirements: 1.3, 2.7, 3.4
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.postgres_support import make_postgres_container

# testcontainers / docker are integration-only dependencies. Skip the whole
# module cleanly if either the library or a running Docker daemon is absent.
docker = pytest.importorskip("docker")

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/onboarding/test_migration.py
BACKEND_DIR = Path(__file__).resolve().parents[3]

ONBOARDING_TABLES = {
    "onboarding_processes",
    "onboarding_tasks",
    "onboarding_audit_logs",
}


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
    (asyncpg) engine, so we point that variable at the container and invoke the
    Alembic command API the same way the CLI would.
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
    """Start PostgreSQL 15, apply all migrations to head, yield a sync engine.

    Module-scoped so the (slow) container start + full migration chain runs
    once for every test in this module.
    """
    if not _docker_available():
        pytest.skip("Docker is not available for the migration smoke test")

    with make_postgres_container() as postgres:
        # testcontainers returns a psycopg2 URL; derive the asyncpg variant that
        # the Alembic env.py expects for its async engine.
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")

        _run_alembic_upgrade_head(async_url)

        engine = create_engine(sync_url)
        try:
            yield engine
        finally:
            engine.dispose()


def _insert_employee(conn: object) -> str:
    """Insert a minimal valid employee row and return its id.

    ``onboarding_processes.employee_id`` is a NOT NULL FK to ``employees.id``,
    so a parent employee must exist before a process can be inserted. Only the
    NOT NULL columns without a server default need explicit values:
    ``id``, ``employee_code``, ``full_name``, ``email``.
    """
    employee_id = str(uuid4())
    unique = employee_id[:8]
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO employees (id, employee_code, full_name, email) "
            "VALUES (:id, :code, :name, :email)"
        ),
        {
            "id": employee_id,
            "code": f"NV-{unique[:3]}",
            "name": "Onboarding Test Employee",
            "email": f"emp-{unique}@example.com",
        },
    )
    return employee_id


@pytest.mark.integration
def test_migration_applies_cleanly_and_creates_onboarding_tables(
    migrated_engine: object,
) -> None:
    """`alembic upgrade head` creates the three onboarding tables (and employees).

    Requirements: 1.3, 2.7, 3.4
    """
    inspector = inspect(migrated_engine)  # type: ignore[arg-type]
    tables = set(inspector.get_table_names())

    missing = ONBOARDING_TABLES - tables
    assert not missing, f"migration did not create onboarding tables: {missing}"
    # The FK parent table must also be present for the process FK to resolve.
    assert "employees" in tables

    # The candidate_id index must exist and be unique — the backbone invariant.
    indexes = inspector.get_indexes("onboarding_processes")  # type: ignore[attr-defined]
    candidate_index = next(
        (ix for ix in indexes if ix["column_names"] == ["candidate_id"]),
        None,
    )
    assert candidate_index is not None, "candidate_id index is missing"
    assert candidate_index["unique"] is True, "candidate_id index is not unique"


@pytest.mark.integration
def test_migration_candidate_id_unique_constraint_rejects_duplicate(
    migrated_engine: object,
) -> None:
    """A second process with the same candidate_id violates the unique index.

    Inserting one onboarding_processes row succeeds; a second insert reusing the
    same candidate_id is rejected with an IntegrityError (unique violation),
    enforcing "exactly one process per candidate" at the database level.

    Requirements: 1.3, 2.7, 3.4
    """
    candidate_id = str(uuid4())

    # First process for the candidate — must succeed.
    with migrated_engine.begin() as conn:  # type: ignore[attr-defined]
        employee_id = _insert_employee(conn)
        conn.execute(
            text(
                "INSERT INTO onboarding_processes (id, candidate_id, employee_id) "
                "VALUES (:id, :candidate_id, :employee_id)"
            ),
            {
                "id": str(uuid4()),
                "candidate_id": candidate_id,
                "employee_id": employee_id,
            },
        )

    # Second process reusing the same candidate_id — must be rejected.
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as conn:  # type: ignore[attr-defined]
            second_employee_id = _insert_employee(conn)
            conn.execute(
                text(
                    "INSERT INTO onboarding_processes (id, candidate_id, employee_id) "
                    "VALUES (:id, :candidate_id, :employee_id)"
                ),
                {
                    "id": str(uuid4()),
                    "candidate_id": candidate_id,
                    "employee_id": second_employee_id,
                },
            )
