from __future__ import annotations

from typing import Optional, List, Dict
from datetime import datetime
import json
import re

try:
    from .db import db_session, User, Insight
except Exception as e:
    raise RuntimeError("Не найден app/db.py с db_session/User/Insight") from e

# Опциональные таблицы — могут отсутствовать в твоей схеме
try:
    from .db import DiaryEntry  # type: ignore
except Exception:
    DiaryEntry = None  # type: ignore

try:
    from .db import BotEvent  # type: ignore
except Exception:
    BotEvent = None  # type: ignore


class MemoryManager:
    """Память Помни: пользователь, приватность, записи (дневник/инсайты), события."""

    # ---------- Пользователь и приватность ----------
    def ensure_user(self, tg_id: str) -> User:
        with db_session() as s:
            u = s.query(User).filter_by(tg_id=str(tg_id)).first()
            if not u:
                kwargs = {"tg_id": str(tg_id)}
                # поддержим разные схемы БД
                if hasattr(User, "privacy_mode"):
                    kwargs["privacy_mode"] = "ask"
                elif hasattr(User, "privacy_level"):
                    kwargs["privacy_level"] = "insights"
                u = User(**kwargs)  # type: ignore
                s.add(u)
                s.commit()
                s.refresh(u)
            return u

    def get_privacy(self, tg_id: str) -> str:
        """Возвращает режим приватности: ask|none|all (insights трактуем как ask)."""
        with db_session() as s:
            u = s.query(User).filter_by(tg_id=str(tg_id)).first()
            if not u:
                return "ask"
            if hasattr(u, "privacy_mode"):
                return getattr(u, "privacy_mode") or "ask"
            if hasattr(u, "privacy_level"):
                v = (getattr(u, "privacy_level") or "insights").lower()
                return "ask" if v == "insights" else v
            return "ask"

    def set_privacy(self, tg_id: str, value: str) -> None:
        """Устанавливает режим приватности: ask|none|all (insights = ask)."""
        mode = (value or "").lower().strip()
        if mode == "insights":
            mode = "ask"
        self.ensure_user(tg_id)
        with db_session() as s:
            u = s.query(User).filter_by(tg_id=str(tg_id)).first()
            if hasattr(u, "privacy_mode"):
                setattr(u, "privacy_mode", mode)
            elif hasattr(u, "privacy_level"):
                setattr(u, "privacy_level", "insights" if mode == "ask" else mode)
            s.commit()

    # ---------- Дневник / Инсайты ----------
    def save_diary_entry(self, tg_id: str, text: str) -> int:
        """Сохраняет запись; возвращает ID."""
        self.ensure_user(tg_id)
        with db_session() as s:
            if DiaryEntry is not None:
                rec = DiaryEntry(tg_id=str(tg_id), text=text, created_at=datetime.utcnow())  # type: ignore
            else:
                rec = Insight(tg_id=str(tg_id), text=text, created_at=datetime.utcnow())
            s.add(rec)
            s.commit()
            s.refresh(rec)
            return int(rec.id)

    def list_diary(self, tg_id: str, limit: int = 20) -> List[Dict]:
        with db_session() as s:
            if DiaryEntry is not None:
                rows = (
                    s.query(DiaryEntry)
                    .filter_by(tg_id=str(tg_id))
                    .order_by(DiaryEntry.created_at.desc())  # type: ignore
                    .limit(limit)
                    .all()
                )
            else:
                rows = (
                    s.query(Insight)
                    .filter_by(tg_id=str(tg_id))
                    .order_by(Insight.created_at.desc())
                    .limit(limit)
                    .all()
                )
            out: List[Dict] = []
            for r in rows:
                created = getattr(r, "created_at", None)
                out.append(
                    {
                        "id": int(r.id),
                        "text": r.text,
                        "created_at": created.isoformat() if created else None,
                    }
                )
            return out

    # ---------- События/метрики ----------
    def log_event(self, tg_id: str, event_type: str, payload: Optional[str] = None) -> None:
        """Пишем событие, если есть таблица; иначе игнорируем молча."""
        if BotEvent is None:
            return
        with db_session() as s:
            ev = BotEvent(  # type: ignore
                user_id=None,
                event_type=event_type,
                payload=payload,
                created_at=datetime.utcnow(),
            )
            s.add(ev)
            s.commit()


# ---------- Backward-compatible wrappers (для старых импортов из bot.py) ----------
def add_journal_entry(tg_id, text):
    return MemoryManager().save_diary_entry(str(tg_id), text)


def list_journal_entries(tg_id, limit: int = 20):
    return MemoryManager().list_diary(str(tg_id), limit=limit)


def save_insight(tg_id, text):
    return add_journal_entry(tg_id, text)


def get_privacy(tg_id):
    return MemoryManager().get_privacy(str(tg_id))


def set_privacy(tg_id, value):
    return MemoryManager().set_privacy(str(tg_id), value)


def get_privacy_mode(tg_id):
    return get_privacy(tg_id)


def set_privacy_mode(tg_id, value):
    return set_privacy(tg_id, value)



def log_event(tg_id, event_type, payload=None):
    """Надёжная запись события: гарантируем user.id, пишем BotEvent, не падаем."""
    try:
        from app.db import db_session, User, BotEvent
        tid = str(tg_id)
        with db_session() as s:
            user = s.query(User).filter(User.tg_id == tid).first()
            if user is None:
                user = User(tg_id=tid)
                s.add(user)
                s.flush()     # присваивает user.id
            ev = BotEvent(
                user_id=user.id,
                event_type=str(event_type or ""),
                payload="" if payload is None else str(payload),
            )
            s.add(ev)
            s.commit()
        return True
    except Exception as e:
        print(f"[memory.log_event wrapper] error: {e}")
        return False

def update_user_memory(tg_id, key=None, value=None, **data):
    """
    Универсальная запись «настроек/памяти».
    Пишем как событие mem_update (payload=JSON). Если таблицы событий нет — создаём текстовую заметку.
    """
    mm = MemoryManager()
    if key is not None:
        payload = {"key": str(key), "value": value}
    else:
        payload = dict(data) if data else {}
    try:
        mm.log_event(str(tg_id), "mem_update", json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        try:
            text = "[mem] " + json.dumps(payload, ensure_ascii=False)
            mm.save_diary_entry(str(tg_id), text)
        except Exception:
            pass
        return True


def get_user_memory(tg_id):
    """
    Возврат «словаря настроек». Сейчас — заглушка (пустой словарь), чтобы код не падал.
    Можно расширить под реальное хранилище позже.
    """
    return {}


def get_user_settings(tg_id):
    """Совместимый алиас поверх get_user_memory."""
    return get_user_memory(tg_id)


def set_user_settings(tg_id, **data):
    """Совместимый алиас поверх update_user_memory."""
    return update_user_memory(tg_id, **data)


def set_user_tone(tg_id, tone: str):
    """Сохранить предпочитаемый тон (friend|coach|plain...)."""
    return update_user_memory(tg_id, tone=tone)


def set_user_method(tg_id, method: str):
    """Сохранить предпочитаемую методологию (cbt|gestalt|mix...)."""
    return update_user_memory(tg_id, method=method)


# Простейшая эвристика для детекции «запроса на помощь»
_HELP_PATTERNS = [
    r"\bчто\s+делать\b",
    r"\bкак\s+справ(ить|ля)сь?\b",
    r"\bпомог(и|ите)\b",
    r"\bсовет(ы)?\b",
    r"\bкак\s+быть\b",
    r"\bплан\b",
    r"\bпрактик\w*\b",
    r"\bупражнен\w*\b",
    r"\bразобра(ть|ться)\b",
    r"\bразложи(ть)?\b",
    r"\bтревог\w*\b",
    r"\bпаник\w*\b",
    r"\bвыгор\w*\b",
    r"\bне\s+сплю\b",
    r"\bплохо\s+сплю\b",
    r"\bкпт\b|\bcbt\b",
]


def is_help_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    for pat in _HELP_PATTERNS:
        if re.search(pat, t):
            return True
    return False

def purge_user_data(tg_id: str) -> int:
    """
    Полная очистка истории для пользователя:
    - DiaryEntry (если есть),
    - Insight,
    - BotEvent, привязанные к user.id.
    Возвращает суммарное кол-во удалённых записей (best effort).
    """
    total = 0
    with db_session() as s:
        # найдём пользователя (для BotEvent.user_id)
        user = s.query(User).filter_by(tg_id=str(tg_id)).first()

        if DiaryEntry is not None:
            total += s.query(DiaryEntry).filter_by(tg_id=str(tg_id)).delete()  # type: ignore

        total += s.query(Insight).filter_by(tg_id=str(tg_id)).delete()

        if user is not None and BotEvent is not None:
            total += s.query(BotEvent).filter(BotEvent.user_id == user.id).delete()  # type: ignore

        s.commit()
    return total

# === ReflectAI: безопасная очистка истории ===
def purge_user_history(tg_id: int) -> int:
    """
    Удаляет записи пользователя из локального хранилища:
    Diary (или DiaryEntry), Insight, BotEvent. Возвращает кол-во удалённых строк.
    Безопасно пропускает отсутствующие таблицы/поля.
    """
    removed = 0
    try:
        from app.db import db_session, User
    except Exception:
        return 0

    user = db_session.query(User).filter_by(tg_id=tg_id).one_or_none()
    if not user:
        return 0
    uid = getattr(user, "id", None)

    def _safe_delete(model, by_user_id=True):
        nonlocal removed
        if model is None:
            return
        try:
            q = db_session.query(model)
            if by_user_id and hasattr(model, "user_id") and uid is not None:
                n = q.filter(model.user_id == uid).delete(synchronize_session=False)
            elif hasattr(model, "tg_id"):
                n = q.filter(model.tg_id == tg_id).delete(synchronize_session=False)
            else:
                return
            removed += int(n or 0)
        except Exception:
            pass

    Insight = Diary = BotEvent = None
    try:
        from app.db import Insight as _Insight
        Insight = _Insight
    except Exception:
        pass
    try:
        from app.db import Diary as _Diary
        Diary = _Diary
    except Exception:
        try:
            from app.db import DiaryEntry as _DiaryEntry
            Diary = _DiaryEntry
        except Exception:
            pass
    try:
        from app.db import BotEvent as _BotEvent
        BotEvent = _BotEvent
    except Exception:
        pass

    _safe_delete(Insight)
    _safe_delete(Diary)
    _safe_delete(BotEvent)
    try:
        db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    return removed
