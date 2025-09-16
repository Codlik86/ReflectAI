Быстрый сценарий:
1) Помести PDF в data/corpus/_incoming или .txt сразу туда.
2) PDF → TXT: scripts/pdf2txt.sh path/to/file.pdf
3) Нормализуй: python scripts/normalize_text.py data/corpus/_incoming/*.txt
4) Открой любой получившийся TXT в data/corpus/_normalized/, вставь в начало шапку из scripts/new_header.txt и заполни поля.
5) Перекинь итоговый TXT в data/corpus/ (корень). Имя файла — короткое латиницей (например, who_pm_plus_ru.txt).
6) Переиндексируй: 
   QDRANT_COLLECTION="reflectai_corpus_v2" python -m scripts.ingest_qdrant --recreate --pattern "data/corpus/*.txt"
7) Проверка: python -m scripts.rag_selfcheck
