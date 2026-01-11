from collections import deque

from app import bot as bot_module


def main() -> None:
    prefixes = deque(maxlen=bot_module._OPENER_PREFIX_HISTORY)

    first = "Похоже, тебе тяжело."
    p1 = bot_module.normalize_opener_prefix(first)
    prefixes.append(p1)
    print(f"[opener][diag] first_prefix={p1!r} banned={bot_module._is_banned_opener_prefix(p1)}")

    second = "Похоже, это непросто."
    p2 = bot_module.normalize_opener_prefix(second)
    repeat = bot_module._is_repeat_opener_prefix(p2, prefixes)
    banned = bot_module._is_banned_opener_prefix(p2)
    print(f"[opener][diag] second_prefix={p2!r} repeat={repeat} banned={banned}")

    stripped = bot_module._strip_banned_prefix(second)
    p3 = bot_module.normalize_opener_prefix(stripped)
    print(f"[opener][diag] stripped_prefix={p3!r} text={stripped!r}")


if __name__ == "__main__":
    main()
