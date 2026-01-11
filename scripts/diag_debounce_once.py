import asyncio

from app import bot as bot_module


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class _FakeMessage:
    def __init__(self, user_id: int, chat_id: int, text: str, message_id: int):
        self.from_user = _FakeUser(user_id)
        self.chat = _FakeChat(chat_id)
        self.text = text
        self.message_id = message_id
        self.bot = None  # typing loop skipped in diag


async def _diag_handler(_message, combined: str) -> None:
    parts = combined.count("\n") + 1 if combined else 0
    chars = len(combined or "")
    print(f"[debounce][diag] handler_called parts={parts} chars={chars}")
    print("[debounce][diag] combined_text:")
    print(combined)


async def run_once() -> None:
    user_id = 12345
    chat_id = 999

    msgs = [
        "Это первая часть.",
        "Вот вторая часть.",
        "И третья часть.",
    ]

    for i, txt in enumerate(msgs, start=1):
        msg = _FakeMessage(user_id, chat_id, txt, message_id=i)
        await bot_module._enqueue_talk_message(msg, txt, handler=_diag_handler)
        if i < len(msgs):
            await asyncio.sleep(0.3)

    await asyncio.sleep(3.0)


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
