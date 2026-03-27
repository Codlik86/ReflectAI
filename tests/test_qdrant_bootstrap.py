from app import qdrant_client as qc
from app import rag_qdrant as rq
from qdrant_client.http import models as qm


class _FakeClient:
    def __init__(self) -> None:
        self.created_collections = []
        self.created_indexes = []

    def create_collection(self, **kwargs) -> None:
        self.created_collections.append(kwargs)

    def create_payload_index(self, **kwargs) -> None:
        self.created_indexes.append(kwargs)


def test_normalize_lang_code_handles_aliases() -> None:
    assert qc.normalize_lang_code("ру") == "ru"
    assert qc.normalize_lang_code("ru-RU") == "ru"
    assert qc.normalize_lang_code("EN_us") == "en"


def test_build_lang_filter_keeps_canonical_value_first() -> None:
    flt, normalized = rq._build_lang_filter(qm, "ру")
    assert normalized == "ru"
    assert flt is not None
    condition = flt.must[0]
    match_any = getattr(condition.match, "any", None)
    assert match_any is not None
    assert list(match_any) == ["ru", "ру"]


def test_ensure_collection_creates_lang_index(monkeypatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(qc, "get_client", lambda: fake)
    monkeypatch.setattr(qc, "_collection_exists_safe", lambda client, name: True)

    assert qc.ensure_collection() is True
    assert [item["field_name"] for item in fake.created_indexes] == ["lang"]


def test_ensure_summaries_collection_creates_expected_indexes(monkeypatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(qc, "get_client", lambda: fake)
    monkeypatch.setattr(qc, "_collection_exists_safe", lambda client, name: True)

    assert qc.ensure_summaries_collection() is True
    assert [item["field_name"] for item in fake.created_indexes] == ["user_id", "kind"]
