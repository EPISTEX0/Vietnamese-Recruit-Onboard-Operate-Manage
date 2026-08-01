"""split_system_admin_and_hr_roles

Revision ID: 082_split_system_admin_and_hr_roles
Revises: 081_add_guide_progress_to_organization_settings
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '082_split_system_admin_and_hr_roles'
down_revision: Union[str, None] = '081_add_guide_progress_to_organization_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Update length of role column if needed and migrate existing 'admin' roles to 'hr'
    op.execute("UPDATE users SET role = 'hr' WHERE role = 'admin'")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'hr'")
