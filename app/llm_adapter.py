from __future__ import annotations
import random
# app/llm_adapter.py
# -*- coding: utf-8 -*-

import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from typing import Optional, Dict, Any, List

import httpx

class LLMAdapter:
    """
    Адаптер к /chat/completions (OpenAI-совместимые API, в т.ч. прокси).

    Env:
      - OPENAI_API_KEY
      - OPENAI_BASE_URL  (например, https://api.proxyapi.ru/openai/v1)
      - OPENAI_MODEL     (например, gpt-4o-mini)
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._client: Optional[httpx.AsyncClient] = None

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY не задан")

    async def _client_async(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    # ----------------- ЕДИНЫЙ ПУБЛИЧНЫЙ ВХОД -----------------

    async def chat(self, messages: List[Dict[str, str]], temperature: float = 0.6, **opts) -> str:
        """
        Новый рекомендованный метод:
          await adapter.chat(messages=[{role, content}, ...], temperature=0.6)
        """
        return await self.complete_chat(messages=messages, temperature=temperature, **opts)

    # ----------------- БЭК-КОМПАТ ОТ ОБОИХ СТИЛЕЙ -----------------

    async def complete_chat(self, *args, **kwargs) -> str:
        """
        Бэк-совместимая обёртка. Поддерживает:
          - complete_chat(system, user, ...)
          - complete_chat(messages=[...], ...)
          - complete_chat(user=None, messages=[...], ...)
        """
        # Попробуем вытащить параметры из kwargs
        messages = kwargs.pop("messages", None)
        system = kwargs.pop("system", None)
        user_text = kwargs.pop("user", None)
        temperature = float(kwargs.pop("temperature", 0.6))
        mtx = kwargs.pop("max_tokens", None)
        max_tokens = int(mtx) if mtx is not None else random.choice([220, 260, 300, 340, 380, 420])
        extra: Optional[Dict[str, Any]] = kwargs.pop("extra", None)

        # Старый стиль позиционных аргументов: (system, user)
        if messages is None and len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
            system, user_text = args[0], args[1]

        # Ещё вариант: complete_chat(messages, ...)
        if messages is None and len(args) == 1 and isinstance(args[0], list):
            messages = args[0]

        # Если передали system/user — соберём messages из них
        if messages is None and (system is not None or user_text is not None):
            messages = [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": user_text or ""},
            ]

        if messages is None:
            raise TypeError("complete_chat: нужен 'messages' (list[{role, content}]) или пара (system, user)")

        # Собираем основной payload
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
    "top_p": 0.9,
    "presence_penalty": 0.6,
    "frequency_penalty": 0.3}

        # Разрешаем прокинуть любые дополнительные опции (top_p, presence_penalty и т.п.)
        if extra:
            payload.update(extra)
        if kwargs:
            # на случай, если кто-то передал неожиданные ключи — тоже прокинем
            payload.update(kwargs)

        try:
            client = await self._client_async()
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
            )
            text = (content or "").strip()
            return text or "Извини, у меня сейчас не выходит сформулировать ответ."
        except Exception:
            # fail-soft
            return "Извини, у меня небольшая заминка с ответом. Давай попробуем ещё раз?"

    # Синонимы на всякий случай
    async def complete(self, *a, **kw) -> str:
        return await self.complete_chat(*a, **kw)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

def apply_generation_defaults(payload: dict) -> dict:
    """
    Добавляет дефолтные параметры генерации, не перетирая уже заданные.
    """
    if payload is None:
        payload = {}
    payload = dict(payload)
    payload.setdefault("temperature", 0.85)
    payload.setdefault("top_p", 0.9)
    payload.setdefault("presence_penalty", 0.6)
    payload.setdefault("frequency_penalty", 0.3)
    payload.setdefault("max_tokens", random.choice([220, 260, 300, 340, 380, 420]))
    return payload

