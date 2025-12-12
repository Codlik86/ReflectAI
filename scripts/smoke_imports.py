"""
Quick smoke import check for Render/CI.
Run: python3 scripts/smoke_imports.py
"""

def main():
    modules = [
        "fastapi",
        "aiogram",
        "qdrant_client",
        "yookassa",
        "sqlalchemy",
        "psycopg",
        "asyncpg",
    ]
    failed = []
    for m in modules:
        try:
            __import__(m)
        except Exception as e:
            failed.append((m, repr(e)))
    if failed:
        print("FAIL", failed)
        raise SystemExit(1)
    print("OK")


if __name__ == "__main__":
    main()
