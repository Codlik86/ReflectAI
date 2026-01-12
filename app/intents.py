from __future__ import annotations

def is_subscription_intent(text: str) -> bool:
    t = (text or "").lower().strip()
    if not t:
        return False

    keywords = [
        "подписк",
        "оплат",
        "купить",
        "оформ",
        "тариф",
        "премиум",
        "платн",
        "оплата",
        "цена",
        "стоим",
        "сколько стоит",
        "как оплат",
        "как купить",
        "где купить",
        "как оформить",
    ]
    return any(k in t for k in keywords)
