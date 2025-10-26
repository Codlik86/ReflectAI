# app/llm_adapter.py
import os
import json
from typing import Any, Dict, List, Optional

import httpx

"""
llm_adapter.py
--------------
Асинхронный адаптер к OpenAI-совместимому /chat/completions API.

Возможности:
- Класс LLMAdapter с методом complete_chat(...)
- Функции chat(...) / complete_chat(...) на синглтоне
- Обёртка chat_with_style(...): подмешивает style/style_hint в system
- Опциональное подмешивание RAG-контекста отдельным system-сообщением
- Конфиги из ENV:
    OPENAI_API_KEY
    OPENAI_BASE_URL (например, https://api.proxyapi.ru/openai/v1)
    CHAT_MODEL (по умолчанию gpt-4o-mini)
"""

# ===== Новые переменные и алиасы для гибкой маршрутизации моделей =====
DEFAULT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
STRONG_MODEL = os.getenv("CHAT_MODEL_STRONG", "gpt-5")  # «старшая» модель для длинных/сложных ответов
TALK_MODEL = os.getenv("CHAT_MODEL_TALK", STRONG_MODEL)  # можно задать отдельную для talk/reflection
FALLBACK_TO_DEFAULT = os.getenv("LLM_FALLBACK_TO_DEFAULT", "1") == "1"  # мягкий фолбэк при 5xx/429

# Алиасы имён моделей на случай отличий в прокси-доке
MODEL_ALIASES: Dict[str, str] = {
    "chat-gpt5": "gpt-5",
    "gpt-5-thinking": "gpt-5",
    "gpt5": "gpt-5",
    "gpt-4o-mini": "gpt-4o-mini",
}

def _resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)

def _pick_model(ctx: Dict[str, Any]) -> str:
    """
    Простейший маршрутизатор по контексту:
    ctx: { mode: 'talk'|'reflection'|'work'|'system', is_crisis: bool, needs_long_context: bool }
    """
    if ctx.get("is_crisis") or ctx.get("needs_long_context"):
        return _resolve_model(STRONG_MODEL)
    if ctx.get("mode") in ("talk", "reflection"):
        return _resolve_model(TALK_MODEL or STRONG_MODEL)
    return _resolve_model(DEFAULT_MODEL)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1").rstrip("/")


# --------- Core adapter ---------

class LLMAdapter:
    def __init__(self, *, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or OPENAI_API_KEY
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self.model = _resolve_model(model or DEFAULT_MODEL)

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY не задан в окружении")

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                timeout=90.0,  # ↑ запас по времени
            )
        return self._client

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def complete_chat(
        self,
        *,
        messages: Optional[List[Dict[str, str]]] = None,
        system: Optional[str] = None,
        user: Optional[str] = None,
        temperature: float = 0.6,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        """
        Высокоуровневый метод: принимает либо messages=[...], либо system+user.
        Возвращает текст assistant'а.
        """
        # Build message list
        msg_list: List[Dict[str, str]] = []
        if messages is not None:
            msg_list = messages[:]
        else:
            if system:
                msg_list.append({"role": "system", "content": system})
            if user:
                msg_list.append({"role": "user", "content": user})

        if not msg_list:
            raise ValueError("Нет сообщений для complete_chat(...)")

        # Базовый payload + предсказуемый дефолт response_format
        response_format = kwargs.pop("response_format", {"type": "text"})
        payload: Dict[str, Any] = {
            "model": _resolve_model(model or self.model),
            "messages": msg_list,
            "temperature": float(temperature),
            "response_format": response_format,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Pass-through (stop, n, penalties и т.п.)
        for k, v in kwargs.items():
            if k in ("stop", "presence_penalty", "frequency_penalty", "n"):
                payload[k] = v

        client = await self._get_client()
        url = "/chat/completions"  # OpenAI-compatible path

        # Устойчивые ретраи для 429/5xx + лёгкий бэкофф; при неудаче — опциональный фолбэк на DEFAULT_MODEL
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
                # Expected OpenAI-like schema
                return data["choices"][0]["message"]["content"].strip()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                # Попытки и бэкофф
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    import asyncio, random
                    await asyncio.sleep(0.6 * (attempt + 1) + random.random() * 0.3)
                    last_err = e
                    continue
                # Мягкий фолбэк на DEFAULT_MODEL (если сейчас не она)
                try_model = payload.get("model")
                if FALLBACK_TO_DEFAULT and try_model != _resolve_model(DEFAULT_MODEL):
                    try:
                        payload["model"] = _resolve_model(DEFAULT_MODEL)
                        r2 = await client.post(url, json=payload)
                        r2.raise_for_status()
                        data2 = r2.json()
                        return data2["choices"][0]["message"]["content"].strip()
                    except Exception:
                        pass
                raise RuntimeError(f"LLM HTTP error {status}: {e.response.text}") from e
            except Exception as e:
                if attempt < 2:
                    import asyncio, random
                    await asyncio.sleep(0.5 * (attempt + 1) + random.random() * 0.2)
                    last_err = e
                    continue
                # Фолбэк на DEFAULT_MODEL при сетевых/прочих сбоях
                try_model = payload.get("model")
                if FALLBACK_TO_DEFAULT and try_model != _resolve_model(DEFAULT_MODEL):
                    try:
                        payload["model"] = _resolve_model(DEFAULT_MODEL)
                        r2 = await client.post(url, json=payload)
                        r2.raise_for_status()
                        data2 = r2.json()
                        return data2["choices"][0]["message"]["content"].strip()
                    except Exception:
                        pass
                raise RuntimeError(f"LLM request failed: {e}") from e

        # На всякий случай (сюда не дойдём при raise выше)
        if last_err:
            raise RuntimeError(f"LLM request failed after retries: {last_err}")
        raise RuntimeError("LLM request failed for unknown reasons")


# --- Module-level singleton & helpers (so chat_with_style can call them) ---
_ADAPTER: Optional[LLMAdapter] = None

def _get_adapter() -> LLMAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = LLMAdapter()
    return _ADAPTER


async def chat(
    *,
    messages: Optional[List[Dict[str, str]]] = None,
    system: Optional[str] = None,
    user: Optional[str] = None,
    temperature: float = 0.78,  # синхронизируем с chat_with_style
    **opts: Any
) -> str:
    """
    Унифицированная модульная функция для чата (используется chat_with_style).
    Поддерживает как messages=[...], так и system+user.
    """
    return await _get_adapter().complete_chat(
        messages=messages, system=system, user=user, temperature=temperature, **opts
    )


async def complete_chat(*args, **kwargs) -> str:
    # Совместимость с поиском имени
    return await chat(*args, **kwargs)


# --- Helpers ---

def _inject_style_into_system(system_text: Optional[str], style_hint: Optional[str]) -> str:
    """Склеиваем system + style (если задан)."""
    base = (system_text or "").strip()
    if style_hint:
        if base:
            base = base + "\n\n" + style_hint.strip()
        else:
            base = style_hint.strip()
    return base or "Ты — эмпатичный друг и психолог в одном лице. Говори тепло, прямо, поддерживающе."


def _append_rag_context(msgs: List[Dict[str, str]], rag_ctx: Optional[str]) -> List[Dict[str, str]]:
    """Подмешиваем RAG-контекст отдельным system-сообщением (если есть)."""
    if rag_ctx and isinstance(rag_ctx, str) and rag_ctx.strip():
        msgs = msgs + [{"role": "system", "content": f"Контекст:\n{rag_ctx.strip()}"}]
    return msgs


# --- Style wrapper (с поддержкой rag_ctx и обратной совместимостью по style_hint) ---

async def chat_with_style(
    *,
    system: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    user: Optional[str] = None,
    style: Optional[str] = None,
    style_hint: Optional[str] = None,
    rag_ctx: Optional[str] = None,
    temperature: float = 0.75,  
    mode: Optional[str] = None,
    is_crisis: bool = False,
    needs_long_context: bool = False,
    model_override: Optional[str] = None,
    **kwargs: Any
) -> str:
    # 1) Склеиваем system + style (или style_hint)
    style_text = style if style is not None else style_hint
    sys = _inject_style_into_system(system, style_text)

    # 2) Выбор модели (маршрутизатор)
    chosen_model = None
    if model_override:
        chosen_model = _resolve_model(model_override)
    else:
        chosen_model = _pick_model({
            "mode": mode,
            "is_crisis": is_crisis,
            "needs_long_context": needs_long_context,
        })

    # >>> ДОБАВЬ ЭТО: дефолты «рандомности без лотереи»
    kwargs.setdefault("top_p", 0.9)
    kwargs.setdefault("presence_penalty", 0.4)   # снижает повторы тем/заходов
    kwargs.setdefault("frequency_penalty", 0.1)  # убирает лексические повторы


    # 3) Если нам передали messages, аккуратно добавим/заменим system
    if messages is not None:
        msgs: List[Dict[str, str]] = list(messages)
        need_inject = bool(system or style_text)

        if need_inject:
            system_found = False
            new_msgs: List[Dict[str, str]] = []
            for m in msgs:
                if not system_found and m.get("role") == "system":
                    new_msgs.append({"role": "system", "content": sys})
                    system_found = True
                else:
                    new_msgs.append(m)
            msgs = new_msgs
            # если system так и не встретился — аккуратно добавим его в начало
            if not system_found:
                msgs = [{"role": "system", "content": sys}] + msgs

        # Подмешиваем RAG-контекст отдельным system-сообщением в хвост
        msgs = _append_rag_context(msgs, rag_ctx)
        return await chat(messages=msgs, temperature=temperature, model=chosen_model, **kwargs)

    # 4) Если messages не задан — путь system+user
    msgs = [{"role": "system", "content": sys}]
    msgs = _append_rag_context(msgs, rag_ctx)
    if user:
        msgs.append({"role": "user", "content": user})
    return await chat(messages=msgs, temperature=temperature, model=chosen_model, **kwargs)


# --- Embeddings via OpenAI ---
_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Синхронная обёртка над OpenAI /embeddings.
    Возвращает список векторов такой же длины, как входной список `texts`.
    """
    if not _OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    url = f"{_OPENAI_BASE_URL}/embeddings"
    headers = {"Authorization": f"Bearer {_OPENAI_API_KEY}"}
    payload = {"model": _OPENAI_EMBED_MODEL, "input": texts}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Порядок сохраняется 1:1 с входом
    return [item["embedding"] for item in data["data"]]
