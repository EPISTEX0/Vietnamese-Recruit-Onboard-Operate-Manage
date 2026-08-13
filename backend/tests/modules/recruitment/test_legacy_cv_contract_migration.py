"""Integration coverage for the legacy ``cv`` routing-contract migration (065)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from tests.postgres_support import make_postgres_container

docker = pytest.importorskip("docker")

BACKEND_DIR = Path(__file__).resolve().parents[3]


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return False
    return True


def _run_alembic(async_url: str, operation: str, revision: str) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        getattr(command, operation)(config, revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="module")
def migration_database() -> Iterator[tuple[Engine, str]]:
    if not _docker_available():
        pytest.skip("Docker is not available for the migration integration test")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic(async_url, "upgrade", "064")
        engine = create_engine(sync_url)
        try:
            yield engine, async_url
        finally:
            engine.dispose()


@pytest.mark.integration
def test_migrates_only_unresolved_legacy_cv_email_once_with_audit_history(
    migration_database: tuple[Engine, str],
) -> None:
    engine, async_url = migration_database
    user_id = uuid4()
    unresolved_email_id = uuid4()
    candidate_email_id = uuid4()
    candidate_id = uuid4()
    existing_application_email_id = uuid4()
    existing_application_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, name, google_sub)
                VALUES (:id, 'hr@example.com', 'HR', 'legacy-cv-migration')
                """
            ),
            {"id": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO email_messages (
                    id, user_id, gmail_message_id, gmail_thread_id, subject,
                    sender_email, sender_name, recipient_emails, received_at,
                    processing_status, category
                ) VALUES
                    (:unresolved_id, :user_id, 'legacy-unresolved', 'thread-1',
                     'Ứng tuyển Backend', 'applicant@example.com', 'Applicant', '[]', now(),
                     'classified', 'cv'),
                    (:candidate_email_id, :user_id, 'legacy-candidate', 'thread-2',
                     'Ứng tuyển Frontend', 'candidate@example.com', 'Candidate', '[]', now(),
                     'classified', 'cv'),
                    (:existing_app_email_id, :user_id, 'legacy-existing-app', 'thread-3',
                     'Ứng tuyển QA', 'qa@example.com', 'QA Applicant', '[]', now(),
                     'classified', 'cv')
                """
            ),
            {
                "unresolved_id": unresolved_email_id,
                "candidate_email_id": candidate_email_id,
                "existing_app_email_id": existing_application_email_id,
                "user_id": user_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO candidates (id, name, email, source_email_message_id)
                VALUES (:id, 'Existing Candidate', 'candidate@example.com', :email_id)
                """
            ),
            {"id": candidate_id, "email_id": candidate_email_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO job_applications (
                    id, source_email_message_id, gmail_message_id, gmail_thread_id,
                    source, sender_name, sender_email, evidence, source_hints,
                    message_references, audit_history, status
                ) VALUES (
                    :id, :email_id, 'legacy-existing-app', 'thread-3', 'direct',
                    'QA Applicant', 'qa@example.com', '[]', '[]', '[]', '[]', 'new'
                )
                """
            ),
            {"id": existing_application_id, "email_id": existing_application_email_id},
        )

    _run_alembic(async_url, "upgrade", "065")

    with engine.connect() as connection:
        applications = (
            connection.execute(
                text(
                    """
                SELECT source_email_message_id, gmail_message_id, audit_history
                FROM job_applications
                ORDER BY gmail_message_id
                """
                )
            )
            .mappings()
            .all()
        )
        candidates = (
            connection.execute(text("SELECT id, source_email_message_id FROM candidates"))
            .mappings()
            .all()
        )
        categories = dict(
            connection.execute(text("SELECT gmail_message_id, category FROM email_messages")).all()
        )

    assert len(applications) == 2
    migrated = next(row for row in applications if row["gmail_message_id"] == "legacy-unresolved")
    assert migrated["source_email_message_id"] == unresolved_email_id
    assert migrated["audit_history"][0]["action"] == "legacy_classification_migrated"
    assert migrated["audit_history"][0]["legacy_intent"] == "cv"
    assert categories["legacy-unresolved"] == "job_application"

    assert candidates == [{"id": candidate_id, "source_email_message_id": candidate_email_id}]
    assert categories["legacy-candidate"] == "cv"
    assert categories["legacy-existing-app"] == "cv"
    assert all(
        UUID(str(row["source_email_message_id"])) != candidate_email_id for row in applications
    )

    # Re-entering the data revision executes the backfill again. It must remain a no-op.
    _run_alembic(async_url, "downgrade", "064")
    _run_alembic(async_url, "upgrade", "065")
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM job_applications")).scalar_one()
    assert count == 2
