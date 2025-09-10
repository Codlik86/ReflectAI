import os
import httpx
from typing import Optional, Dict, Any

class LLMAdapter:
    """
    Адаптер для chat.completions через прокси OpenAI.

    Env:
      - OPENAI_API_KEY
      - OPENAI_BASE_URL (например, https://api.proxyapi.ru/openai/v1)
      - OPENAI_MODEL (например, gpt-4o-mini)
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._client: Optional[httpx.AsyncClient] = None

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

    async def complete_chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 700,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Асинхронный вызов /chat/completions. Возвращает текст первого выбора."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": user or ""},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extra:
            payload.update(extra)

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
            return (content or "").strip() or "Извини, сейчас не получилось сформулировать ответ."
        except Exception:
            # fail-soft
            return "Извини, у меня сейчас небольшая заминка с ответом. Давай попробуем ещё раз?"

    # Алиасы на будущее — если где-то вызовут другой нейминг
    async def chat(self, *a, **kw) -> str:
        return await self.complete_chat(*a, **kw)

    async def complete(self, *a, **kw) -> str:
        return await self.complete_chat(*a, **kw)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
