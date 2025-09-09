from typing import Optional, List
from app.db import db_session, UserMemory, JournalEntry, UserSettings, BotEvent
from app.prompts import MEMORY_SUMMARIZER_PROMPT

# Универсальный вызов LLM: поддерживает .complete(...) и .complete_chat(...)
def _llm_call(adapter, system: str, user: str, max_tokens: int = 280) -> str:
    if hasattr(adapter, "complete"):
        return adapter.complete(system=system, user=user, max_tokens=max_tokens)
    if hasattr(adapter, "complete_chat"):
        return adapter.complete_chat(system=system, user=user, max_tokens=max_tokens)
    raise RuntimeError("LLM adapter must have .complete or .complete_chat")

def log_event(user_id: int, event_type: str, payload: str = ""):
    with db_session() as s:
        s.add(BotEvent(user_id=user_id, event_type=event_type, payload=payload))
        s.commit()

def add_journal_entry(user_id: int, text: str, tags: Optional[str] = None) -> JournalEntry:
    with db_session() as s:
        entry = JournalEntry(user_id=user_id, text=text, tags=tags)
        s.add(entry)
        s.commit()
        s.refresh(entry)
        return entry

def get_user_memory(user_id: int) -> str:
    with db_session() as s:
        mem = s.query(UserMemory).filter(UserMemory.user_id == user_id).first()
        return (mem.summary if mem else "") or ""

def update_user_memory(user_id: int, new_text: str, adapter) -> None:
    piece = _llm_call(adapter, MEMORY_SUMMARIZER_PROMPT, new_text, max_tokens=280).strip()
    with db_session() as s:
        mem = s.query(UserMemory).filter(UserMemory.user_id == user_id).first()
        if not mem:
            mem = UserMemory(user_id=user_id, summary=piece)
            s.add(mem)
        else:
            mem.summary = ((mem.summary or "") + "\n" + piece)[-4000:]
        s.commit()

def get_user_settings(user_id: int) -> UserSettings:
    with db_session() as s:
        st = s.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not st:
            st = UserSettings(user_id=user_id)
            s.add(st); s.commit(); s.refresh(st)
        return st

def set_user_tone(user_id: int, tone: str) -> None:
    with db_session() as s:
        st = s.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not st:
            st = UserSettings(user_id=user_id, tone=tone); s.add(st)
        else:
            st.tone = tone
        s.commit()

def set_user_method(user_id: int, method: str) -> None:
    with db_session() as s:
        st = s.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not st:
            st = UserSettings(user_id=user_id, method=method); s.add(st)
        else:
            st.method = method
        s.commit()

# Интент «помощь/практика/план» (включаем ассистентный ответ)
HELP_KEYWORDS: List[str] = [
    "что делать", "как справ", "помоги", "помощь", "план", "шаг", "упражн",
    "практик", "совет", "подскажи", "дай рекомендац", "разложи", "по полочкам"
]
def is_help_intent(text: Optional[str]) -> bool:
    t = (text or "").lower()
    return ("?" in t) or any(kw in t for kw in HELP_KEYWORDS)
