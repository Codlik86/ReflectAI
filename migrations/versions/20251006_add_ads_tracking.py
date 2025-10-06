"""add ads / ad_links / ad_starts tables (idempotent)"""

from alembic import op
import sqlalchemy as sa

# Alembic identifiers
revision = "20251006_add_ads_tracking"
down_revision = "20251002_dialog_summaries"  # важно: совпадает с твоим head
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
    # ---------- ads ----------
    if not _has_table("ads"):
        op.create_table(
            "ads",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("creative_text", sa.Text, nullable=True),
            sa.Column("image_url", sa.Text, nullable=True),
            sa.Column("channel_handle", sa.Text, nullable=True),
            sa.Column("initial_budget_ton", sa.Numeric(12, 6), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    if not _has_index("ads", "ix_ads_code"):
        op.create_index("ix_ads_code", "ads", ["code"])

    # ---------- ad_links ----------
    if not _has_table("ad_links"):
        op.create_table(
            "ad_links",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("ad_id", sa.BigInteger, sa.ForeignKey("ads.id", ondelete="CASCADE"), nullable=False),
            sa.Column("channel_handle", sa.Text, nullable=True),
            sa.Column("deep_link", sa.Text, nullable=False),
            sa.Column("note", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    if not _has_index("ad_links", "ix_ad_links_ad_id"):
        op.create_index("ix_ad_links_ad_id", "ad_links", ["ad_id"])

    # ---------- ad_starts ----------
    if not _has_table("ad_starts"):
        op.create_table(
            "ad_starts",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("ad_id", sa.BigInteger, sa.ForeignKey("ads.id", ondelete="SET NULL"), nullable=True),
            sa.Column("start_code", sa.String(64), nullable=True),
            sa.Column("tg_user_id", sa.BigInteger, nullable=True),
            sa.Column("username", sa.Text, nullable=True),
            sa.Column("first_name", sa.Text, nullable=True),
            sa.Column("ref_channel", sa.Text, nullable=True),
            sa.Column("raw_payload", sa.JSON, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    if not _has_index("ad_starts", "ix_ad_starts_start_code"):
        op.create_index("ix_ad_starts_start_code", "ad_starts", ["start_code"])
    if not _has_index("ad_starts", "ix_ad_starts_tg_user_id"):
        op.create_index("ix_ad_starts_tg_user_id", "ad_starts", ["tg_user_id"])


def downgrade() -> None:
    # удаляем индексы, если есть
    if _has_index("ad_starts", "ix_ad_starts_tg_user_id"):
        op.drop_index("ix_ad_starts_tg_user_id", table_name="ad_starts")
    if _has_index("ad_starts", "ix_ad_starts_start_code"):
        op.drop_index("ix_ad_starts_start_code", table_name="ad_starts")
    if _has_table("ad_starts"):
        op.drop_table("ad_starts")

    if _has_index("ad_links", "ix_ad_links_ad_id"):
        op.drop_index("ix_ad_links_ad_id", table_name="ad_links")
    if _has_table("ad_links"):
        op.drop_table("ad_links")

    if _has_index("ads", "ix_ads_code"):
        op.drop_index("ix_ads_code", table_name="ads")
    if _has_table("ads"):
        op.drop_table("ads")
