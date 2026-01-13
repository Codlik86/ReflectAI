from __future__ import annotations

def is_subscription_intent(text: str) -> bool:
    if not text:
        return False

    t = text.lower().strip()
    if not t:
        return False
    if t.startswith("/"):
        return False

    t_norm = " ".join(t.split())
    if t_norm in {"подписка", "💳 подписка"}:
        return False

    intent_keywords = [
        "оплат",
        "купить",
        "оформ",
        "подключ",
        "продл",
        "отмен",
        "тариф",
        "премиум",
        "цена",
        "стоим",
        "платеж",
        "платн",
        "не могу",
        "не работает",
        "не получается",
        "ошибк",
        "проблем",
        "сколько стоит",
        "как оплат",
        "как купить",
        "где купить",
        "как оформить",
    ]
    if any(k in t_norm for k in intent_keywords):
        return True

    question_words = ["как", "где", "почему", "сколько"]
    base_terms = ["подписк", "оплат", "платеж"]
    return any(q in t_norm for q in question_words) and any(b in t_norm for b in base_terms)
