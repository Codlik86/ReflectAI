from alembic import op
import sqlalchemy as sa

revision = "20251002_dialog_summaries"
down_revision = "add_pk_id_to_subscriptions"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "dialog_summaries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),  # daily | weekly | topic
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("tokens", sa.Integer, nullable=True),
        sa.Column("source_count", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id","kind","period_start","period_end", name="uq_dialog_summaries_span"),
    )
    op.create_index("ix_dialog_summaries_user_kind", "dialog_summaries", ["user_id","kind"])

def downgrade():
    op.drop_index("ix_dialog_summaries_user_kind", table_name="dialog_summaries")
    op.drop_table("dialog_summaries")
