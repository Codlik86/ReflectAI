# -*- coding: utf-8 -*-
"""
Прогон автотестов диалога в режиме «Поговорить».
— Вызывает app.llm_adapter.chat_with_style с SYSTEM_PROMPT (+ тоновый суффикс)
— Сохраняет Markdown, JSON и простой отчёт-валидацию.
Запуск: python -m scripts.eval_talk
Опции:  --style default|friend|therapist|18plus  --temp 0.82  --max_completion_tokens 480
"""

import os
import json
import csv
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# === используем твои модули ===
from app.prompts import SYSTEM_PROMPT, STYLE_SUFFIXES
from app.llm_adapter import chat_with_style

# -------- Сценарии (10 разных, по 7–10 ходов) --------
SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "sadness_open",
        "title": "Грусть и прояснение причины",
        "turns": [
            "мне грустно",
            "мне кажется, я случайно узнал, что мне изменяет муж",
            "я одновременно злюсь и как будто проваливаюсь внутрь себя",
            "ругаю себя за реакцию — будто я слабая",
            "не знаю, говорить ли ему об этом прямо или сделать вид, что не заметила",
            "еще боюсь остаться одна, если подниму разговор",
            "что мне сделать сегодня, чтобы не развалиться?",
            "если попробую написать ему пару строк — с чего начать?",
            "и как себе напомнить о границах, когда эмоции накрывают?"
        ],
    },
    {
        "id": "anxiety_work",
        "title": "Тревога и дедлайны",
        "turns": [
            "у меня трясутся руки от дедлайнов",
            "кажется, всё провалю и меня уволят",
            "сердце колотится, не могу сосредоточиться",
            "я не понимаю, с чего начать — слишком много задач",
            "можно план на 10–15 минут прямо сейчас?",
            "как сказать руководителю, что мне нужно немного времени?",
            "что сделать к концу дня, чтобы почувствовать прогресс?",
            "как не сорваться вечером и не винить себя?",
            "и что проверить завтра утром первым делом?"
        ],
    },
    {
        "id": "burnout",
        "title": "Выгорание и бессонница",
        "turns": [
            "я не могу заставить себя работать",
            "ночью почти не сплю и просто лежу",
            "утром чувствую вину и пустоту",
            "даже приятные вещи не радуют",
            "не хочу советов, просто побудь рядом",
            "если маленький шаг — какой самый щадящий на сегодня?",
            "как отметить его так, чтобы не обесценить?",
            "что сказать себе вечером, если снова не получится?",
            "как разговаривать с близкими, если сил мало?"
        ],
    },
    {
        "id": "conflict_friend",
        "title": "Ссора с близким другом",
        "turns": [
            "поругалась с лучшим другом",
            "кажется, я обесценила то, что он делает для меня",
            "он обиделся и перестал отвечать",
            "мне стыдно, но я тоже злюсь",
            "не знаю, как начать разговор, чтобы не усугубить",
            "лучше писать или звонить?",
            "помоги с первой фразой — что-то простое и теплое",
            "как показать, что я слушаю, а не оправдываюсь?",
            "и как договориться о границах на будущее?"
        ],
    },
    {
        "id": "decision_two_offers",
        "title": "Выбор между двумя предложениями",
        "turns": [
            "мне предложили две работы",
            "первая — деньги и стабильность, вторая — смысл и классная команда",
            "первая далеко ехать, вторая — рядом",
            "меня пугает риск во второй",
            "дай короткую структуру, как решить",
            "какие 3 критерия точно мои, а не чужие?",
            "что проверить у менеджеров, чтобы снизить риск?",
            "как поставить срок решения и не мучить себя?",
            "и что я скажу себе, если выберу неидеально?"
        ],
    },
    {
        "id": "procrastination_loop",
        "title": "Прокрастинация и стыд",
        "turns": [
            "снова откладываю диплом",
            "столько раз обещала себе начать и не начала",
            "кажется, чем больше тяну, тем стыднее",
            "я боюсь открыть документ и увидеть пустоту",
            "можем придумать маленький эксперимент на сегодня?",
            "давай задание на 10 минут без перфекционизма",
            "как я отмечу факт, что сделала хоть что-то?",
            "что делать, если сорвусь завтра?",
            "и как аккуратно рассказать научруку о прогрессе?"
        ],
    },
    {
        "id": "social_anxiety",
        "title": "Социальная тревожность",
        "turns": [
            "не могу позвонить по делу, стыдно и страшно",
            "когда беру трубку — замираю",
            "в голове крутится, что со мной будут резко говорить",
            "могу ли потренироваться перед звонком?",
            "давай мини-скрипт на 3–4 фразы",
            "а если собеседник перебьёт — что ответить?",
            "как подготовить себя за 2 минуты до звонка?",
            "что сделать сразу после, чтобы закрепить успех?",
            "и как поступить, если всё же не получилось?"
        ],
    },
    {
        "id": "relationship_boundaries",
        "title": "Границы в отношениях",
        "turns": [
            "партнер шутит надо мной при друзьях",
            "говорила, что неприятно — отвечает, что я слишком чувствительная",
            "мне хочется спокойно обозначить границы",
            "боюсь сцены перед другими",
            "помоги сформулировать «я»-сообщение",
            "как выбрать момент и тон?",
            "что если он снова отшутится?",
            "какие последствия я готова обозначить мягко?",
            "и как поддержать себя после разговора?"
        ],
    },
    {
        "id": "health_worry",
        "title": "Тревога о здоровье (без медицины)",
        "turns": [
            "меня пугают симптомы, но к врачу пока не хочу",
            "я понимаю, что ты не врач — просто помоги успокоиться",
            "можем сделать короткую практику на 1–2 минуты?",
            "что потом заметить в теле, чтобы понять, стало легче?",
            "как не загуглить симптомы и не накрутить себя?",
            "что записать в дневник тревоги сегодня?",
            "когда стоит все-таки обратиться к специалисту?",
            "как попросить близкого побыть рядом без советов?",
            "и как себя поддержать, если страх вернется ночью?"
        ],
    },
    {
        "id": "loss_grief",
        "title": "Потеря и горе",
        "turns": [
            "умер близкий человек",
            "я не хочу говорить с родными, будто защищаю их",
            "можешь просто быть рядом и задать один бережный вопрос?",
            "мне тяжело вспоминать, но и забывать страшно",
            "что можно сделать вечером, когда накрывает сильнее всего?",
            "как создать маленький ритуал памяти для себя?",
            "с кем безопасно разделить это, кроме семьи?",
            "какой один добрый шаг к себе я могу сделать завтра?",
            "и как признать, что горе приходит волнами — это нормально?"
        ],
    },
]

# --- простые правила проверки (хитрые метрики специально не усложняю) ---
BANNED_STARTS = [
    "понимаю", "к сожалению", "это сложная тема", "важно", "попробуй", "помни",
    "давай", "иногда", "часто", "бывает", "знаешь",
]

def _starts_with_banned(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(t.startswith(b) for b in BANNED_STARTS)

def _has_question(text: str) -> bool:
    return "?" in (text or "")

def _echo_ratio(user: str, bot: str) -> float:
    """Примитив: доля перекрытия слов (для проверки, что бот не повторяет дословно)."""
    import re
    W = lambda s: set(re.findall(r"[а-яa-z0-9]{3,}", (s or "").lower()))
    u, b = W(user), W(bot)
    if not u:
        return 0.0
    return len(u & b) / max(1, len(u))

def _mentions_topic(user: str, bot: str) -> bool:
    """Есть ли хоть одно ключевое слово пользователя в ответе бота (очень грубо)."""
    import re
    u = set(re.findall(r"[а-яa-z0-9]{4,}", (user or "").lower()))
    b = set(re.findall(r"[а-яa-z0-9]{4,}", (bot or "").lower()))
    return len(u & b) > 0

# --- движок одного сценария ---
async def run_scenario(
    scenario: Dict[str, Any],
    *,
    system_prompt: str,
    style_suffix: str,
    temperature: float,
    max_completion_tokens: int,
) -> Dict[str, Any]:
    """Ведём диалог по заданным turns, аккумулируем history."""
    history: List[Dict[str, str]] = [{"role": "system", "content": system_prompt + (("\n\n" + style_suffix) if style_suffix else "")}]
    records: List[Dict[str, str]] = []

    for i, user_text in enumerate(scenario["turns"]):
        # user -> history
        history.append({"role": "user", "content": user_text})

        # LLM ответ
        reply = await chat_with_style(
            messages=history,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
        )
        reply = (reply or "").strip()
        history.append({"role": "assistant", "content": reply})

        # простая проверка качества на каждом шаге
        check = {
            "banned_start": _starts_with_banned(reply),
            "has_question": _has_question(reply),
            "echo_ratio": round(_echo_ratio(user_text, reply), 3),
            "mentions_topic": _mentions_topic(user_text, reply),
            "len_chars": len(reply),
        }

        records.append({
            "turn_index": i,
            "user": user_text,
            "assistant": reply,
            "checks": check,
        })

    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "records": records,
        "history": history,
    }

# --- сохранение результатов ---
def save_outputs(root: Path, dialogs: List[Dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    # JSON
    with (root / "dialogs.json").open("w", encoding="utf-8") as f:
        json.dump(dialogs, f, ensure_ascii=False, indent=2)

    # Markdown
    md_lines = ["# Talk eval\n"]
    for d in dialogs:
        md_lines.append(f"## {d['title']} ({d['id']})")
        for r in d["records"]:
            md_lines.append(f"**U:** {r['user']}")
            md_lines.append(f"**A:** {r['assistant']}\n")
        md_lines.append("---")
    (root / "dialogs.md").write_text("\n".join(md_lines), encoding="utf-8")

    # CSV отчёт (по шагам)
    with (root / "report.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["scenario_id", "turn", "banned_start", "has_question", "echo_ratio", "mentions_topic", "len_chars"])
        for d in dialogs:
            for r in d["records"]:
                c = r["checks"]
                w.writerow([
                    d["id"], r["turn_index"], int(c["banned_start"]),
                    int(c["has_question"]), c["echo_ratio"], int(c["mentions_topic"]),
                    c["len_chars"],
                ])

# --- CLI / main ---
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", default="default", choices=["default", "friend", "therapist", "18plus"])
    parser.add_argument("--temp", type=float, default=0.82)
    parser.add_argument("--max_completion_tokens", type=int, default=480)
    args = parser.parse_args()

    # системный промпт + суффикс
    style_suffix = STYLE_SUFFIXES.get(args.style, "")
    system_prompt = SYSTEM_PROMPT

    # куда складывать
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path("runs") / f"talk_eval_{stamp}_{args.style}"

    dialogs: List[Dict[str, Any]] = []
    for sc in SCENARIOS:
        d = await run_scenario(
            sc,
            system_prompt=system_prompt,
            style_suffix=style_suffix,
            temperature=args.temp,
            max_completion_tokens=args.max_completion_tokens,
        )
        dialogs.append(d)

    save_outputs(out_dir, dialogs)

    print(f"Готово ✅")
    print(f"Markdown: {out_dir / 'dialogs.md'}")
    print(f"JSON:     {out_dir / 'dialogs.json'}")
    print(f"Report:   {out_dir / 'report.csv'}")
    print("Подсказка: смотри banned_start=0, echo_ratio<0.4, mentions_topic=1, и чтобы периодически был целевой вопрос.")

if __name__ == "__main__":
    asyncio.run(main())
