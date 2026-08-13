"""Append-only audit integration test for the onboarding module.

This module verifies Requirement 8.4 — *if a request attempts to modify or
delete an existing Audit_Log entry, the Onboarding_Service rejects the request
and preserves the existing Audit_Log entry unchanged* — from two complementary
angles:

1. **Application-layer surface (no database needed).**
   :class:`~src.modules.onboarding.infrastructure.audit_repository.OnboardingAuditRepository`
   exposes **only** ``append`` as a public method and provides no
   ``update``/``delete``/``remove`` (or any other mutation) entry point. Because
   the repository is the single application path to the audit table, "no
   mutation method" means application code has no way to alter or remove a
   committed audit entry. (This overlaps the reflection checks in
   ``test_repositories.py`` by design — here it anchors the append-only
   guarantee end-to-end.)

2. **Persistence-unchanged guarantee against a REAL database.**
   Using a real PostgreSQL 15 (via ``testcontainers``) with the project schema
   built by ``alembic upgrade head``, an :class:`OnboardingAuditLog` is appended
   and committed, then re-read in a *fresh* session by id, asserting every field
   persisted unchanged (operation_type, entity_type, entity_id, candidate_id,
   event_id, ``previous_value``/``new_value`` JSONB, change_summary, success,
   created_at). Appending a second entry is then shown not to alter the first —
   the append-only log only ever accretes; existing rows stay byte-for-byte the
   same, and the repository offers no path to change them.

The container/migration/session scaffolding mirrors ``test_migration.py`` and
``test_repositories.py``: a module-scoped container + ``alembic upgrade head``,
and per-operation async sessions. These tests are marked ``integration`` and
skip cleanly when ``testcontainers``/``docker`` or a running Docker daemon is
unavailable.

Requirements: 8.4
"""

from __future__ import annotations

import inspect as inspect_module
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.domain.entities import OnboardingAuditLog
from src.modules.onboarding.infrastructure.audit_repository import OnboardingAuditRepository
from tests.postgres_support import make_postgres_container

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/onboarding/test_append_only_audit_integration.py
BACKEND_DIR = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Migration / container helpers (mirrors test_migration.py / test_repositories.py)
# ---------------------------------------------------------------------------
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


@pytest.fixture(scope="module")
def postgres_async_url() -> Iterator[str]:
    """Start PostgreSQL 15, apply all migrations, yield the asyncpg URL.

    Module-scoped so the (slow) container start + migration chain runs once for
    every test in this module. Skips cleanly if ``testcontainers``/``docker`` or
    a running Docker daemon is unavailable.
    """
    docker = pytest.importorskip("docker")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for the append-only audit integration test")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url


def _make_user() -> User:
    """Build a minimal valid HR (admin) user (not yet persisted).

    ``OnboardingAuditLog.user_id`` is a nullable FK to ``users.id``. To exercise
    the non-null (HR-driven) audit case faithfully against the real schema, a
    parent user row must exist before the audit entry is appended.
    """
    suffix = uuid4().hex[:12]
    return User(
        email=f"hr.admin-{suffix}@example.com",
        name="HR Admin",
        google_sub=f"google-sub-{suffix}",
        role=UserRole.HR,
    )


def _make_audit_entry(user_id: UUID) -> OnboardingAuditLog:
    """Build a fully populated (not yet persisted) audit entry.

    Every nullable/JSONB field is given a non-default value so the
    persistence-unchanged assertions exercise a real round-trip rather than
    columns that happen to be NULL. ``user_id`` must reference an existing user
    row (FK ``onboarding_audit_logs_user_id_fkey``).
    """
    return OnboardingAuditLog(
        user_id=user_id,
        actor_email="hr.admin@example.com",
        operation_type="task_completed",
        entity_type="task",
        entity_id=uuid4(),
        candidate_id=uuid4(),
        event_id=f"evt-{uuid4().hex}",
        previous_value={"status": "pending"},
        new_value={"status": "done", "nested": {"by": "hr", "count": 2}},
        change_summary="Marked onboarding task done",
        success=True,
    )


# ---------------------------------------------------------------------------
# 1. Application-layer surface — append-only, no mutation path (no DB needed)
# ---------------------------------------------------------------------------
class TestAuditRepositoryHasNoMutationPath:
    """The repository exposes only ``append``; there is no update/delete path (R8.4)."""

    def test_append_is_the_only_public_method(self) -> None:
        """``append`` is the single public method on the audit repository."""
        public_methods = {
            name
            for name, _member in inspect_module.getmembers(
                OnboardingAuditRepository, predicate=inspect_module.isfunction
            )
            if not name.startswith("_")
        }

        assert public_methods == {"append"}

    @pytest.mark.parametrize(
        "forbidden",
        ["update", "delete", "remove", "edit", "modify", "set_status", "save", "purge"],
    )
    def test_no_mutation_method_is_exposed(self, forbidden: str) -> None:
        """No update/delete-style mutation method exists on the repository class."""
        assert not hasattr(OnboardingAuditRepository, forbidden)


# ---------------------------------------------------------------------------
# 2. Persistence-unchanged guarantee against a REAL database (R8.4)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestAuditEntriesPersistUnchanged:
    """A committed audit entry persists unchanged and is never mutated (R8.4)."""

    async def test_appended_entry_persists_every_field_unchanged(
        self, postgres_async_url: str
    ) -> None:
        """Append+commit an entry, then re-read it in a fresh session unchanged.

        Confirms the append-only repository writes a complete, faithful row to a
        real PostgreSQL database (including the ``JSONB`` ``previous_value`` /
        ``new_value`` columns) and that re-reading it yields exactly what was
        written — the repository provides only ``append`` and no way to mutate it.
        """
        engine = create_async_engine(postgres_async_url, poolclass=NullPool)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        user = _make_user()
        try:
            # Persist the parent user row the audit entry's user_id references.
            async with maker() as user_session:
                user_session.add(user)
                await user_session.commit()

            entry = _make_audit_entry(user.id)
            # Append within one transaction and commit so a fresh session sees it.
            async with maker() as write_session:
                repo = OnboardingAuditRepository(write_session)
                await repo.append(entry)
                await write_session.commit()

            entry_id = entry.id
            expected = {
                "user_id": entry.user_id,
                "actor_email": entry.actor_email,
                "operation_type": entry.operation_type,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "candidate_id": entry.candidate_id,
                "event_id": entry.event_id,
                "previous_value": entry.previous_value,
                "new_value": entry.new_value,
                "change_summary": entry.change_summary,
                "success": entry.success,
                "created_at": entry.created_at,
            }

            # Re-read in a brand-new session (no identity-map caching).
            async with maker() as read_session:
                reread = await read_session.get(OnboardingAuditLog, entry_id)

            assert reread is not None, "appended audit entry was not persisted"
            assert reread.user_id == expected["user_id"]
            assert reread.actor_email == expected["actor_email"]
            assert reread.operation_type == expected["operation_type"]
            assert reread.entity_type == expected["entity_type"]
            assert reread.entity_id == expected["entity_id"]
            assert reread.candidate_id == expected["candidate_id"]
            assert reread.event_id == expected["event_id"]
            assert reread.previous_value == expected["previous_value"]
            assert reread.new_value == expected["new_value"]
            assert reread.change_summary == expected["change_summary"]
            assert reread.success == expected["success"]
            # Timezone-aware comparison: equal instants regardless of tz encoding.
            assert reread.created_at == expected["created_at"]
        finally:
            await engine.dispose()

    async def test_appending_another_entry_does_not_alter_the_first(
        self, postgres_async_url: str
    ) -> None:
        """Appending a second entry leaves the first entry byte-for-byte unchanged.

        The audit log only ever accretes (append-only): writing new history must
        never touch existing rows. After a second commit, the first entry re-read
        from a fresh session is identical to what was originally written.
        """
        engine = create_async_engine(postgres_async_url, poolclass=NullPool)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        user = _make_user()
        try:
            # Persist the parent user row both audit entries reference.
            async with maker() as user_session:
                user_session.add(user)
                await user_session.commit()

            first = _make_audit_entry(user.id)
            second = _make_audit_entry(user.id)
            async with maker() as session_one:
                await OnboardingAuditRepository(session_one).append(first)
                await session_one.commit()

            first_id = first.id
            first_snapshot = {
                "operation_type": first.operation_type,
                "entity_type": first.entity_type,
                "entity_id": first.entity_id,
                "candidate_id": first.candidate_id,
                "event_id": first.event_id,
                "previous_value": first.previous_value,
                "new_value": first.new_value,
                "change_summary": first.change_summary,
                "success": first.success,
                "created_at": first.created_at,
            }

            # Append a distinct second entry in its own transaction.
            async with maker() as session_two:
                await OnboardingAuditRepository(session_two).append(second)
                await session_two.commit()

            # The first entry must be untouched by the second append.
            async with maker() as read_session:
                reread_first = await read_session.get(OnboardingAuditLog, first_id)

            assert reread_first is not None
            assert reread_first.operation_type == first_snapshot["operation_type"]
            assert reread_first.entity_type == first_snapshot["entity_type"]
            assert reread_first.entity_id == first_snapshot["entity_id"]
            assert reread_first.candidate_id == first_snapshot["candidate_id"]
            assert reread_first.event_id == first_snapshot["event_id"]
            assert reread_first.previous_value == first_snapshot["previous_value"]
            assert reread_first.new_value == first_snapshot["new_value"]
            assert reread_first.change_summary == first_snapshot["change_summary"]
            assert reread_first.success == first_snapshot["success"]
            assert reread_first.created_at == first_snapshot["created_at"]
        finally:
            await engine.dispose()
