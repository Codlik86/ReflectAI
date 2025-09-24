# app/safety.py
# -*- coding: utf-8 -*-
import re
from typing import Iterable

# Базовые ключевые фразы (оставляю твои формулировки + доп.варианты)
CRISIS_KEYWORDS: Iterable[str] = [
    # твои
    "покончу с собой", "самоубиться", "не хочу жить", "суицид", "убью себя",
    "нанести себе вред", "самоповреждение",
    # частые варианты/синонимы
    "хочу умереть", "устал жить", "жить не хочу", "умереть хочу",
    "порезать вены", "порезаться", "навредить себе", "сделать себе больно",
    # англ. варианты (на случай смешанных сообщений)
    "suicide", "kill myself", "end my life", "i want to die", "self-harm", "hurt myself",
]

CRISIS_REPLY = (
    "Мне очень жаль, что тебе сейчас так тяжело. Я не заменяю терапевта, "
    "но хочу помочь тебе оставаться в безопасности. Если есть риск немедленной опасности, "
    "пожалуйста, обратись за срочной помощью: 📞 службы экстренной помощи/горячие линии в твоём регионе.\n\n"
    "Можем сделать короткое упражнение стабилизации (дыхание 60 сек). "
    "Если скажешь, подскажу контакты профессиональной помощи."
)

# Скомпилируем мягкие регулярки для поиска (word-boundary где уместно)
_patterns = [
    re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    if re.match(r"^[\w\s-]+$", kw, flags=re.IGNORECASE) else
    re.compile(re.escape(kw), re.IGNORECASE)
    for kw in CRISIS_KEYWORDS
]

def is_crisis(text: str) -> bool:
    """
    Простой детектор кризисных формулировок.
    Возвращает True, если текст содержит одну из ключевых фраз.
    """
    if not text:
        return False
    t = text.strip()
    for p in _patterns:
        if p.search(t):
            return True
    return False

__all__ = ["CRISIS_KEYWORDS", "CRISIS_REPLY", "is_crisis"]
