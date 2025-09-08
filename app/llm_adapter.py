import os, httpx
from typing import List, Dict

class LLMAdapter:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")
        self.base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base}/chat/completions", json=payload, headers=self.headers)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()