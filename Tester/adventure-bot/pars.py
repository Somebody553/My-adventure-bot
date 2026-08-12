import asyncio
import logging
import json
import re
import sqlite3
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
MAX_HISTORY_MESSAGES = 20  # Лимит памяти для ИИ
DB_NAME = "game_data.db"   # Файл базы данных

# --- ИНИЦИАЛИЗАЦИЯ ---
logging.basicConfig(level=logging.INFO)

session = AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

giga = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    scope="GIGACHAT_API_PERS",  
    verify_ssl_certs=False,
)

# --- КЛАСС ДЛЯ ХРАНЕНИЯ СТАТОВ ---
@dataclass
class PlayerStats:
    strength: int = 10
    endurance: int = 10
    motivation: int = 50
    budget: int = 1000

    def to_prompt_str(self):
        return f"[Статы: Сила={self.strength}, Выносл={self.endurance}, Мотив={self.motivation}, Бюджет={self.budget}]"

# --- БАЗА ДАННЫХ (SQLITE) ---
def init_db():
    """Создает таблицы, если их нет"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                strength INTEGER DEFAULT 10,
                endurance INTEGER DEFAULT 10,
                motivation INTEGER DEFAULT 50,
                budget INTEGER DEFAULT 1000
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT
            )
        """)
        conn.commit()

def get_user_stats(user_id: int):
    """Получает статы юзера или создает нового"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT strength, endurance, motivation, budget FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return PlayerStats(row["strength"], row["endurance"], row["motivation"], row["budget"])
        else:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return PlayerStats()

def update_user_stats(user_id: int, stats: PlayerStats):
    """Сохраняет обновленные статы"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            UPDATE users SET strength = ?, endurance = ?, motivation = ?, budget = ? WHERE user_id = ?
        """, (stats.strength, stats.endurance, stats.motivation, stats.budget, user_id))
        conn.commit()

def reset_user(user_id: int):
    """Полный сброс прогресса и истории"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        conn.commit()

def get_history(user_id: int, limit: int):
    """Достает последние N сообщений из базы"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("""
            SELECT role, content FROM history 
            WHERE user_id = ? 
            ORDER BY id DESC 
            LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        rows.reverse() # Возвращаем хронологический порядок
        return [Messages(role=MessagesRole(row[0]), content=row[1]) for row in rows]

def append_history(user_id: int, role: str, content: str):
    """Добавляет сообщение в историю базы"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        conn.commit()

# --- ПРОМПТ И ЛОГИКА ИИ ---
CHARACTER_PROFILE = """
Ты — Дед Михалыч, тренер зала "Железный Подвал". 65 лет, суровый, ворчливый. Фразы короткие.

ПРАВИЛА ОТВЕТА (СТРОГО ПЛОСКИЙ JSON, БЕЗ МАССИВОВ):
Твой ответ ДОЛЖЕН быть валидным JSON. Никаких пояснений, только JSON.
ЗАПРЕЩЕНО использовать кавычки и переносы строк внутри текстовых полей!
Формат:
{
  "t": "Твой ответ (максимум 2 коротких предложения)",
  "b": "Текст кнопки 1|Текст кнопки 2",
  "m": -5,
  "s": 0,
  "e": 0,
  "bgt": 0
}
- "t": текст ответа от Деда Михалыча.
- "b": строка с вариантами действий, разделёнными символом | (минимум 2, максимум 3).
- "m", "s", "e", "bgt": изменения статов (m=мотивация, s=сила, e=выносливость, bgt=бюджет). Если изменений нет, пиши 0.
"""

SYSTEM_PROMPT = (
    "Ты гейм-мастер текстовой RPG. Всегда отыгрывай роль:\n" + CHARACTER_PROFILE
)

def call_gigachat_sync(history_list: list):
    chat_request = Chat(
        messages=history_list,
        max_tokens=2048,
        temperature=0.5,
        top_p=0.9
    )
    return giga.chat(chat_request)

def heal_json(json_str: str) -> str:
    json_str = json_str.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    open_braces = json_str.count('{') - json_str.count('}')
    temp_str = json_str.replace('\\"', '')
    if temp_str.count('"') % 2 != 0:
        json_str += '"'
    json_str += '}' * open_braces
    return json_str

def parse_ai_response(raw_text: str):
    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    json_str = match.group(0) if match else raw_text
    
    if not json_str.startswith('{') and '"t"' in raw_text:
        json_str = "{" + raw_text

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            data = json.loads(heal_json(json_str))
        except json.JSONDecodeError:
            logging.warning(f"Failed to parse JSON even after healing.")
            return raw_text, [], {}

    text = str(data.get("t", raw_text))
    buttons_str = str(data.get("b", ""))
    
    buttons_data = []
    if buttons_str:
        for i, btn_text in enumerate(buttons_str.split('|')):
            btn_text = btn_text.strip()
            if btn_text:
                safe_callback = f"action_{i}_{re.sub(r'[^a-z0-9_]', '', btn_text.lower())[:20]}"
                buttons_data.append({"text": btn_text, "callback": safe_callback})
                
    stats_change = {}
    mapping = {"m": "motivation", "s": "strength", "e": "endurance", "bgt": "budget"}
    for k, stat_name in mapping.items():
        if k in data:
            try:
                stats_change[stat_name] = int(data[k])
            except (ValueError, TypeError):
                pass
                    
    return text, buttons_data, stats_change

# --- ЕДИНЫЙ ОБРАБОТЧИК ХОДА ---
async def process_turn(update_obj, user_input_text, message_obj):
    user_id = update_obj.from_user.id
    
    # Загружаем данные из базы (используем to_thread, чтобы не блокировать бота)
    stats = await asyncio.to_thread(get_user_stats, user_id)
    history = await asyncio.to_thread(get_history, user_id, MAX_HISTORY_MESSAGES)

    full_system_prompt = f"{SYSTEM_PROMPT}\n\n{stats.to_prompt_str()}"
    
    history_to_send = [
        Messages(role=MessagesRole.SYSTEM, content=full_system_prompt)
    ] + history + [
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
                if hasattr(stats, stat):
                    new_value = getattr(stats, stat) + value
                    setattr(stats, stat, max(0, min(100, new_value)))
            
            # Сохраняем статы в базу
            await asyncio.to_thread(update_user_stats, user_id, stats)
            
            if stats.motivation <= 0:
                reply_text = "💀 Мотивация на нуле! Вы ушли есть шаверму. Игра окончена. (Используйте /reset)"
                buttons_data = []

        # 🛡️ СТРАХОВКА: Если ИИ не вернул кнопки
        if not buttons_data and stats.motivation > 0:
            logging.warning("ИИ не вернул кнопки, применяем fallback-клавиатуру")
            buttons_data = [
                {"text": "🔄 Попробовать другое действие", "callback": "fallback_retry"},
                {"text": "📊 Посмотреть мои статы", "callback": "show_stats"},
                {"text": "🆘 Позвать Деда Михалыча на помощь", "callback": "fallback_help"}
            ]

        # Формируем компактный JSON для истории
        history_json = json.dumps({
            "t": reply_text,
            "b": "|".join([btn["text"] for btn in buttons_data]),
            "m": stats_change.get("motivation", 0),
            "s": stats_change.get("strength", 0),
            "e": stats_change.get("endurance", 0),
            "bgt": stats_change.get("budget", 0)
        }, ensure_ascii=False)

        # Сохраняем ход в базу данных
        if "GAME_OVER" not in reply_text and stats.motivation > 0:
            await asyncio.to_thread(append_history, user_id, MessagesRole.USER.value, user_input_text)
            await asyncio.to_thread(append_history, user_id, MessagesRole.ASSISTANT.value, history_json)

        # Строим клавиатуру
        keyboard = None
        if buttons_data and stats.motivation > 0:
            builder = InlineKeyboardBuilder()
            for btn in buttons_data:
                builder.button(text=btn["text"], callback_data=str(btn.get("callback", "btn"))[:64])
            builder.adjust(1)
            builder.button(text="📊 Мои статы", callback_data="show_stats")
            keyboard = builder.as_markup()

        await message_obj.answer(reply_text, reply_markup=keyboard)

    except Exception as e:
        logging.error(f"GigaChat API error: {e}")
        await message_obj.answer("Произошла ошибка. Попробуйте еще раз.")

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await asyncio.to_thread(reset_user, user_id)
    
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
    await asyncio.to_thread(reset_user, user_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Войти в зал заново", callback_data="look_around")
    
    await message.answer("🔄 История и статы сброшены. Начинаем сначала, боец!", reply_markup=builder.as_markup())

@dp.message(F.text)
async def handle_text(message: types.Message):
    await process_turn(message, message.text, message)

@dp.callback_query(F.data)
async def handle_button_click(callback: types.CallbackQuery):
    await callback.answer()
    
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
    # Берем статы из базы
    stats = await asyncio.to_thread(get_user_stats, callback.from_user.id)
    
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
    # Инициализируем базу данных перед запуском бота
    await asyncio.to_thread(init_db)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")