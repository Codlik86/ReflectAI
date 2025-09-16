import os, time, random, urllib.request, urllib.error, html, re, pathlib, ssl

# В некоторых окружениях удобнее не ругаться на SSL
ssl._create_default_https_context = ssl._create_unverified_context

UA = os.getenv("FETCH_UA", "ReflectAI/1.0 (+https://reflect.local)")

TARGETS = [
  ("data/corpus/_incoming/wiki_cbt_ru.txt",
   "https://ru.wikipedia.org/wiki/%D0%9A%D0%BE%D0%B3%D0%BD%D0%B8%D1%82%D0%B8%D0%B2%D0%BD%D0%BE-%D0%BF%D0%BE%D0%B2%D0%B5%D0%B4%D0%B5%D0%BD%D1%87%D0%B5%D1%81%D0%BA%D0%B0%D1%8F_%D1%82%D0%B5%D1%80%D0%B0%D0%BF%D0%B8%D1%8F?printable=yes"),
  ("data/corpus/_incoming/wiki_act_ru.txt",
   "https://ru.wikipedia.org/wiki/%D0%A2%D0%B5%D1%80%D0%B0%D0%BF%D0%B8%D1%8F_%D0%BF%D1%80%D0%B8%D0%BD%D1%8F%D1%82%D0%B8%D1%8F_%D0%B8_%D0%BE%D1%82%D0%B2%D0%B5%D1%82%D1%81%D1%82%D0%B2%D0%B5%D0%BD%D0%BD%D0%BE%D1%81%D1%82%D0%B8?printable=yes"),
  ("data/corpus/_incoming/wiki_gestalt_ru.txt",
   "https://ru.wikipedia.org/wiki/%D0%93%D0%B5%D1%88%D1%82%D0%B0%D0%BB%D1%8C%D1%82-%D1%82%D0%B5%D1%80%D0%B0%D0%BF%D0%B8%D1%8F?printable=yes"),
]

def fetch(url: str, tries: int = 6) -> bytes:
    """Загрузка с экспоненциальным бэкоффом и учётом Retry-After для 429."""
    for i in range(tries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "ru,en;q=0.7",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 429/503 — подождём чуть дольше, уважаем Retry-After если есть
            if e.code in (429, 503):
                ra = e.headers.get("Retry-After")
                wait = int(ra) if (ra and ra.isdigit()) else (2 ** i)
                time.sleep(wait + random.random())
                continue
            if i == tries - 1:
                raise
        except Exception:
            time.sleep((2 ** i) + random.random())
    raise RuntimeError(f"failed to fetch {url}")

def html_to_text(h: str) -> str:
    # режем скрипты/стили/комментарии
    h = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", "", h)
    # убираем инфобоксы
    h = re.sub(r'(?is)<table class="infobox.*?</table>', '', h)
    # теги -> пробел
    t = re.sub(r"(?s)<[^>]+>", " ", h)
    t = html.unescape(t)
    t = re.sub(r"\s+\[\d+\]", "", t)   # [1], [2] и т.п.
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()

def main() -> None:
    for out, url in TARGETS:
        p = pathlib.Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        print("↓ wiki:", url)
        hb = fetch(url)
        txt = html_to_text(hb.decode("utf-8", "ignore"))
        p.write_text(txt, encoding="utf-8")
        print("✓ HTML → TXT:", out, len(txt))

if __name__ == "__main__":
    main()
