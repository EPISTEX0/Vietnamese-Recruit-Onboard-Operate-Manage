"""Source-level invariants for the role-split migrations (082 and 084).

These do not need a database. They guard the two failure modes that shipped
silently in 082 and stayed invisible until someone tried to boot the app:

1. ``users.role`` was left at ``VARCHAR(10)`` while ``'system_admin'`` is 12
   characters, so the role could not be written at all -- first-run setup, the
   super-admin bootstrap and every promotion failed with "value too long".
2. ``082.downgrade()`` mapped only ``hr`` back to ``admin``, leaving any
   ``system_admin`` row holding a value the pre-split model cannot interpret.

Both are checks against the migration source, so a future role added to
``UserRole`` without widening the column (or without extending the downgrade)
fails here rather than in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.modules.identity.domain.entities import User, UserRole

VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"
MIGRATION_082 = VERSIONS_DIR / "082_split_system_admin_and_hr_roles.py"
MIGRATION_084 = VERSIONS_DIR / "084_recover_system_admin_role.py"

# Roles that exist only after the split; 082's downgrade must map them all
# back to the single pre-split administrative role.
POST_SPLIT_ADMIN_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.HR)


@pytest.fixture(scope="module")
def source_084() -> str:
    """Return the text of migration 084."""
    return MIGRATION_084.read_text()


@pytest.fixture(scope="module")
def source_082() -> str:
    """Return the text of migration 082."""
    return MIGRATION_082.read_text()


def test_migrations_exist() -> None:
    """Guard against the invariants silently passing on a renamed file."""
    assert MIGRATION_082.is_file(), f"missing {MIGRATION_082}"
    assert MIGRATION_084.is_file(), f"missing {MIGRATION_084}"


def test_role_column_is_wide_enough_for_every_role(source_084: str) -> None:
    """The widened column must hold the longest UserRole value."""
    match = re.search(r"ROLE_COLUMN_LENGTH\s*=\s*(\d+)", source_084)
    assert match, "084 must declare ROLE_COLUMN_LENGTH"
    column_length = int(match.group(1))

    longest = max(UserRole, key=lambda role: len(role.value))
    assert len(longest.value) <= column_length, (
        f"UserRole.{longest.name} = '{longest.value}' is {len(longest.value)} chars "
        f"but users.role holds only {column_length}. Widen it in a new migration."
    )


def test_model_column_width_matches_the_migration(source_084: str) -> None:
    """The model must declare the same width 084 gave the real column.

    The model sat at ``VARCHAR(10)`` long after 084 widened the column to 20.
    Nothing failed, because the test schema is built by ``alembic upgrade head``
    rather than ``create_all`` -- but the next ``alembic revision
    --autogenerate`` would read the model, see a narrower column and emit a
    migration shrinking it back to 10, reinstating exactly the bug 084 fixed.

    The assertion reads the width off the mapped column rather than the
    ``ROLE_COLUMN_LENGTH`` constant: autogenerate reads the column, so a column
    built from some other literal would drift with the constant still correct.
    """
    match = re.search(r"ROLE_COLUMN_LENGTH\s*=\s*(\d+)", source_084)
    assert match, "084 must declare ROLE_COLUMN_LENGTH"
    migration_length = int(match.group(1))

    model_length = User.__table__.c.role.type.length  # type: ignore[union-attr]

    assert model_length == migration_length, (
        f"the User model maps users.role as VARCHAR({model_length}) but "
        f"migration 084 made it VARCHAR({migration_length}); autogenerate will "
        "propose resizing the live column to match the model."
    )


def test_082_downgrade_maps_every_post_split_role(source_082: str) -> None:
    """Downgrading must not strand a role the old model cannot interpret."""
    downgrade = source_082.split("def downgrade()")[-1]

    for role in POST_SPLIT_ADMIN_ROLES:
        assert f"'{role.value}'" in downgrade, (
            f"082.downgrade() does not map '{role.value}' back to 'admin'; rows "
            "holding it would survive the downgrade with an invalid value."
        )


def test_082_upgrade_is_untouched(source_082: str) -> None:
    """082 is already applied in the field -- editing upgrade() fixes nothing.

    Repairs belong in a new migration (084). This pins the applied statement so
    a well-meaning edit to already-shipped history is caught in review.
    """
    upgrade = source_082.split("def upgrade()")[1].split("def downgrade()")[0]
    statements = [line.strip() for line in upgrade.splitlines() if "op.execute" in line]

    assert statements == [
        "op.execute(\"UPDATE users SET role = 'hr' WHERE role = 'admin'\")"
    ], f"082.upgrade() has been modified: {statements}"


def test_084_is_idempotent_on_existing_system_admin(source_084: str) -> None:
    """084 must short-circuit when a system admin already exists."""
    assert "WHERE role = 'system_admin'" in source_084
    assert "if existing > 0:" in source_084


def test_084_prefers_configured_super_admin_over_fallback(source_084: str) -> None:
    """The operator's explicit choice must outrank the oldest-HR heuristic."""
    env_lookup = source_084.index("AUTH_SUPER_ADMIN_EMAIL")
    fallback = source_084.index("ORDER BY created_at")
    assert env_lookup < fallback, (
        "084 must consult AUTH_SUPER_ADMIN_EMAIL before falling back to the "
        "oldest HR account."
    )


def test_084_fails_loudly_when_no_admin_can_be_identified(source_084: str) -> None:
    """Silently picking an arbitrary account would hand out infra credentials."""
    assert "raise RuntimeError(" in source_084
    assert "AUTH_SUPER_ADMIN_EMAIL" in source_084.split("raise RuntimeError(")[1]
