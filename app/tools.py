# app/tools.py
import asyncio
import time
import json
from typing import Dict, Optional, Any
from aiogram.types import Message

# ====== РЕФРЕЙМИНГ (тексты шагов) ======
REFRAMING_STEPS = [
    ("thought", "Шаг 1 из 4 — *Мысль*.\n\nЧто ты сейчас думаешь о ситуации?\nНапиши 1–2 предложения."),
    ("emotion", "Шаг 2 из 4 — *Эмоция*.\n\nЧто ты чувствуешь? (напр., тревога/злость/грусть)\nНасколько сильно по шкале 1–10?"),
    ("behavior", "Шаг 3 из 4 — *Действие*.\n\nЧто ты обычно делаешь (или хочется сделать), когда так думаешь/чувствуешь?"),
    ("alternative", "Шаг 4 из 4 — *Альтернатива*.\n\nКакая более сбалансированная мысль могла бы помочь посмотреть на ситуацию иначе?\nИдея: «я не обязан быть идеальным, могу сделать по шагу»."),
]

# ====== ДЫХАНИЕ ======
BREATHING_SCRIPT = [
    "Вдох через нос — 4…",
    "Задержка — 2…",
    "Выдох через рот — 6…",
]

BODY_SCAN_TEXT = (
    "🧘 Body Scan (3–5 мин)\n\n"
    "1) Сядь/ляг удобно, закрой глаза.\n"
    "2) Переводи внимание: ступни → голени → колени → бёдра → живот/грудь → плечи → шея → лицо.\n"
    "3) В каждой зоне отметь: тепло/холод, напряжение/расслабление, покалывание.\n"
    "4) Если ум уходит — мягко вернись к телу и дыханию.\n"
    "5) Заверши глубоким вдохом/выдохом и мягко открой глаза.\n\n"
    "Можешь сохранить инсайт кнопкой «💾 Сохранить инсайт»."
)

# ====== Менеджер задач: чтобы не плодить дубликаты и уметь останавливать ======
_running_tasks: Dict[str, asyncio.Task] = {}       # user_id -> Task
_stop_flags: Dict[str, asyncio.Event] = {}         # user_id -> stop-event
_last_click_ts: Dict[str, float] = {}              # user_id -> last cb ts (debounce)

DEBOUNCE_SEC = 1.2

def debounce_ok(user_id: str) -> bool:
    """Простая защита от дабл-кликов по кнопкам."""
    now = time.monotonic()
    last = _last_click_ts.get(user_id, 0.0)
    if now - last < DEBOUNCE_SEC:
        return False
    _last_click_ts[user_id] = now
    return True

def has_running_task(user_id: str) -> bool:
    t = _running_tasks.get(user_id)
    return t is not None and not t.done()

def stop_user_task(user_id: str):
    # Ставим флаг и ждём, пока корутина аккуратно выйдет
    ev = _stop_flags.get(user_id)
    if ev:
        ev.set()

async def wait_task_finish(user_id: str, timeout: float = 1.0):
    t = _running_tasks.get(user_id)
    if t and not t.done():
        try:
            await asyncio.wait_for(t, timeout=timeout)
        except asyncio.TimeoutError:
            t.cancel()

def _prepare_task(user_id: str):
    # Сбросим предыдущие следы
    old = _running_tasks.get(user_id)
    if old and not old.done():
        old.cancel()
    ev = asyncio.Event()
    _stop_flags[user_id] = ev
    return ev

# ====== Собственно дыхательная практика (60 сек), управляемая флагом ======
async def _breathing_loop(message: Message, stop_event: asyncio.Event):
    msg = await message.answer("🫁 Дыхательная пауза — запускаю таймер…")
    # 4 цикла ~12–13 сек = ~50 сек; добавим небольшой бридж для ~60 сек
    for i in range(1, 5):
        if stop_event.is_set():
            try:
                await msg.edit_text("⏹️ Остановлено.")
            except Exception:
                pass
            return

        try:
            await msg.edit_text(f"🫁 Цикл {i}/4\n\n{BREATHING_SCRIPT[0]}")
        except Exception:
            pass
        # Вдох (4с)
        for _ in range(4):
            if stop_event.is_set():
                try: await msg.edit_text("⏹️ Остановлено.")
                except Exception: pass
                return
            await asyncio.sleep(1)

        try:
            await msg.edit_text(f"🫁 Цикл {i}/4\n\n{BREATHING_SCRIPT[1]}")
        except Exception:
            pass
        for _ in range(2):
            if stop_event.is_set():
                try: await msg.edit_text("⏹️ Остановлено.")
                except Exception: pass
                return
            await asyncio.sleep(1)

        try:
            await msg.edit_text(f"🫁 Цикл {i}/4\n\n{BREATHING_SCRIPT[2]}")
        except Exception:
            pass
        for _ in range(6):
            if stop_event.is_set():
                try: await msg.edit_text("⏹️ Остановлено.")
                except Exception: pass
                return
            await asyncio.sleep(1)

    # Добивка до ~60 сек
    for _ in range(4):
        if stop_event.is_set():
            try: await msg.edit_text("⏹️ Остановлено.")
            except Exception: pass
            return
        await asyncio.sleep(1)

    try:
        await msg.edit_text("Готово. Как ощущения? 🙌\nМожешь сохранить мысль/ощущение как инсайт (кнопка ниже).")
    except Exception:
        await message.answer("Готово. Как ощущения? 🙌")

def start_breathing_task(message: Message, user_id: str) -> asyncio.Task:
    ev = _prepare_task(user_id)
    t = asyncio.create_task(_breathing_loop(message, ev))
    _running_tasks[user_id] = t
    return t

# ====== UX-утилиты для бота ======

# Безопасное редактирование сообщения с фолбэком на отправку нового
async def safe_edit(msg: Message, text: str, reply_markup: Optional[Any] = None):
    """
    Пытается отредактировать текст сообщения.
    Если редактирование невозможно (message is not modified / слишком старое / другое),
    отправляет новое сообщение тем же чатом.
    """
    try:
        await msg.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception:
        try:
            await msg.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)
        except Exception:
            # Последний шанс — молча проглатываем, чтобы не ронять вебхук
            pass

# Анти-дубль для callback_query: хранение последних id в памяти процесса (TTL)
_CALLBACK_TTL_SEC = 600  # 10 минут
_processed_cb: Dict[str, float] = {}  # cbq.id -> timestamp

def is_duplicate_callback(cbq_id: str) -> bool:
    """
    Возвращает True, если этот callback уже обрабатывался недавно.
    Хранение в памяти процесса; при множестве воркеров можно вынести в БД/кэш.
    """
    now = time.monotonic()
    # Очистка старых записей (ленивая)
    stale = [k for k, ts in _processed_cb.items() if (now - ts) > _CALLBACK_TTL_SEC]
    for k in stale:
        _processed_cb.pop(k, None)

    if cbq_id in _processed_cb:
        return True
    _processed_cb[cbq_id] = now
    return False

# Лёгкое логирование событий в БД (для метрик)
def log_event(user_id: Optional[int], event_type: str, payload: Optional[dict] = None):
    """
    Пишет событие в таблицу bot_events (если она есть по ORM).
    Безопасно: ошибки проглатываются.
    """
    try:
        from app.db import db_session, BotEvent
        with db_session() as s:
            be = BotEvent(
                user_id=int(user_id) if user_id is not None else None,
                event_type=str(event_type),
                payload=json.dumps(payload, ensure_ascii=False) if payload else None,
            )
            s.add(be)
            s.commit()
    except Exception:
        # Логирование не должно валить обработку апдейта
        pass

__all__ = [
    "REFRAMING_STEPS",
    "BREATHING_SCRIPT",
    "BODY_SCAN_TEXT",
    "debounce_ok",
    "has_running_task",
    "stop_user_task",
    "wait_task_finish",
    "start_breathing_task",
    "safe_edit",
    "is_duplicate_callback",
    "log_event",
]
