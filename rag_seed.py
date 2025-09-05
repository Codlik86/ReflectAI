import os
import json
from openai import OpenAI

# --- Настройки ---
API_KEY = os.getenv("OPENAI_API_KEY")  # или вставь ключ прямо строкой
BASE_URL = "https://api.proxyapi.ru/openai/v1"

DATA_DIR = "data/corpus"
OUTPUT_FILE = "embeddings_index.json"

# --- Инициализация клиента с прокси ---
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

def load_corpus(data_dir):
    """Загружаем все тексты из файлов в папке"""
    items = []
    for filename in os.listdir(data_dir):
        path = os.path.join(data_dir, filename)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
                if text:
                    items.append({"filename": filename, "text": text})
    print(f"Found {len(items)} files in {data_dir}")
    return items

def embed_texts(items):
    """Создаем эмбеддинги через OpenAI API с прокси"""
    embeddings = {}
    for item in items:
        try:
            resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=item["text"]
            )
            embeddings[item["filename"]] = resp.data[0].embedding
        except Exception as e:
            print(f"Ошибка при обработке {item['filename']}: {e}")
    return embeddings

def save_embeddings(embeddings, output_file):
    """Сохраняем эмбеддинги в JSON файл"""
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(embeddings, f, ensure_ascii=False, indent=2)
    print(f"Эмбеддинги сохранены в {output_file}")

if __name__ == "__main__":
    if not API_KEY:
        print("Ошибка: API_KEY не установлен. Установите переменную окружения OPENAI_API_KEY")
        exit(1)

    items = load_corpus(DATA_DIR)
    if not items:
        print("Нет файлов для обработки в", DATA_DIR)
        exit(1)

    embeddings = embed_texts(items)
    save_embeddings(embeddings, OUTPUT_FILE)