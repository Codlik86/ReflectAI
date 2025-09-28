# app/db/models.py
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    ...


class User(Base):
    __tablename__ = "users"

    # первичные поля
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    # настройки
    privacy_level: Mapped[str] = mapped_column(Text, default="ask", nullable=False)
    style_profile: Mapped[str] = mapped_column(Text, default="default", nullable=False)

    # --- ТРИАЛ (старое и новое поле; новое — совместимо с нашими хелперами) ---
    # твое прежнее поле — не трогаем, вдруг где-то используется
    trial_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # новые поля, которые использует биллинг/админка
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- ПОДПИСКА (упрощённый флаг в users; основная инфа живёт в Subscription) ---
    # 'active' | 'none' | NULL (для старых записей)
    subscription_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # сервисные поля
    # ВАЖНО: DateTime(timezone=True) ожидает aware-datetime — задаём через функцию
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # связи
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    provider: Mapped[str] = mapped_column(Text, default="yookassa", nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)  # аналог yk_payment_id
    amount: Mapped[int] = mapped_column(Integer, nullable=False)   # в копейках
    currency: Mapped[str] = mapped_column(Text, default="RUB", nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)      # pending/succeeded/canceled
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    raw: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped["User"] = relationship(back_populates="payments")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # основные поля плана/статуса
    plan: Mapped[str] = mapped_column(Text, nullable=False)                 # week|month|quarter|year
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active|canceled|expired
    is_auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ВАЖНО: эти поля должны быть в модели, т.к. они есть в БД и NOT NULL
    is_premium: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False, default="basic")

    # опционально (если есть такие столбцы в БД — оставляем, если нет, тоже ок, просто не будут использоваться)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # реквизиты ЮК (опциональны)
    yk_payment_method_id: Mapped[str | None] = mapped_column(Text)
    yk_customer_id: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="subscriptions")