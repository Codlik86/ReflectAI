import asyncio
import os

try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

from app.prompts import SYSTEM_PROMPT, LENGTH_HINTS
from app.llm_adapter import chat_with_style


async def run_once():
    sys_prompt = SYSTEM_PROMPT + "\n\n" + LENGTH_HINTS.get("short", "")
    msgs = [{"role": "system", "content": sys_prompt}]

    user_msgs = [
        "Привет! Это тестовый диалог, просто убедись, что можешь ответить.",
        "Чувствую усталость и тревогу, нужен быстрый совет.",
    ]
    replies = []

    for u in user_msgs:
        msgs.append({"role": "user", "content": u})
        reply = await chat_with_style(messages=msgs, temperature=0.66, max_completion_tokens=240, mode="talk")
        replies.append(reply.strip() if reply else "")
        msgs.append({"role": "assistant", "content": reply})
        print(f"user: {u}\nassistant: {reply}\n---")

    if replies[0] and replies[0] == replies[1]:
        print("WARN: second reply is identical to the first — possible looping.")
    else:
        print("OK: replies differ and consider context.")

    if os.getenv("SMOKE_BREAK_QDRANT") == "1":
        # Симулируем поломку Qdrant — проверяем, что LLM всё равно отвечает.
        os.environ["QDRANT_URL"] = "http://127.0.0.1:9"
        try:
            from app.rag_qdrant import search as rag_search  # импорт внутри, чтобы поймать ошибку клиента
            res = await rag_search("Проверка связи", k=3, max_chars=400, lang="ру")
            print("RAG result (broken URL):", bool(res))
        except Exception as e:
            print("RAG failed as expected with broken URL:", e)
        msgs_broken = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "Усталость, нет сил, проверь fail-open."},
        ]
        reply_broken = await chat_with_style(messages=msgs_broken, temperature=0.6, max_completion_tokens=200, mode="talk")
        print("LLM reply with broken Qdrant:", reply_broken)


def main():
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
