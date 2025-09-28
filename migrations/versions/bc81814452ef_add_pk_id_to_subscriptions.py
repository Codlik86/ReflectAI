from alembic import op

# revision identifiers, used by Alembic.
revision = "add_pk_id_to_subscriptions"
down_revision = "5426c75e96a0"  # <-- оставь последнюю существующую у тебя ревизию-хед
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='subscriptions' AND column_name='id'
      ) THEN
        ALTER TABLE public.subscriptions ADD COLUMN id BIGSERIAL;
        -- если первичного ключа ещё нет — добавим
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint
          WHERE conrelid = 'public.subscriptions'::regclass AND contype = 'p'
        ) THEN
          ALTER TABLE public.subscriptions ADD PRIMARY KEY (id);
        END IF;
      END IF;
    END$$;
    """)

def downgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='subscriptions' AND column_name='id'
      ) THEN
        -- снимем PK если он на id
        IF EXISTS (
          SELECT 1
          FROM pg_constraint
          WHERE conrelid = 'public.subscriptions'::regclass AND contype = 'p'
          AND conkey = ARRAY[
            (SELECT attnum FROM pg_attribute
             WHERE attrelid='public.subscriptions'::regclass AND attname='id')
          ]
        ) THEN
          ALTER TABLE public.subscriptions DROP CONSTRAINT IF EXISTS subscriptions_pkey;
        END IF;
        ALTER TABLE public.subscriptions DROP COLUMN IF EXISTS id;
      END IF;
    END$$;
    """)