"""add unique index on payments.provider_payment_id"""

from alembic import op

# Alembic identifiers
revision = "5426c75e96a0"
down_revision = "03425fb1a46e"  # твоя предыдущая ревизия из списка
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ux_payments_provider_payment_id",
        "payments",
        ["provider_payment_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_payments_provider_payment_id", table_name="payments")
