import asyncio
import logging
import json
import re
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.utils.keyboard import InlineKeyboardBuilder

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

session = AiohttpSession(proxy=proxy_url)
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

giga = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    scope="GIGACHAT_API_PERS",  
    verify_ssl_certs=False,     
)

user_history = {}
user_stats = {}

# --- КЛАСС ДЛЯ ХРАНЕНИЯ СТАТОВ ---
@dataclass
class PlayerStats:
    strength: int = 10
    endurance: int = 10
    motivation: int = 50
    budget: int = 1000

    def to_prompt_str(self):
        return f"[Текущие статы героя: strength={self.strength}, endurance={self.endurance}, motivation={self.motivation}, budget={self.budget}]"

# --- ПРОМПТ ПЕРСОНАЖА (УЖЕСТOЧЕННЫЙ) ---
CHARACTER_PROFILE = """
Ты — Дед Михалыч, владелец и главный тренер легендарного зала "Железный Подвал". 
Тебе 65 лет, ты суровый, ворчливый, но справедливый. Обращайся на "ты" ("сынок", "боец").
Фразы короткие, рубленые. "Железо не врет", "Не ной, работай".

Правила реакции на статы:
- Если motivation < 30: ругай, но давай легкий поддерживающий пинок.
- Если budget мал: предлагай бесплатные варианты ("Зал сам себя не уберет, бери тряпку").

ТЕХНИЧЕСКОЕ ПРАВИЛО (АБСОЛЮТНОЕ):
Ты ОБЯЗАН отвечать СТРОГО в формате JSON в КАЖДОМ сообщении. Никакого текста вне фигурных скобок.
Формат: 
{
  "text": "Твой ответ от лица Деда Михалыча", 
  "buttons": [
    {"text": "Вариант действия 1", "callback": "action_1"},
    {"text": "Вариант действия 2", "callback": "action_2"}
  ], 
  "stats_change": {"motivation": -5}
}
Массив "buttons" ДОЛЖЕН содержать от 2 до 3 вариантов действия для продолжения игры. 
Используй для stats_change ТОЛЬКО ключи: strength, endurance, motivation, budget.
"""

SYSTEM_PROMPT = (
    "Ты гейм-мастер текстовой RPG. Всегда отыгрывай роль:\n" + CHARACTER_PROFILE
)

# --- СИНХРОННАЯ ОБЕРТКА ---
def call_gigachat_sync(history_list: list):
    chat_request = Chat(messages=history_list)
    return giga.chat(chat_request)

# --- ПАРСИНГ ОТВЕТА ---
def parse_ai_response(raw_text: str):
    try:
        clean_text = raw_text.strip()
        clean_text = re.sub(r'^```json\s*', '', clean_text)
        clean_text = re.sub(r'\s*```$', '', clean_text)
        
        data = json.loads(clean_text)
        if isinstance(data, dict) and "text" in data:
            return data["text"], data.get("buttons", []), data.get("stats_change", {})
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Если ИИ сломал JSON, возвращаем пустые списки (их перехватит страховка ниже)
    return raw_text, [], {}

# --- ЕДИНЫЙ ОБРАБОТЧИК ХОДА ---
async def process_turn(update_obj, user_input_text, message_obj):
    user_id = update_obj.from_user.id
    
    if user_id not in user_history:
        user_history[user_id] = []
        user_stats[user_id] = PlayerStats()

    current_stats = user_stats[user_id].to_prompt_str()
    full_system_prompt = f"{SYSTEM_PROMPT}\n\n{current_stats}"
    
    # SYSTEM всегда строго первый!
    history_to_send = [
        Messages(role=MessagesRole.SYSTEM, content=full_system_prompt)
    ] + user_history[user_id] + [
        Messages(role=MessagesRole.USER, content=user_input_text)
    ]

    await bot.send_chat_action(message_obj.chat.id, "typing")

    try:
        response = await asyncio.to_thread(call_gigachat_sync, history_to_send)
        raw_ai_reply = response.choices[0].message.content

        reply_text, buttons_data, stats_change = parse_ai_response(raw_ai_reply)

        # Применяем изменения статов
        if stats_change:
            for stat, value in stats_change.items():
                if hasattr(user_stats[user_id], stat):
                    new_value = getattr(user_stats[user_id], stat) + value
                    setattr(user_stats[user_id], stat, max(0, min(100, new_value)))
            
            if user_stats[user_id].motivation <= 0:
                reply_text = "💀 Мотивация на нуле! Вы ушли есть шаверму. Игра окончена. (Используйте /reset)"
                buttons_data = []

        if "GAME_OVER" not in reply_text and user_stats[user_id].motivation > 0:
            user_history[user_id].append(Messages(role=MessagesRole.USER, content=user_input_text))
            user_history[user_id].append(Messages(role=MessagesRole.ASSISTANT, content=reply_text))

        # 🛡️ СТРАХОВКА: Если ИИ не вернул кнопки, создаем их принудительно
        if not buttons_data and user_stats[user_id].motivation > 0:
            logging.warning("ИИ не вернул кнопки, применяем fallback-клавиатуру")
            buttons_data = [
                {"text": "🔄 Попробовать другое действие", "callback": "fallback_retry"},
                {"text": "📊 Посмотреть мои статы", "callback": "show_stats"},
                {"text": "🆘 Позвать Деда Михалыча на помощь", "callback": "fallback_help"}
            ]

        # Строим клавиатуру
        keyboard = None
        if buttons_data and user_stats[user_id].motivation > 0:
            builder = InlineKeyboardBuilder()
            for btn in buttons_data:
                safe_callback = str(btn.get("callback", "btn"))[:64]
                builder.button(text=btn["text"], callback_data=safe_callback)
            builder.adjust(1)
            
            # Всегда добавляем кнопку статов внизу
            builder.button(text="📊 Мои статы", callback_data="show_stats")
            keyboard = builder.as_markup()

        await message_obj.answer(reply_text, reply_markup=keyboard)

    except Exception as e:
        logging.error(f"GigaChat API error: {e}")
        await message_obj.answer("Произошла ошибка. Попробуйте еще раз.")
        if len(user_history[user_id]) >= 2:
            user_history[user_id].pop()
            user_history[user_id].pop()

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_history[user_id] = []
    user_stats[user_id] = PlayerStats()
    
    # 🎯 КНОПКИ УЖЕ В ПЕРВОМ СООБЩЕНИИ
    builder = InlineKeyboardBuilder()
    builder.button(text="💪 Осмотреться в зале", callback_data="look_around")
    builder.button(text="🗣️ Громко позвать тренера", callback_data="call_trainer")
    builder.adjust(1)
    
    await message.answer(
        "🚪 Вы толкаете тяжелую металлическую дверь. Изнутри доносится "
        "звук падающих блинов и тяжелый бас: 'Ещё два раза, давай!'.\n\n"
        "Это 'Железный Подвал'. Ваше приключение начинается.",
        reply_markup=builder.as_markup()
    )

@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    user_id = message.from_user.id
    user_history[user_id] = []
    user_stats[user_id] = PlayerStats()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Войти в зал заново", callback_data="look_around")
    
    await message.answer("🔄 История и статы сброшены. Начинаем сначала, боец!", reply_markup=builder.as_markup())

@dp.message(F.text)
async def handle_text(message: types.Message):
    await process_turn(message, message.text, message)

@dp.callback_query(F.data)
async def handle_button_click(callback: types.CallbackQuery):
    await callback.answer()
    
    # Обработка страховочных кнопок
    if callback.data == "fallback_retry":
        action_text = "[Игрок растерялся и хочет попробовать что-то другое]"
    elif callback.data == "fallback_help":
        action_text = "[Игрок кричит: 'Дед Михалыч, помоги!']"
    else:
        action_text = f"[Игрок выбрал действие: {callback.data}]"
        
    await process_turn(callback, action_text, callback.message)

@dp.callback_query(F.data == "show_stats")
async def show_stats_callback(callback: types.CallbackQuery):
    await callback.answer()
    stats = user_stats.get(callback.from_user.id, PlayerStats())
    
    # После просмотра статов возвращаем возможность действия
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Вернуться к тренировке", callback_data="look_around")
    
    await callback.message.answer(
        f"📊 **Ваши характеристики:**\n"
        f"💪 Сила: {stats.strength}\n"
        f"⚡ Выносливость: {stats.endurance}\n"
        f"🔥 Мотивация: {stats.motivation}\n"
        f"💰 Бюджет: {stats.budget} руб.",
        reply_markup=builder.as_markup()
    )

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")