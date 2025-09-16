#!/usr/bin/env bash
set -euo pipefail
if ! command -v pdftotext >/dev/null 2>&1; then
  echo "pdftotext не найден. Установи poppler: brew install poppler" >&2
  exit 1
fi
in="$1"
out="${2:-data/corpus/_incoming/$(basename "${in%.*}").txt}"
mkdir -p "$(dirname "$out")"
pdftotext -layout -enc UTF-8 "$in" "$out"
echo "✓ PDF → TXT: $out"
