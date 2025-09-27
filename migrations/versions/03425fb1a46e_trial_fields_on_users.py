"""trial fields on users

Revision ID: 03425fb1a46e
Revises: 20250925_fix_tg_id_bigint
Create Date: 2025-09-27 21:20:27.736474

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '03425fb1a46e'
down_revision: Union[str, Sequence[str], None] = '20250925_fix_tg_id_bigint'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("users", sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("trial_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("subscription_status", sa.String(length=16), nullable=True))  # 'active'|'expired'|None


def downgrade():
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "trial_expires_at")
    op.drop_column("users", "trial_started_at")
