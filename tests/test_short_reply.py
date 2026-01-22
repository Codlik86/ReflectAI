from app.services.short_reply import is_short_reply, normalize_short_reply


def test_is_short_reply_basic_yes() -> None:
    assert is_short_reply("да") is True
    assert is_short_reply("угу") is True
    assert is_short_reply("ок") is True


def test_is_short_reply_rejects_url_and_long() -> None:
    assert is_short_reply("https://example.com") is False
    assert is_short_reply("это уже не короткий ответ") is False


def test_normalize_short_reply_includes_last_bot_turn() -> None:
    txt = normalize_short_reply("да", "Ты хочешь продолжить разговор?")
    assert "Короткий ответ пользователя" in txt
    assert "да" in txt


def test_normalize_short_reply_passthrough() -> None:
    original = "мне нужно больше времени, чтобы подумать"
    assert normalize_short_reply(original, "вопрос") == original
