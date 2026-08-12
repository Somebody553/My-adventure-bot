import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
load_dotenv()
proxy_url="socks5://eu8buz:zEhk8F@168.80.73.57:8000"
session = AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()
TOKEN=os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, session=session)
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