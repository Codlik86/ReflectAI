"""users.trial + payments + subscriptions (safe, idempotent)

Revision ID: 345e2cf026c1
Revises:
Create Date: 2025-09-25 16:54:27.165701
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "345e2cf026c1"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------- helpers ---------------- #

def _insp():
    bind = op.get_bind()
    return sa.inspect(bind)

def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()

def _has_column(table: str, column: str) -> bool:
    cols = [c["name"] for c in _insp().get_columns(table)]
    return column in cols

def _has_index(table: str, index_name: str) -> bool:
    idxs = [i["name"] for i in _insp().get_indexes(table)]
    return index_name in idxs


# --------------- upgrade ----------------- #

def upgrade() -> None:
    # USERS: безопасно добавляем поля
    if not _has_column("users", "style_profile"):
        op.add_column(
            "users",
            sa.Column("style_profile", sa.Text(), nullable=False, server_default="default"),
        )
        # убираем временный дефолт
        op.alter_column("users", "style_profile", server_default=None)

    if not _has_column("users", "trial_until"):
        op.add_column(
            "users",
            sa.Column("trial_until", sa.DateTime(timezone=True), nullable=True),
        )

    # PAYMENTS: создаём таблицу, если её ещё нет
    if not _has_table("payments"):
        op.create_table(
            "payments",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.Text(), nullable=False, server_default="yookassa"),
            sa.Column("provider_payment_id", sa.Text(), nullable=True),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("currency", sa.Text(), nullable=False, server_default="RUB"),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("raw", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        )

    # индекс на payments.user_id — создаём только если его нет
    if _has_table("payments") and not _has_index("payments", "ix_payments_user_id"):
        op.execute("CREATE INDEX IF NOT EXISTS ix_payments_user_id ON payments (user_id)")

    # SUBSCRIPTIONS: мягко добавляем недостающие поля, ничего не дропаем
    if _has_table("subscriptions"):
        if not _has_column("subscriptions", "plan"):
            op.add_column("subscriptions", sa.Column("plan", sa.Text(), nullable=True))
        if not _has_column("subscriptions", "status"):
            op.add_column("subscriptions", sa.Column("status", sa.Text(), nullable=True, server_default="active"))
            op.alter_column("subscriptions", "status", server_default=None)
        if not _has_column("subscriptions", "is_auto_renew"):
            op.add_column("subscriptions", sa.Column("is_auto_renew", sa.Boolean(), nullable=True, server_default=sa.text("true")))
            op.alter_column("subscriptions", "is_auto_renew", server_default=None)
        if not _has_column("subscriptions", "subscription_until"):
            op.add_column("subscriptions", sa.Column("subscription_until", sa.DateTime(timezone=True), nullable=True))
        if not _has_column("subscriptions", "yk_payment_method_id"):
            op.add_column("subscriptions", sa.Column("yk_payment_method_id", sa.Text(), nullable=True))
        if not _has_column("subscriptions", "yk_customer_id"):
            op.add_column("subscriptions", sa.Column("yk_customer_id", sa.Text(), nullable=True))
        if not _has_column("subscriptions", "created_at"):
            op.add_column("subscriptions", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")))
            op.alter_column("subscriptions", "created_at", server_default=None)
        if not _has_column("subscriptions", "updated_at"):
            op.add_column("subscriptions", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")))
            op.alter_column("subscriptions", "updated_at", server_default=None)

        # индекс на user_id
        if not _has_index("subscriptions", "ix_subscriptions_user_id"):
            op.execute("CREATE INDEX IF NOT EXISTS ix_subscriptions_user_id ON subscriptions (user_id)")


# --------------- downgrade ---------------- #

def downgrade() -> None:
    # payments
    if _has_table("payments"):
        if _has_index("payments", "ix_payments_user_id"):
            op.execute("DROP INDEX IF EXISTS ix_payments_user_id")
        op.drop_table("payments")

    # users
    if _has_column("users", "trial_until"):
        op.drop_column("users", "trial_until")
    if _has_column("users", "style_profile"):
        op.drop_column("users", "style_profile")

    # subscriptions: удаляем только добавленные нами поля
    if _has_table("subscriptions"):
        for col in [
            "updated_at",
            "created_at",
            "yk_customer_id",
            "yk_payment_method_id",
            "subscription_until",
            "is_auto_renew",
            "status",
            "plan",
        ]:
            if _has_column("subscriptions", col):
                op.drop_column("subscriptions", col)
