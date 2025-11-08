# app/db/models.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    ...


# =========================
# USERS
# =========================
class User(Base):
    __tablename__ = "users"

    # BIGSERIAL в БД — оставим BigInteger для совместимости
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)

    privacy_level: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ask'"))
    style_profile: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'default'"))

    # триал
    trial_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # принято ли соглашение (есть в БД)
    policy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # быстрый флаг подписки
    subscription_status: Mapped[str | None] = mapped_column(String(16))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # relations: одна подписка на пользователя
    subscription: Mapped["Subscription"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="joined",
        cascade="save-update, merge",
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user",
        lazy="selectin",
        cascade="save-update, merge",
    )

    __table_args__ = (
        UniqueConstraint("tg_id", name="ix_users_tg_id_unique"),
    )


# =========================
# SUBSCRIPTIONS
# =========================
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # поля по скрину Neon
    is_premium: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    tier: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'basic'"))
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped[Optional[str]] = mapped_column(Text)      # в БД без NOT NULL
    status: Mapped[Optional[str]] = mapped_column(Text)    # в БД без NOT NULL
    is_auto_renew: Mapped[Optional[bool]] = mapped_column(Boolean)
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    yk_payment_method_id: Mapped[str | None] = mapped_column(Text)
    yk_customer_id: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    user: Mapped["User"] = relationship(back_populates="subscription", lazy="joined")

    __table_args__ = (
        # важный инвариант: одна подписка на пользователя
        UniqueConstraint("user_id", name="ix_subscriptions_user_id_unique"),
    )


# =========================
# PAYMENTS
# =========================
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'yookassa'"))
    provider_payment_id: Mapped[str | None] = mapped_column(Text)  # уникальность ниже в __table_args__
    amount: Mapped[int] = mapped_column(Integer, nullable=False)   # копейками
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'RUB'"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    raw: Mapped[dict] = mapped_column(JSON, nullable=False, server_default=text("'{}'"))

    user: Mapped["User"] = relationship(back_populates="payments", lazy="joined")

    __table_args__ = (
        UniqueConstraint("provider_payment_id", name="ux_payments_provider_payment_id"),
    )


# индексы (совпадают с тем, что уже в БД)
Index("ix_payments_user_id", Payment.user_id)
Index("ix_subscriptions_user_id", Subscription.user_id, unique=True)
