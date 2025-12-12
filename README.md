# ReflectAI

## Runtime
- Python 3.12.6 (see `runtime.txt` for Render)
- Render build must show Python 3.12.6 in logs, иначе падает asyncpg

## Smoke checks
- LLM/Qdrant code expects `pip install -r requirements.txt`
- Quick import smoke: `python3 scripts/smoke_imports.py`
