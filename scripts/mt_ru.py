import os, sys, pathlib
from typing import List
from openai import OpenAI

BASE_URL = os.getenv("OPENAI_BASE_URL", "")
API_KEY  = os.getenv("OPENAI_API_KEY", "")
MODEL    = os.getenv("GPT_MODEL", "gpt-5.2")

if not API_KEY:
    sys.exit("OPENAI_API_KEY is empty")
if not BASE_URL:
    sys.exit("OPENAI_BASE_URL is empty")

cli = OpenAI(base_url=BASE_URL, api_key=API_KEY)

SYS_PROMPT = (
    "Ты профессиональный переводчик психол. и мед. текстов. "
    "Переводи на русский максимально точно и нейтрально, без добавлений. "
    "Сохраняй структуру: заголовки (#, ##), списки (-, •, 1.), цитаты. "
    "Термины КПТ/АСТ переводить общепринято (CBT=КПТ, exposure=экспозиция и т.п.). "
    "Никаких вступлений/дисклеймеров — только переведённый текст."
)

def split_header_body(text: str):
    return text.split("\n\n", 1) if "\n\n" in text else (text, "")

def set_lang_ru(head: str) -> str:
    lines = head.splitlines()
    out, saw = [], False
    for ln in lines:
        if ln.lower().startswith("# lang:"):
            out.append("# lang: ru"); saw = True
        else:
            out.append(ln)
    if not saw:
        out.append("# lang: ru")
    return "\n".join(out)

def chunk(body: str, limit: int = 9000) -> List[str]:
    # грубо по абзацам, чтобы не упираться в лимиты
    parts, cur, cur_len = [], [], 0
    for para in body.split("\n\n"):
        p = para.strip()
        add = len(p) + 2
        if cur and cur_len + add > limit:
            parts.append("\n\n".join(cur).strip()); cur, cur_len = [], 0
        cur.append(p); cur_len += add
    if cur: parts.append("\n\n".join(cur).strip())
    return parts or [""]

def translate_chunk(txt: str) -> str:
    r = cli.chat.completions.create(
        model=MODEL, temperature=0.1,
        messages=[{"role":"system","content":SYS_PROMPT},
                  {"role":"user","content":txt[:12000]}]
    )
    return (r.choices[0].message.content or "").strip()

def translate_file(path: pathlib.Path) -> pathlib.Path:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    head, body = split_header_body(raw)
    head_ru = set_lang_ru(head)
    chunks = chunk(body, limit=9000)
    out_parts = []
    for i, ch in enumerate(chunks, 1):
        if ch.strip():
            out_parts.append(translate_chunk(ch))
        else:
            out_parts.append("")
    out_txt = head_ru + "\n\n" + "\n\n".join(out_parts).strip() + "\n"
    out = path.with_name(path.stem.replace("_en","_ru") + ".txt")
    out.write_text(out_txt, encoding="utf-8")
    return out

def main():
    src_dir = pathlib.Path("data/corpus")
    en_files = sorted(src_dir.glob("*_en.txt"))
    if not en_files:
        print("Нет *_en.txt — перевод не требуется."); return
    print(f"Найдено EN файлов: {len(en_files)}")
    for f in en_files:
        print("→ Перевод:", f.name, flush=True)
        try:
            out = translate_file(f)
            print("  ✓", out.name, flush=True)
        except Exception as e:
            print("  ✗ ошибка:", e, flush=True)
            raise
    # переносим оригиналы EN, чтобы не индексировались
    dst = pathlib.Path("data/corpus_en"); dst.mkdir(parents=True, exist_ok=True)
    for f in en_files:
        if f.exists():
            f.replace(dst / f.name)
            print("  ↪ перемещён EN →", dst / f.name)

if __name__ == "__main__":
    main()
