import os, time, random, requests, re, html
from bs4 import BeautifulSoup
from pathlib import Path

UA = os.getenv("FETCH_UA", "ReflectAI/1.0 (+https://reflect.local)")

def fetch_html(url: str, tries: int = 6) -> str:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.7"}, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 503):
                ra = r.headers.get("Retry-After")
                wait = int(ra) if (ra and ra.isdigit()) else (2 ** i)
                time.sleep(wait + random.random()); continue
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)
        time.sleep((2 ** i) + random.random())
    raise RuntimeError(f"fetch failed: {url} ({last})")

def html_to_text(doc: str) -> str:
    soup = BeautifulSoup(doc, "html.parser")
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s{2,}", " ", text)
    return html.unescape(text.strip())

def save(path: str, content: str) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

def try_fetch(url: str, out: str, label: str):
    try:
        print("↓", label, url)
        doc = fetch_html(url)
        txt = html_to_text(doc)
        save(out, txt)
        print("✓ HTML → TXT:", out, len(txt))
        return True
    except Exception as e:
        print(f"! skip {url}: {e}")
        return False

def main():
    targets = [
        # 1) NHS CBT overview
        ("https://www.nhs.uk/mental-health/talking-therapies-medicine-treatments/talking-therapies-and-counselling/cognitive-behavioural-therapy-cbt/",
         "data/corpus/_incoming/nhs_cbt_overview_en.txt",
         "NHS: CBT overview"),
        # 2) NHS Every Mind Matters — self-help CBT
        ("https://www.nhs.uk/every-mind-matters/mental-wellbeing-tips/self-help-cbt-techniques/",
         "data/corpus/_incoming/nhs_self_help_cbt_en.txt",
         "NHS: self-help CBT"),
        # 3) CNTW NHS — Social anxiety (print)
        ("https://selfhelp.cntw.nhs.uk/self-help-guides/social-anxiety/print/399",
         "data/corpus/_incoming/nhs_cntw_social_anxiety_en.txt",
         "NHS CNTW: social anxiety (print)"),
        # 4) NHS — Panic disorder/panic attacks (может меняться путь; если 404 — просто пропустим)
        ("https://www.nhs.uk/mental-health/conditions/panic-disorder/",
         "data/corpus/_incoming/nhs_panic_disorder_en.txt",
         "NHS: panic disorder"),
        # 5) NHS — Generalised anxiety (путь может отличаться, есть fallback)
        ("https://www.nhs.uk/mental-health/conditions/generalised-anxiety-disorder-gad/",
         "data/corpus/_incoming/nhs_gad_en.txt",
         "NHS: GAD"),
    ]
    ok = 0
    for url, out, label in targets:
        ok += 1 if try_fetch(url, out, label) else 0
    print(f"done: {ok}/{len(targets)} fetched")

if __name__ == "__main__":
    main()
