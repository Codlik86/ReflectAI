# -*- coding: utf-8 -*-
"""
Проверка целостности app/exercises.py:
- уникальность topic_id и exercise.id
- обязательные поля
- базовые длины текстов
Запуск: python scripts/validate_exercises.py
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ex = importlib.import_module("app.exercises")

def main():
    topics = ex.TOPICS
    seen_topic_ids = set()
    seen_ex_ids = set()
    assert isinstance(topics, dict) and topics, "TOPICS должен быть непустым словарём"
    for tid, t in topics.items():
        assert tid not in seen_topic_ids, f"Дубликат topic_id: {tid}"
        seen_topic_ids.add(tid)
        assert "title" in t and isinstance(t["title"], str) and t["title"].strip(), f"{tid}: пустой title"
        ttype = t.get("type", "steps")
        assert ttype in ("steps","chat"), f"{tid}: неизвестный type={ttype}"
        if ttype == "chat":
            assert "chat_prompt" in t and t["chat_prompt"].strip(), f"{tid}: нужен chat_prompt"
        else:
            exs = t.get("exercises", [])
            assert isinstance(exs, list) and exs, f"{tid}: exercises пуст"
            for e in exs:
                eid = e.get("id")
                assert eid and isinstance(eid, str), f"{tid}: упражнение без id"
                assert eid not in seen_ex_ids, f"Дубликат exercise.id: {eid}"
                seen_ex_ids.add(eid)
                assert e.get("title","").strip(), f"{tid}/{eid}: пустой title"
                assert e.get("intro","").strip(), f"{tid}/{eid}: пустой intro"
                steps = e.get("steps", [])
                assert isinstance(steps, list) and steps, f"{tid}/{eid}: пустые steps"
                for i, s in enumerate(steps):
                    assert isinstance(s, str) and s.strip(), f"{tid}/{eid}: пустой шаг {i}"
    print(f"OK: {len(topics)} тем(ы), {len(seen_ex_ids)} упражнений, дублей нет.")

if __name__ == "__main__":
    main()
