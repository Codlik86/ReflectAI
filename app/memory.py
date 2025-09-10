
from __future__ import annotations
from typing import Optional, List, Dict
from datetime import datetime

# Берём существующие модели/сессию
from .db import db_session, User, Insight
try:
    from .db import DiaryEntry  # может не быть — тогда пишем в Insight
except Exception:
    DiaryEntry = None  # type: ignore
try:
    from .db import BotEvent
except Exception:
    BotEvent = None  # type: ignore

class MemoryManager:
    """Память Помни: приватность, записи дневника/инсайтов и событийные логи."""
    # ---------- Пользователь и приватность ----------
    def ensure_user(self, tg_id: str) -> User:
        with db_session() as s:
            u = s.query(User).filter_by(tg_id=str(tg_id)).first()
            if not u:
                # поддержим оба поля: privacy_mode | privacy_level
                kwargs = {"tg_id": str(tg_id)}
                if hasattr(User, "privacy_mode"):
                    kwargs["privacy_mode"] = "ask"
                elif hasattr(User, "privacy_level"):
                    # старое значение — совместим с нашей логикой
                    kwargs["privacy_level"] = "insights"
                u = User(**kwargs)  # type: ignore
                s.add(u); s.commit(); s.refresh(u)
            return u

    def get_privacy(self, tg_id: str) -> str:
        with db_session() as s:
            u = s.query(User).filter_by(tg_id=str(tg_id)).first()
            if not u:
                return "ask"
            if hasattr(u, "privacy_mode"):
                return getattr(u, "privacy_mode") or "ask"
            if hasattr(u, "privacy_level"):
                # маппинг старых значений к новой терминологии
                v = (getattr(u, "privacy_level") or "insights").lower()
                return {"insights":"ask"}.get(v, v)
            return "ask"

    def set_privacy(self, tg_id: str, value: str) -> None:
        value = value.lower().strip()
        # допустим ask | none | all | insights(=ask для обратной совместимости)
        if value == "insights": value = "ask"
        self.ensure_user(tg_id)
        with db_session() as s:
            u = s.query(User).filter_by(tg_id=str(tg_id)).first()
            if hasattr(u, "privacy_mode"):
                setattr(u, "privacy_mode", value)
            elif hasattr(u, "privacy_level"):
                # храним как есть (старое поле), но значения используем те же
                setattr(u, "privacy_level", "insights" if value=="ask" else value)
            s.commit()

    # ---------- Дневник/инсайты ----------
    def save_diary_entry(self, tg_id: str, text: str) -> int:
        """Сохраняет запись дневника/инсайт. Возвращает ID записи."""
        self.ensure_user(tg_id)
        with db_session() as s:
            if DiaryEntry is not None:
                rec = DiaryEntry(tg_id=str(tg_id), text=text, created_at=datetime.utcnow())  # type: ignore
            else:
                rec = Insight(tg_id=str(tg_id), text=text, created_at=datetime.utcnow())
            s.add(rec); s.commit(); s.refresh(rec)
            return int(rec.id)

    def list_diary(self, tg_id: str, limit: int = 20) -> List[Dict]:
        with db_session() as s:
            if DiaryEntry is not None:
                rows = s.query(DiaryEntry).filter_by(tg_id=str(tg_id)).order_by(DiaryEntry.created_at.desc()).limit(limit).all()  # type: ignore
            else:
                rows = s.query(Insight).filter_by(tg_id=str(tg_id)).order_by(Insight.created_at.desc()).limit(limit).all()
            out=[]
            for r in rows:
                created = getattr(r, "created_at", None)
                out.append({"id": int(r.id), "text": r.text, "created_at": created.isoformat() if created else None})
            return out

    # ---------- Событийные логи (опционально) ----------
    def log_event(self, tg_id: str, event_type: str, payload: Optional[str]=None) -> None:
        if BotEvent is None:
            return
        with db_session() as s:
            ev = BotEvent(user_id=None, event_type=event_type, payload=payload, created_at=datetime.utcnow())  # type: ignore
            s.add(ev); s.commit()
