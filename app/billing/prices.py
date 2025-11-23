# Общие цены подписок (RUB), берём из ENV чтобы не дублировать в коде.
from __future__ import annotations

import os
from decimal import Decimal


PRICE_WEEK_RUB = Decimal(os.getenv("PRICE_WEEK_RUB", "199"))
PRICE_MONTH_RUB = Decimal(os.getenv("PRICE_MONTH_RUB", "599"))
PRICE_QUARTER_RUB = Decimal(os.getenv("PRICE_QUARTER_RUB", "999"))
PRICE_YEAR_RUB = Decimal(os.getenv("PRICE_YEAR_RUB", "2999"))

PLAN_PRICES_RUB: dict[str, Decimal] = {
    "week": PRICE_WEEK_RUB,
    "month": PRICE_MONTH_RUB,
    "quarter": PRICE_QUARTER_RUB,
    "year": PRICE_YEAR_RUB,
}


def plan_price_decimal(plan: str | None) -> Decimal:
    p = (plan or "month").lower()
    if p in PLAN_PRICES_RUB:
        return PLAN_PRICES_RUB[p]
    return PLAN_PRICES_RUB["month"]


def plan_price_int(plan: str | None) -> int:
    return int(plan_price_decimal(plan))


def plan_price_str(plan: str | None) -> str:
    return f"{plan_price_decimal(plan):.2f}"


PLAN_PRICES_STR: dict[str, str] = {k: plan_price_str(k) for k in PLAN_PRICES_RUB}
PLAN_PRICES_INT: dict[str, int] = {k: plan_price_int(k) for k in PLAN_PRICES_RUB}
