from __future__ import annotations
import re, sys, pathlib

def normalize(text: str) -> str:
    t = text
    # убрать BOM/невидимые
    t = t.replace("\ufeff", "")
    # склеить переносы по дефисам в конце строки: "психо-\nтерапия" -> "психотерапия"
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    # иногда в PDF слово разрезано пробелом перед переносом: "психо \nтерапия" -> "психотерапия"
    t = re.sub(r"(\S)\s*\n\s*(\S)", lambda m: m.group(1)+" "+m.group(2) if (len(m.group(1))>1 and len(m.group(2))>1 and m.group(1)[-1].isalnum() and m.group(2)[0].isalnum()) else m.group(0), t)
    # нормализуем множественные пустые строки
    t = re.sub(r"\n{3,}", "\n\n", t)
    # убираем номера страниц в одиночных строках (цифры посреди пустот)
    t = re.sub(r"\n\s*\d{1,4}\s*\n", "\n", t)
    # убираем повторяющиеся пробелы
    t = re.sub(r"[ \t]{2,}", " ", t)
    # приводим «красивые» кавычки и тире
    t = t.replace("—", "-").replace("–", "-").replace("«", "\"").replace("»", "\"")
    # трим
    t = t.strip()+"\n"
    return t

def main(paths: list[str]) -> None:
    out_dir = pathlib.Path("data/corpus/_normalized")
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in paths:
        pth = pathlib.Path(p)
        if not pth.exists():
            print(f"! not found: {p}", file=sys.stderr); continue
        txt = pth.read_text(encoding="utf-8", errors="ignore")
        norm = normalize(txt)
        out = out_dir / pth.with_suffix(".txt").name
        out.write_text(norm, encoding="utf-8")
        print(f"✓ normalized: {out}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/normalize_text.py <file1.txt> [file2.txt ...]", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1:])
