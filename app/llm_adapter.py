# app/llm_adapter.py
import os
import json
from typing import Any, Dict, List, Optional

import httpx

"""
llm_adapter.py
--------------
Асинхронный адаптер к OpenAI-совместимому /chat/completions API.

Ключевые возможности:
- Класс LLMAdapter с методом complete_chat(...)
- Модульные функции chat(...) / complete_chat(...) на синглтоне
- Обёртка chat_with_style(...), аккуратно подмешивающая style/style_hint в system
- Поддержка подмешивания RAG-контекста (rag_ctx) отдельным system-сообщением
- Безопасные дефолты: модель и базовый URL берутся из окружения:
    OPENAI_API_KEY
    OPENAI_BASE_URL (например, https://api.proxyapi.ru/openai/v1)
    CHAT_MODEL (по умолчанию gpt-4o-mini)
"""

DEFAULT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1").rstrip("/")


# --------- Core adapter ---------

class LLMAdapter:
    def __init__(self, *, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or OPENAI_API_KEY
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL

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
                timeout=60.0,
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

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": msg_list,
            "temperature": float(temperature),
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Pass-through other opts (e.g., response_format, stop, n, penalties)
        for k, v in kwargs.items():
            if k in ("stop", "presence_penalty", "frequency_penalty", "response_format", "n"):
                payload[k] = v

        client = await self._get_client()
        url = "/chat/completions"  # OpenAI-compatible path

        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            text = e.response.text
            raise RuntimeError(f"LLM HTTP error {e.response.status_code}: {text}") from e
        except Exception as e:
            raise RuntimeError(f"LLM request failed: {e}") from e

        data = r.json()
        # Expected OpenAI-like schema
        try:
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            # Fallback to raw text for debugging
            return json.dumps(data, ensure_ascii=False)


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
    temperature: float = 0.6,
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
    # Совместимость с поиском имени в chat_with_style
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
    return base or "Ты — тёплый русскоязычный собеседник и друг. Общайся на «ты», без диагнозов и медицинских рекомендаций."


def _append_rag_context(msgs: List[Dict[str, str]], rag_ctx: Optional[str]) -> List[Dict[str, str]]:
    """Подмешиваем RAG-контекст отдельным system-сообщением (если есть)."""
    if rag_ctx and isinstance(rag_ctx, str) and rag_ctx.strip():
        msgs = msgs + [{"role": "system", "content": f"Контекст:\n{rag_ctx.strip()}"}]
    return msgs


# --- Style wrapper (с поддержкой rag_ctx и обратной совместимостью по style_hint) ---

async def chat_with_style(
    *,
    # основной путь: system + messages
    system: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    # альтернативный путь: system + user (если messages не задан)
    user: Optional[str] = None,
    # стилизация
    style: Optional[str] = None,
    style_hint: Optional[str] = None,  # алиас для обратной совместимости
    # контекст поиска/базы (опционально)
    rag_ctx: Optional[str] = None,
    # сэмплинг
    temperature: float = 0.6,
    **kwargs: Any
) -> str:
    """
    Обёртка, которая добавляет style/style_hint в system и опционально подмешивает rag_ctx.
    Затем делегирует базовой chat(...).
    """
    # 1) Склеиваем system + style (или style_hint)
    style_text = style if style is not None else style_hint
    sys = _inject_style_into_system(system, style_text)

    # 2) Если нам передали messages, аккуратно добавим/заменим system
    if messages is not None:
        msgs: List[Dict[str, str]] = []
        system_found = False
        for m in messages:
            if m.get("role") == "system" and not system_found:
                msgs.append({"role": "system", "content": sys})
                system_found = True
            else:
                msgs.append(m)
        if not system_found:
            msgs.insert(0, {"role": "system", "content": sys})

        # Подмешиваем RAG-контекст отдельным system-сообщением в хвост
        msgs = _append_rag_context(msgs, rag_ctx)
        return await chat(messages=msgs, temperature=temperature, **kwargs)

    # 3) Если messages не задан — путь system+user
    msgs = [{"role": "system", "content": sys}]
    msgs = _append_rag_context(msgs, rag_ctx)
    if user:
        msgs.append({"role": "user", "content": user})
    return await chat(messages=msgs, temperature=temperature, **kwargs)
