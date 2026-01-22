"""add tg blocked fields to users (idempotent)"""

from alembic import op
import sqlalchemy as sa

# Alembic identifiers
revision = "20260122_add_tg_blocked_fields"
down_revision = "20251010_create_nudges_table"
branch_labels = None
depends_on = None


def _insp():
    bind = op.get_bind()
    return sa.inspect(bind)


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _has_column(table: str, column_name: str) -> bool:
    if not _has_table(table):
        return False
    cols = [c["name"] for c in _insp().get_columns(table)]
    return column_name in cols


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    idxs = [i["name"] for i in _insp().get_indexes(table)]
    return index_name in idxs


def upgrade() -> None:
    if _has_table("users"):
        if not _has_column("users", "tg_is_blocked"):
            op.add_column(
                "users",
                sa.Column(
                    "tg_is_blocked",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )
        if not _has_column("users", "tg_blocked_at"):
            op.add_column("users", sa.Column("tg_blocked_at", sa.DateTime(timezone=True)))

        if not _has_index("users", "ix_users_tg_is_blocked"):
            op.create_index("ix_users_tg_is_blocked", "users", ["tg_is_blocked"])


def downgrade() -> None:
    if _has_table("users"):
        if _has_index("users", "ix_users_tg_is_blocked"):
            op.drop_index("ix_users_tg_is_blocked", table_name="users")
        if _has_column("users", "tg_blocked_at"):
            op.drop_column("users", "tg_blocked_at")
        if _has_column("users", "tg_is_blocked"):
            op.drop_column("users", "tg_is_blocked")
