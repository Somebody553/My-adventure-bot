import asyncio
import os

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, 
    ReplyKeyboardMarkup, 
    KeyboardButton,
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

# Загружаем переменные окружения в самом начале
load_dotenv()

# --- Настройка прокси (ВАЖНО: нужен пакет aiohttp-socks для SOCKS5) ---
PROXY_URL = "socks5://eu8buz:zEhk8F@168.80.73.57:8000"
session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession()

# --- Инициализация бота и диспетчера ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, session=session)

# Хранилище для FSM (в памяти; в продакшене лучше Redis)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Создаем роутер для обработчиков ---
router = Router()


# ===== Функции-генераторы клавиатур =====

def get_reply_keyboard() -> ReplyKeyboardMarkup:
    """Главная reply-клавиатура под строкой ввода"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Меню"), KeyboardButton(text="🧹 Очистить")],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )


def get_inline_menu() -> InlineKeyboardMarkup:
    """Inline-меню (кнопки прикрепляются к сообщению)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
            [InlineKeyboardButton(text="🧹 Очистить диалог", callback_data="clear")]
        ]
    )


# ===== Обработчики команд =====

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Сбрасываем прошлое состояние при старте
    await state.clear()
    
    await message.answer(
        "👋 <b>Привет!</b> Я эхо-бот с функциями.\n\n"
        "Напиши что-нибудь, и я повторю. Используй кнопки ниже 👇",
        reply_markup=get_reply_keyboard()
    )


@router.message(Command("clear"))
async def cmd_clear(message: Message, state: FSMContext):
    """Очистка памяти бота по команде /clear"""
    await state.clear()
    await message.answer("🧹 Диалог очищен! Начинаем с чистого листа.")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Список команд:</b>\n"
        "/start - Перезапустить бота\n"
        "/clear - Очистить диалог\n"
        "/menu  - Показать inline-меню\n"
        "/help  - Показать эту справку"
    )


# ===== Обработчики reply-кнопок =====

@router.message(F.text == "📋 Меню")
async def show_menu(message: Message):
    await message.answer(
        "Выберите действие:",
        reply_markup=get_inline_menu()
    )


@router.message(F.text == "🧹 Очистить")
async def button_clear(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🧹 Диалог очищен!")


@router.message(F.text == "ℹ️ Помощь")
async def button_help(message: Message):
    await message.answer("Просто пиши мне сообщения — я их повторю 😉")


# ===== Обработчики inline-кнопок (callback) =====

@router.callback_query(F.data == "profile")
async def process_profile(callback: CallbackQuery):
    await callback.message.answer(f"👤 Ваш ID: <code>{callback.from_user.id}</code>")
    await callback.answer()  # Убираем "часики"


@router.callback_query(F.data == "settings")
async def process_settings(callback: CallbackQuery):
    await callback.message.answer("⚙️ Здесь будут настройки...")
    await callback.answer("Раздел в разработке!", show_alert=True)


@router.callback_query(F.data == "clear")
async def process_clear_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🧹 Диалог очищен через inline-меню!")
    await callback.answer()


# ===== Эхо-обработчик (ловит всё остальное) =====

@router.message()
async def echo(message: Message):
    if message.text:
        await message.answer(f"🔁 Эхо: {message.text}")
    else:
        await message.answer("Это было не текстовое сообщение 🙂")


# ===== Запуск =====

async def main():
    # Регистрируем роутер в диспетчере
    dp.include_router(router)
    
    # Запускаем long-polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")