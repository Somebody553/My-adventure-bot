import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
# Импортируем синхронный клиент и необходимые модели
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from dotenv import load_dotenv
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from pathlib import Path
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
env_path = BASE_DIR / ".env"

values = dotenv_values(env_path)
load_dotenv()
# --- КОНФИГУРАЦИЯ ---
TOKEN = values.get("BOT_TOKEN") or values.get("TOKEN")
GIGACHAT_CREDENTIALS = values.get("GIGACHAT_CREDENTIALS")
proxy_url="socks5://eu8buz:zEhk8F@168.80.73.57:8000"

# --- ИНИЦИАЛИЗАЦИЯ ---
logging.basicConfig(level=logging.INFO)

session = AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# Инициализируем синхронный клиент GigaChat
giga = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    scope="GIGACHAT_API_PERS",  
    verify_ssl_certs=False,     
)

# Хранилище истории диалогов
user_history = {}

# --- СИНХРОННАЯ ОБЕРТКА (Ключевое исправление) ---
def call_gigachat_sync(history_list: list):
    """
    Эта функция выполняется в отдельном потоке.
    Мы явно создаем объект Chat, чтобы удовлетворить валидацию Pydantic,
    и передаем его позиционно, избегая ошибок с именованными аргументами.
    """
    chat_request = Chat(messages=history_list)
    return giga.chat(chat_request)

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_history[user_id] = []
    await message.answer(
        "Привет! 👋 Я бот на базе GigaChat.\n"
        "Напиши мне любой текст. Используй /reset, чтобы очистить историю."
    )

@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    user_id = message.from_user.id
    user_history[user_id] = []
    await message.answer("История диалога очищена. Начинаем сначала!")

@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in user_history:
        user_history[user_id] = []

    # Добавляем сообщение пользователя в историю
    user_history[user_id].append(
        Messages(role=MessagesRole.USER, content=message.text)
    )

    # Показываем "печатает..." в Telegram
    await bot.send_chat_action(message.chat.id, "typing")

    try:
        # Вызываем нашу обертку в отдельном потоке
        response = await asyncio.to_thread(call_gigachat_sync, user_history[user_id])
        
        assistant_reply = response.choices[0].message.content

        # Добавляем ответ нейросети в историю
        user_history[user_id].append(
            Messages(role=MessagesRole.ASSISTANT, content=assistant_reply)
        )

        await message.answer(assistant_reply)

    except Exception as e:
        logging.error(f"GigaChat API error: {e}")
        await message.answer("Произошла ошибка при обращении к нейросети. Попробуйте позже.")
        # Удаляем сообщение, которое вызвало ошибку, чтобы не ломать историю
        user_history[user_id].pop()

# --- ЗАПУСК ---

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")