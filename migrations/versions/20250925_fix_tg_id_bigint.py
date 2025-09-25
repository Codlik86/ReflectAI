"""Fix users.tg_id -> BIGINT (idempotent)"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa  # noqa

# подставляем ваш предыдущий revision как down_revision:
revision: str = "20250925_fix_tg_id_bigint"
down_revision: Union[str, Sequence[str], None] = "345e2cf026c1"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Меняем тип только если он НЕ bigint
    op.execute("""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name='users'
      AND column_name='tg_id'
      AND data_type NOT IN ('bigint','integer')
  ) THEN
    ALTER TABLE users
      ALTER COLUMN tg_id TYPE BIGINT USING tg_id::bigint;
  END IF;
END $$;
""")
    # Опционально индекс по tg_id (безопасно, если уже есть)
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_tg_id ON users (tg_id);")

def downgrade() -> None:
    # Возврат в TEXT — только если сейчас bigint
    op.execute("""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name='users'
      AND column_name='tg_id'
      AND data_type IN ('bigint','integer')
  ) THEN
    ALTER TABLE users
      ALTER COLUMN tg_id TYPE TEXT USING tg_id::text;
  END IF;
END $$;
""")
    op.execute("DROP INDEX IF EXISTS ix_users_tg_id;")
