#!/usr/bin/env bash
set -euo pipefail
# usage: scripts/pdf2txt.sh <pdf_path_or_url> [out_txt]

in="$1"
out="${2:-}"
incoming="data/corpus/_incoming"
mkdir -p "$incoming"

UA="${FETCH_UA:-ReflectAI/1.0 (+https://reflect.local)}"

# URL -> download with retries
if [[ "$in" =~ ^https?:// ]]; then
  fname="$incoming/$(basename "${in%%\?*}")"
  if [[ ! -s "$fname" ]]; then
    echo "↓ downloading: $in"
    attempt=1
    while :; do
      if curl -L --fail --connect-timeout 20 -H "User-Agent: $UA" -o "$fname.tmp" "$in"; then
        mv "$fname.tmp" "$fname"
        break
      fi
      attempt=$((attempt+1))
      if [[ $attempt -gt 6 ]]; then
        echo "✗ failed to download: $in" >&2
        exit 1
      fi
      sleep $(( 2 ** attempt ))
    done
  else
    echo "• exists: $fname"
  fi
  in="$fname"
fi

# output path
if [[ -z "${out}" ]]; then
  base="$(basename "${in%.*}")"
  out="$incoming/${base}.txt"
fi

# convert
if command -v pdftotext >/dev/null 2>&1; then
  pdftotext -layout -enc UTF-8 "$in" "$out"
else
  python - "$in" "$out" <<'PY'
import sys
from pdfminer.high_level import extract_text
inp, out = sys.argv[1], sys.argv[2]
txt = extract_text(inp) or ""
open(out, "w", encoding="utf-8").write(txt)
PY
fi

echo "✓ PDF → TXT: $out"
