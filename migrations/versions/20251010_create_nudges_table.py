"""create nudges table (idempotent)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Alembic identifiers
revision = "20251010_create_nudges_table"
down_revision = "20251006_add_ads_tracking"
branch_labels = None
depends_on = None


def _insp():
    bind = op.get_bind()
    return sa.inspect(bind)


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    idxs = [i["name"] for i in _insp().get_indexes(table)]
    return index_name in idxs


def upgrade() -> None:
    if not _has_table("nudges"):
        op.create_table(
            "nudges",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.BigInteger,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tg_id", sa.BigInteger, nullable=False),
            sa.Column("kind", sa.String(64), nullable=False),
            sa.Column(
                "payload",
                postgresql.JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    if not _has_index("nudges", "ix_nudges_kind_created_at"):
        op.create_index("ix_nudges_kind_created_at", "nudges", ["kind", "created_at"])
    if not _has_index("nudges", "ix_nudges_user_kind_created_at"):
        op.create_index("ix_nudges_user_kind_created_at", "nudges", ["user_id", "kind", "created_at"])


def downgrade() -> None:
    if _has_table("nudges"):
        if _has_index("nudges", "ix_nudges_user_kind_created_at"):
            op.drop_index("ix_nudges_user_kind_created_at", table_name="nudges")
        if _has_index("nudges", "ix_nudges_kind_created_at"):
            op.drop_index("ix_nudges_kind_created_at", table_name="nudges")
        op.drop_table("nudges")
