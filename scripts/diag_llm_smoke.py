"""
Мини-smoke для LLM: один запрос к GPT-5.2 без фолбэков, выводит сырой ответ или ошибку.
"""
import asyncio
import os

try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

from app.llm_adapter import chat_with_style


async def main():
    try:
        reply = await chat_with_style(
            messages=[
                {"role": "system", "content": "Ты отвечаешь лаконично и по делу."},
                {"role": "user", "content": "Коротко: чем отличается эмпатия от симпатии? 1-2 предложения."},
            ],
            temperature=0.4,
            max_completion_tokens=120,
            mode="talk",
        )
        print("LLM reply:", reply)
    except Exception as e:
        print("LLM error:", repr(e))


if __name__ == "__main__":
    asyncio.run(main())
