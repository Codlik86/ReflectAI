# scripts/stress_talk.py
# -*- coding: utf-8 -*-
"""
Stress test: few topics, but LONG multi-turn dialogs to check deep context,
1-question rule, and thematic coherence.

Env:
  OPENAI_API_KEY (required)
  OPENAI_BASE_URL (optional; defaults from llm_adapter)
  CHAT_MODEL (optional)

Outputs (in a timestamped dir by default):
  out/stress_<YYYYmmdd-HHMMSS>/dialogs.json
  out/stress_<YYYYmmdd-HHMMSS>/dialogs.md
  out/stress_<YYYYmmdd-HHMMSS>/report.csv
"""

import os
import re
import csv
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Any

# --- project root on sys.path ---
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# --------------------------------

from app.prompts import SYSTEM_PROMPT, STYLE_SUFFIXES, LENGTH_HINTS
from app.llm_adapter import chat_with_style


# ---------- Scenarios (few topics, long chains) ----------
def scenarios() -> List[Dict[str, Any]]:
    """
    Each scenario: {
      "name": str,
      "tone": "default"|"friend"|"therapist"|"18plus",
      "len_hint": "short"|"medium"|"deep" (appended to system),
      "turns": [ "user message 1", "user message 2", ... ]  # model replies in between
    }
    """
    return [
        {
            "name": "Workplace disrespect & boundaries",
            "tone": "therapist",
            "len_hint": "medium",
            "turns": [
                "Мне тяжело ходить на работу — ощущение, что меня там не уважают.",
                "Иногда прямо слышу пассивную агрессию, а начальник делает вид, что не замечает.",
                "Каждый день думаю: может просто уволиться, но страшно за доходы.",
                "Боюсь разговаривать с одним человеком из отдела — он язвит при других.",
                "У меня уже телом реагирует: спина каменная, дышу поверхностно.",
                "Не понимаю, как ставить границы, если формально он выше по должности.",
                "Пару раз пытался возразить фактами — стали подкалывать сильнее.",
                "Дома разряжаюсь на близких, этого не хочу.",
                "Если начать фиксировать эпизоды — не станет ли хуже?",
                "Как провести разговор по фактам, если эмоции шкалят?",
                "Какой первый шаг сделать завтра, чтобы это не было войной?",
                "Если честно, боюсь, что меня сочтут конфликтным и уволят.",
                "Что делать, если опять начнут оскорблять намёками?",
                "Я уже думаю о переводе в другой отдел — это не бегство?",
                "Под конец дня меня просто штормит — не сплю нормально.",
                "Хочу проверить — вдруг я сам что-то делаю не так. С чего начать?",
            ],
        },
        {
            "name": "Relationship: suspicion of infidelity",
            "tone": "friend",
            "len_hint": "medium",
            "turns": [
                "Похоже, узнал(а) неприятную вещь: кажется, партнёр мне изменяет.",
                "Нашёл(а) переписку, но прямого признания нет. В голове каша.",
                "Не знаю, говорить ли об этом прямо — боюсь разрушить всё окончательно.",
                "В теле дрожь, на работе не могу собраться.",
                "Часть меня хочет всё выяснить жёстко, часть — просто закрыть глаза.",
                "Делаю скриншоты переписки — это паранойя?",
                "Думаю, может сначала спросить нейтрально: «что у нас происходит?»",
                "Есть страх, что я вылью всё в обвинениях — эмоции зашкаливают.",
                "Если подтвердится — как вообще пережить это и не сломаться?",
                "Есть ли смысл пробовать паузу, чтобы остыть?",
                "Боюсь, что если уйду — пожалею. Если останусь — буду мучиться.",
                "Нужен микро-план на ближайшие сутки, чтобы не утонуть.",
                "А если он(а) всё перевернёт и скажет, что я придумал(а)?",
                "Я запутался(ась): говорить прямо или собрать факты и потом?",
                "Всё время думаю, что со мной что-то не так, раз так вышло.",
                "Хочу вернуть ощущение опоры хотя бы на вечер.",
            ],
        },
        {
            "name": "Panic/grounding under heavy stress",
            "tone": "therapist",
            "len_hint": "short",
            "turns": [
                "Сейчас накрывает паникой: потные ладони, не хватает воздуха.",
                "Страшно, что это повторится среди людей.",
                "Слишком много задач и дедлайнов, кажется, не вывожу.",
                "Кажется, сердце скачет, хочу сбежать.",
                "Иногда помогает дыхание, но сегодня никак.",
                "Нужно что-то на 1–2 минуты прямо сейчас.",
                "После приступа чувствую пустоту и стыд.",
                "Хочется, чтобы было хоть немного контроля.",
                "Хочу маленький ритуал на каждый день, чтобы не ждать обвала.",
                "Через час встреча — боюсь сорваться прямо там.",
                "Как объяснить коллегам, если станет плохо?",
                "Хочу закрыть вечер без истощения.",
            ],
        },
        {
            "name": "Procrastination on important personal project",
            "tone": "default",
            "len_hint": "deep",
            "turns": [
                "Тяну личный проект уже год — постоянно откладываю.",
                "Как только сажусь, в голове шум: «всё равно не получится».",
                "Есть большой страх оценок — будто осмеют.",
                "Пробовал(а) план на неделю — срываюсь на втором дне.",
                "Залипаю на мелочах и псевдоподготовке.",
                "Хочу близкую победу, чтобы почувствовать движение.",
                "Иногда полезно менять контекст, но я снова возвращаюсь в прокрастинацию.",
                "Не знаю, какую самую маленькую задачу выбрать.",
                "Хочу «эксперимент без наказания» — как его сформулировать?",
                "Нужен способ отследить, что я реально продвинулся(ась).",
                "Как защищать время от отвлечений дома?",
                "Что сказать себе, когда включается «всё пропало»?",
                "Стоит ли кому-то рассказать, чтобы появилась ответственность?",
                "Как не превратить всё в ещё один контрольный список?",
                "Что делать, если появится идеализм и я начну переписывать всё?",
                "Хочу довести первый маленький кусок до конца на этой неделе.",
            ],
        },
    ]


# ---------- Helpers ----------
def now_slug() -> str:
    return time.strftime("%Y%m%d-%H%M%S")

def count_questions(text: str) -> int:
    return text.count("?")

def ends_with_question(text: str) -> bool:
    return text.strip().endswith("?")

def one_question_rule_ok(text: str) -> bool:
    # allow at most one question mark; tolerate "?!"
    return count_questions(text) <= 1

def charlen(text: str) -> int:
    return len(text or "")

def sanitize(s: str) -> str:
    return (s or "").replace("\r", "").strip()


# ---------- Runner ----------
async def run_dialog(topic: Dict[str, Any], temperature: float, max_completion_tokens: int) -> Dict[str, Any]:
    sys_prompt = SYSTEM_PROMPT
    tone = STYLE_SUFFIXES.get(topic["tone"], "")
    if tone:
        sys_prompt += "\n\n" + tone

    # length hint to bias variability
    hint_key = topic.get("len_hint", "short")
    hint = LENGTH_HINTS.get(hint_key, "")
    if hint:
        sys_prompt += "\n\n" + hint

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    transcript: List[Dict[str, str]] = []

    for turn in topic["turns"]:
        # user turn
        u = sanitize(turn)
        messages.append({"role": "user", "content": u})
        transcript.append({"role": "user", "content": u})

        # model reply
        try:
            reply = await chat_with_style(
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
            )
        except TypeError:
            reply = await chat_with_style(messages, temperature=temperature, max_completion_tokens=max_completion_tokens)

        reply = sanitize(reply)
        transcript.append({"role": "assistant", "content": reply})
        messages.append({"role": "assistant", "content": reply})

    # simple metrics
    replies = [t["content"] for t in transcript if t["role"] == "assistant"]
    if not replies:
        replies = [""]

    q_end = sum(1 for r in replies if ends_with_question(r)) / len(replies)
    one_q_ok = sum(1 for r in replies if one_question_rule_ok(r)) / len(replies)
    avg_len = sum(charlen(r) for r in replies) / max(1, len(replies))
    too_long = sum(1 for r in replies if charlen(r) > 900)

    return {
        "name": topic["name"],
        "tone": topic["tone"],
        "len_hint": topic.get("len_hint"),
        "turns_count": len(topic["turns"]),
        "transcript": transcript,
        "metrics": {
            "pct_end_with_question": round(q_end * 100, 1),
            "pct_one_question_ok": round(one_q_ok * 100, 1),
            "avg_reply_chars": round(avg_len, 1),
            "num_too_long_replies(>900ch)": int(too_long),
        },
    }


async def main():
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default=f"out/stress_{now_slug()}")
    parser.add_argument("--temp", type=float, default=0.66)
    parser.add_argument("--max_completion_tokens", type=int, default=520)
    parser.add_argument("--delay", type=float, default=0.0, help="sleep seconds between turns")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = []
    dialogs_md = []
    rows = []

    print(f"[stress] running {len(scenarios())} long dialogs → {out_dir}")

    for sc in scenarios():
        item = await run_dialog(sc, temperature=args.temp, max_completion_tokens=args.max_completion_tokens)
        data.append(item)

        # MD
        dialogs_md.append(f"# {item['name']}  \n(tone={item['tone']}, len_hint={item['len_hint']})\n")
        for t in item["transcript"]:
            role = "Ты" if t["role"] == "user" else "Помни"
            dialogs_md.append(f"**{role}:** {t['content']}\n")
        dialogs_md.append("\n")

        m = item["metrics"]
        rows.append([
            item["name"],
            item["tone"],
            item["len_hint"],
            item["turns_count"],
            m["pct_end_with_question"],
            m["pct_one_question_ok"],
            m["avg_reply_chars"],
            m["num_too_long_replies(>900ch)"],
        ])

        if args.delay > 0:
            await asyncio.sleep(args.delay)

    # save files
    (out_dir / "dialogs.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dialogs.md").write_text("\n".join(dialogs_md), encoding="utf-8")

    with (out_dir / "report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "scenario", "tone", "len_hint", "turns",
            "pct_end_with_question", "pct_one_question_ok",
            "avg_reply_chars", "num_too_long_replies(>900ch)"
        ])
        w.writerows(rows)

    print(f"[stress] done. Files in: {out_dir}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
