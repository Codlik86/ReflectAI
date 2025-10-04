# scripts/talk_regression.py
# -*- coding: utf-8 -*-
"""
Regression: multi-turn 'talk' conversations to check context adherence.

Env:
  OPENAI_API_KEY (required)
  OPENAI_BASE_URL (optional; defaults from llm_adapter)
  CHAT_MODEL (optional)

Outputs:
  out/dialogs.json
  out/dialogs.md
  out/report.csv
"""

import os
import csv
import json
import time
import asyncio
from pathlib import Path
from typing import List, Dict, Any

# Use our in-app prompt and adapter
from app.prompts import SYSTEM_PROMPT, STYLE_SUFFIXES
from app.llm_adapter import chat_with_style

OUT_DIR = Path("out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# -------- Scenarios (7–10 turns each) --------
# NB: we do not broaden topics here, only lengthen conversations.
SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "panic_attack",
        "title": "Паническая атака на месте",
        "style": "therapist",
        "turns": [
            "мне тревожно и кружится голова",
            "кажется, начинается паническая атака",
            "ладони потеют, воздуха мало",
            "что делать прямо сейчас?",
            "каждый раз это накрывает внезапно",
            "после приступа ещё полдня я разбит",
            "можно ли как-то предотвратить заранее?",
            "если случится в метро — что делать?",
        ],
    },
    {
        "id": "work_conflict",
        "title": "Конфликт на работе, ощущение неприязни",
        "style": "friend",
        "turns": [
            "меня бесит работа",
            "коллеги ведут себя мерзко",
            "меня не уважают и шепчутся за спиной",
            "я хочу просто уйти с работы",
            "но страшно потерять доход",
            "иногда думаю, что со мной что-то не так",
            "как понять — менять работу или пытаться наладить?",
            "если говорить с руководителем — что именно спросить?",
            "что сделать завтра, чтобы стало полегче?",
        ],
    },
    {
        "id": "depressed_mood",
        "title": "Грусть, упадок сил, ничего не радует",
        "style": "therapist",
        "turns": [
            "мне грустно уже неделю",
            "всё валится из рук",
            "ничего не хочется",
            "сон плохой, аппетит то есть то нет",
            "работа тоже стала тяжёлой",
            "я не хочу советов, просто побудь рядом",
            "как понять, это депрессия или просто усталость?",
            "что можно сделать сегодня на 10 минут?",
            "если не поможет — что дальше?",
        ],
    },
    {
        "id": "relationship_trust",
        "title": "Подозрение на измену, тревога",
        "style": "friend",
        "turns": [
            "мне кажется, партнёр мне изменяет",
            "я видел переписку случайно",
            "хочется всё бросить",
            "я злюсь и одновременно боюсь",
            "стоит ли говорить напрямую?",
            "как это обсудить без скандала?",
            "а если он всё отрицает?",
            "как себя поддержать на ближайшие сутки?",
        ],
    },
    {
        "id": "everyday_overload",
        "title": "Бытовая перегрузка, дедлайны и хаос",
        "style": "default",
        "turns": [
            "я тону в делах",
            "почта, бытовые задачи, дедлайны",
            "могу ли я за час навести порядок?",
            "меня срывает от мелочей",
            "как не уйти в прокрастинацию?",
            "что сделать сегодня вечером, чтобы легче завтра?",
            "а если сорвусь — что мне напомнить?",
            "итог: какой один шаг взять на завтра утром?",
        ],
    },
]

# -------- helper: slight temperature wobble like in bot.py --------
def temp_for(text: str) -> float:
    base = 0.62
    wobble = (abs(hash(text)) % 13) / 100.0  # +0.00..0.12
    return round(base + wobble, 2)

# -------- dialogue runner --------
async def run_one(scn: Dict[str, Any]) -> Dict[str, Any]:
    messages: List[Dict[str, str]] = []

    # build system: base + tone suffix
    sys = SYSTEM_PROMPT
    style = scn.get("style", "default")
    tone_suffix = STYLE_SUFFIXES.get(style, "")
    if tone_suffix:
        sys += "\n\n" + tone_suffix

    messages.append({"role": "system", "content": sys})

    dialogue: List[Dict[str, str]] = []
    for i, user_uttr in enumerate(scn["turns"], start=1):
        messages.append({"role": "user", "content": user_uttr})

        # call our adapter
        try:
            reply = await chat_with_style(
                messages=messages,
                temperature=temp_for(user_uttr),
                max_tokens=420,
            )
        except TypeError:
            reply = await chat_with_style(messages, temperature=temp_for(user_uttr), max_tokens=420)
        except Exception as e:
            reply = f"[ERROR] {e}"

        messages.append({"role": "assistant", "content": reply})
        dialogue.append({"turn": i, "user": user_uttr, "bot": reply})

        # gentle pacing to avoid rate limits
        time.sleep(0.4)

    return {
        "id": scn["id"],
        "title": scn["title"],
        "style": style,
        "dialogue": dialogue,
    }

async def main() -> None:
    results = []
    for scn in SCENARIOS:
        res = await run_one(scn)
        results.append(res)

    # JSON
    (OUT_DIR / "dialogs.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Markdown (compact and human-readable)
    md_lines: List[str] = ["# Диалоги — регрессионный прогон\n"]
    for r in results:
        md_lines.append(f"## {r['title']}  \n(style: {r['style']})\n")
        for t in r["dialogue"]:
            md_lines.append(f"**E:** {t['user']}")
            md_lines.append(f"**Помни:** {t['bot']}\n")
    (OUT_DIR / "dialogs.md").write_text("\n".join(md_lines), encoding="utf-8")

    # CSV: per turn row
    with (OUT_DIR / "report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["scenario_id", "scenario_title", "style", "turn", "user", "bot"])
        for r in results:
            for t in r["dialogue"]:
                w.writerow([r["id"], r["title"], r["style"], t["turn"], t["user"], t["bot"]])

    print("Saved:", OUT_DIR / "dialogs.json")
    print("Saved:", OUT_DIR / "dialogs.md")
    print("Saved:", OUT_DIR / "report.csv")

if __name__ == "__main__":
    asyncio.run(main())
