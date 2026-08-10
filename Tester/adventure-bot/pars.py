from pathlib import Path
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
env_path = BASE_DIR / ".env"

print("Папка скрипта:", BASE_DIR)
print("Путь к .env:", env_path)
print(".env существует:", env_path.exists())

values = dotenv_values(env_path)
print("Ключи из .env:", list(values.keys()))

TOKEN = values.get("BOT_TOKEN") or values.get("TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Смотрите вывод выше.")

print(TOKEN)