import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart
from dotenv import load_dotenv
load_dotenv() # подтягивает переменные из .env
bot = Bot(os.getenv("BOT_TOKEN"))
dp = Dispatcher()
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("Привет! Пока я просто повторяю. Напиши что-нибудь.")
@dp.message()
async def echo(message: Message):
    await message.answer(message.text or "Это было не текстовое сообщение :)")
async def main():
    await dp.start_polling(bot)
if __name__ == "__main__":
    asyncio.run(main())
