from app import llm_adapter as llm


def test_resolve_model_name_uses_openrouter_ids() -> None:
    assert llm.resolve_model_name("gpt-5.2") == "openai/gpt-5.2"
    assert llm.resolve_model_name("text-embedding-3-small") == "openai/text-embedding-3-small"
    assert llm.resolve_model_name("anthropic/claude-sonnet-4") == "anthropic/claude-sonnet-4"


def test_build_llm_headers_include_openrouter_metadata(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_HTTP_REFERER", "https://selflect.onrender.com")
    monkeypatch.setenv("OPENROUTER_TITLE", "ReflectAI")

    headers = llm.build_llm_headers("test-key")

    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"
    assert headers["HTTP-Referer"] == "https://selflect.onrender.com"
    assert headers["X-Title"] == "ReflectAI"


def test_build_llm_headers_force_ascii_title(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_HTTP_REFERER", raising=False)
    monkeypatch.setenv("OPENROUTER_TITLE", "Помни")

    headers = llm.build_llm_headers("test-key")

    assert headers["X-Title"] == "ReflectAI"


def test_extract_chat_text_supports_segment_list() -> None:
    data = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "output_text", "text": "hello"},
                        {"type": "output_text", "text": {"value": "world"}},
                    ]
                }
            }
        ]
    }

    assert llm._extract_chat_text(data) == "hello\nworld"
