import asyncio
import logging
import json
import re
import sqlite3
import base64
import random
from dataclasses import dataclass
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
MAX_HISTORY_MESSAGES = 20
DB_NAME = "game_data.db"

# 🎯 ВАШИ КЛЮЧИ (замените на реальные или используйте .env)
TOKEN = values.get("BOT_TOKEN") or values.get("TOKEN")
GIGACHAT_CREDENTIALS = values.get("GIGACHAT_CREDENTIALS")
proxy_url="socks5://eu8buz:zEhk8F@168.80.73.57:8000"

# 🎨 Единый визуальный стиль для всех генерируемых изображений
IMAGE_STYLE = (
    "dark atmospheric soviet basement gym, 1980s aesthetic, rusty old iron equipment, "
    "harsh fluorescent lighting, cinematic composition, gritty realism, "
    "moody shadows, film grain, photorealistic"
)

# 🖼️ Интервал генерации изображений (каждые 2–3 хода)
IMAGE_MIN_INTERVAL = 2
IMAGE_MAX_INTERVAL = 3

# 📅 Механика дней
TURNS_PER_DAY = 30      # Ходов в одном дне
MAX_DAYS = 7            # Максимум дней в игре

# Прирост характеристик и весов при переходе на новый день
DAY_STRENGTH_GAIN = 2
DAY_ENDURANCE_GAIN = 2
DAY_MOTIVATION_GAIN = 20
DAY_BENCH_GAIN = 5 
DAY_SQUAT_GAIN = 7
DAY_DEADLIFT_GAIN = 10

# 🚩 Фразы-триггеры окончания тренировки
DAY_END_TRIGGERS = [
    "тренировка окончена", "тренировка завершена", "тренировка закончена",
    "конец тренировки", "на сегодня всё", "на сегодня все",
    "сегодня всё", "сегодня все", "иди домой", "иди отдыхать",
    "отправляйся домой", "пора домой", "свободен", "вали домой"
]

# --- ТЕКСТ ПРИВЕТСТВИЯ ДЛЯ /start ---
START_TEXT = (
    "🚪 Вы толкаете тяжелую металлическую дверь. Изнутри доносится "
    "звук падающих блинов и тяжелый бас: 'Ещё два раза, давай!'.\n\n"
    "Это 'Железный Подвал'. Ваше приключение начинается.\n"
    f"📅 Программа: {MAX_DAYS} дней по {TURNS_PER_DAY} ходов. Каждый день веса растут.\n\n"
    "📜 Команды:\n"
    "/start — начать игру\n"
    "/new — очистить диалог и начать заново\n"
    "/reset — сбросить прогресс\n"
    "📊 Мои статы — посмотреть характеристики"
)

# 🧹 ТЕКСТ ПРИВЕТСТВИЯ ДЛЯ /new (с визуальным разделителем)
NEW_GAME_TEXT = (
    "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
    "🔄 **ДИАЛОГ ПОЛНОСТЬЮ ОЧИЩЕН**\n"
    "➖➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
    "🚪 Вы толкаете тяжелую металлическую дверь. Изнутри доносится "
    "звук падающих блинов и тяжелый бас: 'Ещё два раза, давай!'.\n\n"
    "Это 'Железный Подвал'. Ваше приключение начинается заново.\n"
    f"📅 Программа: {MAX_DAYS} дней по {TURNS_PER_DAY} ходов. Каждый день веса растут.\n\n"
    "📜 Команды:\n"
    "/start — начать игру\n"
    "/new — полная очистка и рестарт\n"
    "/reset — быстрый сброс прогресса\n"
    "/help — Справка о имеющихся командах\n"
    "📊 Мои статы — посмотреть характеристики"
)

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
    bench_press: int = 40
    squat: int = 60
    deadlift: int = 80
    current_day: int = 1
    day_turn_count: int = 0

    def to_prompt_str(self):
        return (
            f"[День {self.current_day}/{MAX_DAYS}, ход {self.day_turn_count}/{TURNS_PER_DAY} | "
            f"Статы: Сила={self.strength}, Выносл={self.endurance}, "
            f"Мотив={self.motivation}, Бюджет={self.budget} | "
            f"Рабочие веса: Жим={self.bench_press}кг, Присед={self.squat}кг, Тяга={self.deadlift}кг]"
        )

# --- БАЗА ДАННЫХ (SQLITE) ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                strength INTEGER DEFAULT 10, endurance INTEGER DEFAULT 10,
                motivation INTEGER DEFAULT 50, budget INTEGER DEFAULT 1000,
                bench_press INTEGER DEFAULT 40, squat INTEGER DEFAULT 60, deadlift INTEGER DEFAULT 80,
                current_day INTEGER DEFAULT 1, day_turn_count INTEGER DEFAULT 0,
                turn_count INTEGER DEFAULT 0, next_image_turn INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, role TEXT, content TEXT
            )
        """)
        
        migration_columns = [
            ("bench_press", "INTEGER DEFAULT 40"), ("squat", "INTEGER DEFAULT 60"),
            ("deadlift", "INTEGER DEFAULT 80"), ("current_day", "INTEGER DEFAULT 1"),
            ("day_turn_count", "INTEGER DEFAULT 0"), ("turn_count", "INTEGER DEFAULT 0"),
            ("next_image_turn", "INTEGER DEFAULT 1"),
        ]
        for col_name, col_def in migration_columns:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass
        
        conn.commit()

def get_user_stats(user_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT strength, endurance, motivation, budget,
                   bench_press, squat, deadlift, current_day, day_turn_count
            FROM users WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            return PlayerStats(
                strength=row["strength"], endurance=row["endurance"], motivation=row["motivation"],
                budget=row["budget"], bench_press=row["bench_press"], squat=row["squat"],
                deadlift=row["deadlift"], current_day=row["current_day"], day_turn_count=row["day_turn_count"],
            )
        else:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return PlayerStats()

def update_user_stats(user_id: int, stats: PlayerStats):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            UPDATE users SET strength = ?, endurance = ?, motivation = ?, budget = ?,
                             bench_press = ?, squat = ?, deadlift = ?,
                             current_day = ?, day_turn_count = ?
            WHERE user_id = ?
        """, (
            stats.strength, stats.endurance, stats.motivation, stats.budget,
            stats.bench_press, stats.squat, stats.deadlift,
            stats.current_day, stats.day_turn_count, user_id
        ))
        conn.commit()

def reset_user(user_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.execute("""
            INSERT INTO users (user_id, turn_count, next_image_turn, current_day, day_turn_count)
            VALUES (?, 0, 1, 1, 0)
        """, (user_id,))
        conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        conn.commit()

def increment_turn_count(user_id: int) -> int:
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE users SET turn_count = turn_count + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        cursor = conn.execute("SELECT turn_count FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

def get_next_image_turn(user_id: int) -> int:
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("SELECT next_image_turn FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 1

def schedule_next_image(user_id: int, current_turn: int):
    next_turn = current_turn + random.randint(IMAGE_MIN_INTERVAL, IMAGE_MAX_INTERVAL)
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE users SET next_image_turn = ? WHERE user_id = ?", (next_turn, user_id))
        conn.commit()

def advance_to_next_day(stats: PlayerStats) -> PlayerStats:
    stats.current_day += 1
    stats.day_turn_count = 0
    stats.strength += DAY_STRENGTH_GAIN
    stats.endurance += DAY_ENDURANCE_GAIN
    stats.motivation = min(100, stats.motivation + DAY_MOTIVATION_GAIN)
    stats.bench_press += DAY_BENCH_GAIN
    stats.squat += DAY_SQUAT_GAIN
    stats.deadlift += DAY_DEADLIFT_GAIN
    return stats

def get_history(user_id: int, limit: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("""
            SELECT role, content FROM history 
            WHERE user_id = ? 
            ORDER BY id DESC 
            LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        rows.reverse()
        return [Messages(role=MessagesRole(row[0]), content=row[1]) for row in rows]

def append_history(user_id: int, role: str, content: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        conn.commit()

def get_last_buttons(user_id: int, count: int = 2):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("""
            SELECT content FROM history 
            WHERE user_id = ? AND role = 'assistant'
            ORDER BY id DESC 
            LIMIT ?
        """, (user_id, count))
        rows = cursor.fetchall()
        
        last_buttons = []
        for row in rows:
            try:
                data = json.loads(row[0])
                buttons_str = data.get("b", "")
                if buttons_str:
                    buttons = [b.strip() for b in buttons_str.split('|') if b.strip()]
                    last_buttons.extend(buttons)
            except (json.JSONDecodeError, KeyError):
                pass
                
        return list(set(last_buttons))

# --- ПРОМПТ И ЛОГИКА ИИ ---
CHARACTER_PROFILE = """
Ты — Дед Михалыч, тренер зала "Железный Подвал". 65 лет, суровый, ворчливый. Фразы короткие.

ВИЗУАЛЬНЫЙ СТИЛЬ ИГРЫ:
Действие происходит в тёмном подвальном тренажерном зале советской эпохи 80-х. Ржавое железо, тусклый флуоресцентный свет, мрачная атмосфера, кинематографичные тени.
Дед Михалыч: 65 лет, седой, морщинистое суровое лицо, изношенный спортивный костюм, вечно недовольное выражение.

ИГРОВАЯ МЕХАНИКА:
- Игра длится 7 дней, каждый день состоит из 30 ходов.
- В начале каждого нового дня рабочие веса игрока растут, а он становится сильнее.
- Учитывай текущий день и рабочие веса игрока в своих ответах. По мере прогресса тренировки становятся тяжелее.
- ВАЖНО: Если считаешь, что тренировка на сегодня должна быть завершена (игрок устал, тренировка была насыщенной, или просто конец логичной сцены) — обязательно добавь в поле "t" одну из этих фраз: "тренировка окончена", "на сегодня всё", "иди домой". Это переведёт игру на следующий день. НЕ злоупотребляй — используй только когда это действительно уместно!

ПРАВИЛА ОТВЕТА (СТРОГО ПЛОСКИЙ JSON, БЕЗ МАССИВОВ):
Твой ответ ДОЛЖЕН быть валидным JSON. Никаких пояснений, только JSON.
ЗАПРЕЩЕНО использовать кавычки и переносы строк внутри текстовых полей!
Формат:
{
  "t": "Твой ответ (максимум 2 коротких предложения)",
  "b": "Текст кнопки 1|Текст кнопки 2|Текст кнопки 3",
  "img": "Описание сцены для картинки на английском (10-15 слов). Описывай сцену в стиле сурового советского подвального зала 80-х. Если в сцене есть Дед Михалыч, опиши его как седого 65-летнего тренера в изношенном спортивном костюме. Если ничего визуально важного не произошло, оставь пустым.",
  "m": -5,
  "s": 0,
  "e": 0,
  "bgt": 0
}
- "t": текст ответа от Деда Михалыча.
- "b": строка с вариантами действий, разделёнными символом | (СТРОГО МИНИМУМ 3 варианта действий!).
- "img": описание сцены на английском. Заполняй, когда происходит что-то визуально значимое.
- "m", "s", "e", "bgt": изменения статов.
"""

SYSTEM_PROMPT = (
    "Ты гейм-мастер текстовой RPG. Всегда отыгрывай роль:\n" + CHARACTER_PROFILE
)

def create_dynamic_prompt(stats: PlayerStats, last_buttons: list) -> str:
    base_prompt = f"{SYSTEM_PROMPT}\n\n{stats.to_prompt_str()}"
    if last_buttons:
        buttons_list = ", ".join([f'"{btn}"' for btn in last_buttons[:10]])
        base_prompt += f"\n\nВАЖНО: Ты НЕ ДОЛЖЕН повторять эти варианты действий последние 2-3 хода: {buttons_list}. Придумай СОВЕРШЕННО НОВЫЕ варианты!"
    return base_prompt

def call_gigachat_sync(history_list: list):
    chat_request = Chat(
        messages=history_list,
        max_tokens=2048,
        temperature=0.5,
        top_p=0.9
    )
    return giga.chat(chat_request)

def generate_gigachat_image_sync(img_prompt: str) -> bytes | None:
    full_prompt = f"{img_prompt}, {IMAGE_STYLE}"
    
    chat_request = Chat(
        messages=[
            Messages(role=MessagesRole.USER, content=f"Нарисуй: {full_prompt}")
        ],
        function_call="auto",
    )
    try:
        response = giga.chat(chat_request)
        content = response.choices[0].message.content
        
        match = re.search(r'<img\s+src="([^"]+)"', content)
        if not match:
            logging.warning("GigaChat не вернул тег <img> в ответе на генерацию.")
            return None
            
        file_id = match.group(1)
        image_obj = giga.get_image(file_id)
        
        if hasattr(image_obj, 'content'):
            return base64.b64decode(image_obj.content)
        
        return None
    except Exception as e:
        logging.error(f"Ошибка генерации изображения: {e}")
        return None

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
            return raw_text, [], {}, ""

    text = str(data.get("t", raw_text))
    buttons_str = str(data.get("b", ""))
    img_prompt = str(data.get("img", "")).strip()
    
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
                    
    return text, buttons_data, stats_change, img_prompt


def check_day_end_trigger(text: str) -> tuple:
    text_lower = text.lower()
    cleaned_text = text
    
    for trigger in DAY_END_TRIGGERS:
        if trigger in text_lower:
            pattern = re.compile(r'[,.;!]*\s*' + re.escape(trigger) + r'\s*[,.;!]*', re.IGNORECASE)
            cleaned_text = pattern.sub(' ', cleaned_text).strip()
            cleaned_text = re.sub(r'\s+', ' ', cleaned_text)
            return True, cleaned_text
    
    return False, text


# --- ЕДИНЫЙ ОБРАБОТЧИК ХОДА ---
async def process_turn(update_obj, user_input_text, message_obj, human_action: str = ""):
    user_id = update_obj.from_user.id
    
    stats = await asyncio.to_thread(get_user_stats, user_id)
    
    if stats.current_day > MAX_DAYS:
        await message_obj.answer(
            "🎓 Программа завершена! Ты прошёл все 7 дней в 'Железном Подвале'.\n"
            "Используй /new, чтобы очистить диалог и начать заново."
        )
        return
    
    history = await asyncio.to_thread(get_history, user_id, MAX_HISTORY_MESSAGES)
    last_buttons = await asyncio.to_thread(get_last_buttons, user_id, 2)
    
    turn_count = await asyncio.to_thread(increment_turn_count, user_id)
    next_image_turn = await asyncio.to_thread(get_next_image_turn, user_id)
    should_generate_image = turn_count >= next_image_turn
    
    if should_generate_image:
        await asyncio.to_thread(schedule_next_image, user_id, turn_count)
    
    full_system_prompt = create_dynamic_prompt(stats, last_buttons)
    
    history_to_send = [
        Messages(role=MessagesRole.SYSTEM, content=full_system_prompt)
    ] + history + [
        Messages(role=MessagesRole.USER, content=user_input_text)
    ]

    await bot.send_chat_action(message_obj.chat.id, "typing")

    try:
        response = await asyncio.to_thread(call_gigachat_sync, history_to_send)
        raw_ai_reply = response.choices[0].message.content

        reply_text, buttons_data, stats_change, img_prompt = parse_ai_response(raw_ai_reply)

        if stats_change:
            for stat, value in stats_change.items():
                if hasattr(stats, stat):
                    new_value = getattr(stats, stat) + value
                    setattr(stats, stat, max(0, min(100, new_value)))
        
        stats.day_turn_count += 1
        
        day_end_triggered = False
        day_end_triggered, reply_text = check_day_end_trigger(reply_text)
        
        day_advanced = False
        game_finished = False
        
        if stats.day_turn_count >= TURNS_PER_DAY:
            if stats.current_day < MAX_DAYS:
                stats = advance_to_next_day(stats)
                day_advanced = True
            else:
                stats.current_day = MAX_DAYS + 1
                game_finished = True
        elif day_end_triggered:
            if stats.current_day < MAX_DAYS:
                stats = advance_to_next_day(stats)
                day_advanced = True
                stats.day_turn_count = 0
            else:
                stats.current_day = MAX_DAYS + 1
                game_finished = True
        
        await asyncio.to_thread(update_user_stats, user_id, stats)
        
        if game_finished:
            await message_obj.answer(reply_text)
            await message_obj.answer(
                "🎓 **Поздравляю, боец!**\n\n"
                "Ты прошёл все 7 дней в 'Железном Подвале'. Дед Михалыч жмёт тебе руку:\n"
                "'Неплохо. Совсем неплохо. Теперь ты не тот хлюпик, что зашёл сюда неделю назад.'\n\n"
                f"📊 Итоговые характеристики:\n"
                f"💪 Сила: {stats.strength}\n"
                f"⚡ Выносливость: {stats.endurance}\n"
                f"🏋️ Жим лёжа: {stats.bench_press} кг\n"
                f"🦵 Присед: {stats.squat} кг\n"
                f"💪 Становая тяга: {stats.deadlift} кг\n\n"
                "Используй /new, чтобы начать заново."
            )
            return
        
        if stats.motivation <= 0:
            reply_text = "💀 Мотивация на нуле! Вы ушли есть шаверму. Игра окончена. (Используйте /new)"
            buttons_data = []
            img_prompt = ""
            should_generate_image = False

        if stats.motivation > 0:
            if not buttons_data or day_end_triggered:
                if day_end_triggered or day_advanced:
                    logging.info("Применяем стартовую клавиатуру для нового дня")
                    buttons_data = [
                        {"text": "🚪 Войти в зал", "callback": "enter_gym"},
                        {"text": "👀 Осмотреться", "callback": "fallback_look"},
                        {"text": "🗣️ Найти Михалыча", "callback": "call_trainer"}
                    ]
                else:
                    logging.warning("ИИ не вернул кнопки, применяем fallback-клавиатуру")
                    buttons_data = [
                        {"text": "👀 Осмотреться в зале", "callback": "fallback_look"},
                        {"text": "🗣️ Позвать Михалыча", "callback": "fallback_call"},
                        {"text": "🤷 Пожать плечами", "callback": "fallback_shrug"}
                    ]
            else:
                filtered_buttons = []
                for btn in buttons_data:
                    btn_text_lower = btn["text"].lower().strip()
                    is_duplicate = any(
                        last_btn.lower().strip() in btn_text_lower or btn_text_lower in last_btn.lower().strip() 
                        for last_btn in last_buttons
                    )
                    if not is_duplicate:
                        filtered_buttons.append(btn)
                
                if len(filtered_buttons) < 3:
                    new_actions = [
                        {"text": "🏋️ Подойти к штанге", "callback": "action_barbell"},
                        {"text": "🚶 Пройтись по залу", "callback": "action_walk"},
                        {"text": "👀 Осмотреть тренажеры", "callback": "action_machines"},
                        {"text": "💬 Поговорить с качком рядом", "callback": "action_talk_gym_bro"},
                        {"text": "📱 Проверить телефон", "callback": "action_phone"},
                        {"text": "🚰 Попить воды", "callback": "action_water"}
                    ]
                    used_callbacks = [btn["callback"] for btn in filtered_buttons]
                    for action in new_actions:
                        if len(filtered_buttons) >= 3:
                            break
                        if action["callback"] not in used_callbacks:
                            filtered_buttons.append(action)
                
                buttons_data = filtered_buttons

        history_json = json.dumps({
            "t": reply_text,
            "b": "|".join([btn["text"] for btn in buttons_data]),
            "m": stats_change.get("motivation", 0),
            "s": stats_change.get("strength", 0),
            "e": stats_change.get("endurance", 0),
            "bgt": stats_change.get("budget", 0)
        }, ensure_ascii=False)

        if "GAME_OVER" not in reply_text and stats.motivation > 0:
            await asyncio.to_thread(append_history, user_id, MessagesRole.USER.value, user_input_text)
            await asyncio.to_thread(append_history, user_id, MessagesRole.ASSISTANT.value, history_json)

        keyboard = None
        if buttons_data and stats.motivation > 0:
            builder = InlineKeyboardBuilder()
            for btn in buttons_data:
                builder.button(text=btn["text"], callback_data=str(btn.get("callback", "btn"))[:64])
            builder.adjust(1)
            builder.button(text="📊 Мои статы", callback_data="show_stats")
            keyboard = builder.as_markup()

        img_prompt_final = img_prompt
        if not img_prompt_final:
            img_prompt_final = human_action if human_action else user_input_text

        if img_prompt_final and stats.motivation > 0 and should_generate_image and not day_end_triggered:
            await bot.send_chat_action(message_obj.chat.id, "upload_photo")
            try:
                image_bytes = await asyncio.to_thread(generate_gigachat_image_sync, img_prompt_final)
                
                if image_bytes:
                    photo = BufferedInputFile(image_bytes, filename="scene.jpg")
                    
                    if len(reply_text) <= 1000:
                        await message_obj.answer_photo(photo=photo, caption=reply_text, reply_markup=keyboard)
                    else:
                        await message_obj.answer(reply_text, reply_markup=keyboard)
                        await message_obj.answer_photo(photo=photo)
                else:
                    await message_obj.answer(reply_text, reply_markup=keyboard)
            except Exception as e:
                logging.error(f"GigaChat image generation error: {e}")
                await message_obj.answer(reply_text, reply_markup=keyboard)
        else:
            await message_obj.answer(reply_text, reply_markup=keyboard)
        
        if day_advanced:
            await message_obj.answer(
                f"🌅 **День {stats.current_day} из {MAX_DAYS}**\n\n"
                f"Дед Михалыч кивает: 'Неплохо, боец. Но сегодня работаем тяжелее.'\n\n"
                f"📈 Рабочие веса выросли:\n"
                f"🏋️ Жим лёжа: {stats.bench_press} кг\n"
                f"🦵 Присед: {stats.squat} кг\n"
                f"💪 Становая тяга: {stats.deadlift} кг\n\n"
                f"💪 Сила: {stats.strength} | ⚡ Выносливость: {stats.endurance} | "
                f"🔥 Мотивация: {stats.motivation}"
            )

    except Exception as e:
        logging.error(f"GigaChat API error: {e}")
        await message_obj.answer("Произошла ошибка. Попробуйте еще раз.")

# --- ОБРАБОТЧИКИ КОМАНД ---
async def send_welcome(message: types.Message, welcome_text: str = None):
    """Единая функция для отправки приветственного сообщения"""
    if welcome_text is None:
        welcome_text = START_TEXT
    
    user_id = message.from_user.id
    await asyncio.to_thread(reset_user, user_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="💪 Осмотреться в зале", callback_data="look_around")
    builder.button(text="🗣️ Громко позвать тренера", callback_data="call_trainer")
    builder.button(text="🚪 Развернуться и уйти", callback_data="run_away")
    builder.adjust(1)
    
    await message.answer(welcome_text, reply_markup=builder.as_markup())


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await send_welcome(message)


@dp.message(Command("new"))
async def cmd_new(message: types.Message):
    """🧹 Команда /new — сбрасывает игру и удаляет сообщение команды"""
    # Пытаемся удалить сообщение с командой для эффекта "чистого диалога"
    try:
        await message.delete()
    except Exception as e:
        # В некоторых чатах (групповых) удаление может не сработать — не страшно
        logging.warning(f"Не удалось удалить сообщение команды /new: {e}")
    
    # 🔄 Используем специальный текст с разделителем
    await send_welcome(message, welcome_text=NEW_GAME_TEXT)


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    user_id = message.from_user.id
    await asyncio.to_thread(reset_user, user_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Войти в зал заново", callback_data="look_around")
    
    await message.answer(
        "🔄 История и статы сброшены. Начинаем сначала, боец!\n"
        "💡 Используй /new, чтобы очистить диалог полностью.",
        reply_markup=builder.as_markup()
    )
@dp.message(Command("help"))
async def cmd_reset(message: types.Message):
    user_id = message.from_user.id
    await asyncio.to_thread(reset_user, user_id)
    await message.answer(
        "💡/satrt - новая игра\n"
        "💡/new - очистить память диалог полностью.",
    )


async def send_stats(target):
    user_id = target.from_user.id
    stats = await asyncio.to_thread(get_user_stats, user_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Вернуться к тренировке", callback_data="look_around")
    
    text = (
        f"📊 **Ваши характеристики**\n\n"
        f"📅 День: {stats.current_day}/{MAX_DAYS} (ход {stats.day_turn_count}/{TURNS_PER_DAY})\n\n"
        f"💪 Сила: {stats.strength}\n"
        f"⚡ Выносливость: {stats.endurance}\n"
        f"🔥 Мотивация: {stats.motivation}\n"
        f"💰 Бюджет: {stats.budget} руб.\n\n"
        f"🏋️ **Рабочие веса:**\n"
        f"Жим лёжа: {stats.bench_press} кг\n"
        f"Присед: {stats.squat} кг\n"
        f"Становая тяга: {stats.deadlift} кг"
    )
    
    if isinstance(target, types.CallbackQuery):
        await target.message.answer(text, reply_markup=builder.as_markup())
    else:
        await target.answer(text, reply_markup=builder.as_markup())


@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text and message.text.lower().strip() in ["мои статы", "статы", "характеристики", "прогресс"]:
        await send_stats(message)
        return

    await process_turn(message, message.text, message, human_action=message.text)


@dp.callback_query(F.data)
async def handle_button_click(callback: types.CallbackQuery):
    await callback.answer()
    
    if callback.data == "fallback_retry":
        action_text = "[Игрок растерялся и хочет попробовать что-то другое]"
        human_action = "Игрок растерялся и хочет попробовать что-то другое"
    elif callback.data == "fallback_help":
        action_text = "[Игрок кричит: 'Дед Михалыч, помоги!']"
        human_action = "Игрок кричит: Дед Михалыч, помоги"
    elif callback.data == "fallback_look":
        action_text = "[Игрок осматривается в зале]"
        human_action = "Игрок внимательно осматривается в тренажерном зале"
    elif callback.data == "fallback_call":
        action_text = "[Игрок зовет Деда Михалыча]"
        human_action = "Игрок громко зовет Деда Михалыча"
    elif callback.data == "fallback_shrug":
        action_text = "[Игрок пожимает плечами]"
        human_action = "Игрок недоуменно пожимает плечами"
    else:
        button_text = ""
        try:
            if callback.message.reply_markup:
                for row in callback.message.reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.callback_data == callback.data:
                            button_text = btn.text
                            break
                    if button_text:
                        break
        except Exception:
            pass
        
        human_action = button_text if button_text else callback.data
        action_text = f"[Игрок выбрал действие: {button_text or callback.data}]"
        
    await process_turn(callback, action_text, callback.message, human_action=human_action)


@dp.callback_query(F.data == "show_stats")
async def show_stats_callback(callback: types.CallbackQuery):
    await callback.answer()
    await send_stats(callback)

# --- ЗАПУСК ---
async def main():
    await asyncio.to_thread(init_db)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")