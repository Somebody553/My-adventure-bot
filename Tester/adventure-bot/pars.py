import asyncio
import logging
import json
import re
import random
import sqlite3
from dataclasses import dataclass
from urllib.parse import quote
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import BufferedInputFile
from aiogram.enums import ParseMode

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from dotenv import dotenv_values

# --- КОНФИГУРАЦИЯ ---
MAX_HISTORY_MESSAGES = 20
DB_NAME = "game_data.db"
IMAGE_GENERATION_COOLDOWN = 3  # Генерируем картинку раз в N ходов

BASE_DIR = Path(__file__).resolve().parent
env_path = BASE_DIR / ".env"

values = dotenv_values(env_path)
TOKEN = values.get("BOT_TOKEN") or values.get("TOKEN")
GIGACHAT_CREDENTIALS = values.get("GIGACHAT_CREDENTIALS")
PROXY_URL = "socks5://eu8buz:zEhk8F@168.80.73.57:8000"

logging.basicConfig(level=logging.INFO)

# Используем ОДИН прокси и для Telegram, и для загрузки картинок
session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession()
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
    turn_counter: int = 0  # Счетчик ходов для контроля генерации картинок

    def to_prompt_str(self):
        return f"[Статы: Сила={self.strength}, Выносл={self.endurance}, Мотив={self.motivation}, Бюджет={self.budget}]"


# --- БАЗА ДАННЫХ (SQLITE) ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                strength INTEGER DEFAULT 10,
                endurance INTEGER DEFAULT 10,
                motivation INTEGER DEFAULT 50,
                budget INTEGER DEFAULT 1000,
                turn_counter INTEGER DEFAULT 0
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


def get_user_stats(user_id: int) -> PlayerStats:
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT strength, endurance, motivation, budget, turn_counter FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        if row:
            return PlayerStats(
                row["strength"], row["endurance"],
                row["motivation"], row["budget"],
                row["turn_counter"]
            )
        else:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return PlayerStats()


def update_user_stats(user_id: int, stats: PlayerStats):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "UPDATE users SET strength=?, endurance=?, motivation=?, budget=?, turn_counter=? WHERE user_id=?",
            (stats.strength, stats.endurance, stats.motivation,
             stats.budget, stats.turn_counter, user_id)
        )
        conn.commit()


def reset_user(user_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        conn.commit()


def get_history(user_id: int, limit: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("""
            SELECT role, content FROM history 
            WHERE user_id = ? ORDER BY id DESC LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        rows.reverse()
        return [Messages(role=MessagesRole(row[0]), content=row[1]) for row in rows]


def append_history(user_id: int, role: str, content: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        )
        conn.commit()


def get_last_buttons(user_id: int, count: int = 2):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("""
            SELECT content FROM history 
            WHERE user_id = ? AND role = 'assistant'
            ORDER BY id DESC LIMIT ?
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

        return list(dict.fromkeys(last_buttons))  # сохраняем порядок, убирая дубли


# --- ПРОМПТ И ЛОГИКА ИИ ---
CHARACTER_PROFILE = """
Ты — Дед Михалыч, тренер зала "Железный Подвал". 65 лет, суровый, ворчливый. Фразы короткие.

ПРАВИЛА ОТВЕТА (СТРОГО ПЛОСКИЙ JSON, БЕЗ МАССИВОВ):
Твой ответ ДОЛЖЕН быть валидным JSON. Никаких пояснений, только JSON.
ЗАПРЕЩЕНО использовать кавычки и переносы строк внутри текстовых полей!
Формат:
{
  "t": "Твой ответ (максимум 2 коротких предложения)",
  "b": "Текст кнопки 1|Текст кнопки 2|Текст кнопки 3",
  "m": 0, "s": 0, "e": 0, "bgt": 0,
  "img": "короткое описание сцены для генерации картинки (или пустая строка)"
}
- "t": текст ответа от Деда Михалыча.
- "b": строка с вариантами действий, разделёнными | (СТРОГО МИНИМУМ 3 варианта!).
- "m", "s", "e", "bgt": изменения статов.
- "img": КРАТКОЕ описание сцены на АНГЛИЙСКОМ для генерации картинки (например: "grumpy old gym coach in dark basement gym, dramatic lighting"). 
  Добавляй ТОЛЬКО когда меняется локация или происходит важное событие. В остальных ходах пиши пустую строку "".
"""

SYSTEM_PROMPT = "Ты гейм-мастер текстовой RPG. Всегда отыгрывай роль:\n" + CHARACTER_PROFILE


def create_dynamic_prompt(stats: PlayerStats, last_buttons: list) -> str:
    base_prompt = f"{SYSTEM_PROMPT}\n\n{stats.to_prompt_str()}"

    if last_buttons:
        buttons_list = ", ".join([f'"{btn}"' for btn in last_buttons[:10]])
        base_prompt += f"\n\nВАЖНО: НЕ повторяй эти варианты действий последние 2-3 хода: {buttons_list}. Придумай СОВЕРШЕННО НОВЫЕ!"

    # Подсказываем ИИ, когда генерировать картинку
    turns_until_image = IMAGE_GENERATION_COOLDOWN - (stats.turn_counter % IMAGE_GENERATION_COOLDOWN)
    if turns_until_image == 1:
        base_prompt += "\n\nСЕЙЧАС СГЕНЕРИРУЙ описание сцены для картинки в поле 'img'!"
    else:
        base_prompt += f"\n\nКартинку генерировать рано (еще {turns_until_image} ходов). Поле 'img' оставь пустым."

    return base_prompt


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
            logging.warning("Failed to parse JSON even after healing.")
            return raw_text, [], {}, ""

    text = str(data.get("t", raw_text))
    buttons_str = str(data.get("b", ""))
    image_prompt = str(data.get("img", "")).strip()

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

    return text, buttons_data, stats_change, image_prompt


# --- ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ (НОВОЕ) ---
async def generate_image_pollinations(prompt: str) -> bytes | None:
    """Генерирует изображение через бесплатный Pollinations.ai"""
    # Добавляем стиль для консистентности
    style_suffix = ", dark dramatic lighting, gym atmosphere, realistic photo style, high detail"
    full_prompt = f"{prompt}{style_suffix}"
    encoded_prompt = quote(full_prompt)
    seed = random.randint(1, 100000)

    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=768&height=768&nologo=true&seed={seed}"
    )

    timeout = aiohttp.ClientTimeout(total=90)
    # Используем тот же прокси, что и у бота
    connector = None
    if PROXY_URL:
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(PROXY_URL)
        except ImportError:
            logging.warning("aiohttp-socks not installed, trying without proxy")

    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                logging.warning(f"Image generation failed with status {response.status}")
    except Exception as e:
        logging.error(f"Image generation error: {e}")

    return None


# --- ЕДИНЫЙ ОБРАБОТЧИК ХОДА ---
async def process_turn(update_obj, user_input_text, message_obj):
    user_id = update_obj.from_user.id

    stats = await asyncio.to_thread(get_user_stats, user_id)
    history = await asyncio.to_thread(get_history, user_id, MAX_HISTORY_MESSAGES)
    last_buttons = await asyncio.to_thread(get_last_buttons, user_id, 2)

    full_system_prompt = create_dynamic_prompt(stats, last_buttons)

    history_to_send = (
        [Messages(role=MessagesRole.SYSTEM, content=full_system_prompt)]
        + history
        + [Messages(role=MessagesRole.USER, content=user_input_text)]
    )

    await bot.send_chat_action(message_obj.chat.id, "typing")

    try:
        response = await asyncio.to_thread(call_gigachat_sync, history_to_send)
        raw_ai_reply = response.choices[0].message.content

        reply_text, buttons_data, stats_change, image_prompt = parse_ai_response(raw_ai_reply)

        # Применяем изменения статов
        if stats_change:
            for stat, value in stats_change.items():
                if hasattr(stats, stat):
                    new_value = getattr(stats, stat) + value
                    setattr(stats, stat, max(0, min(100, new_value)))

        # Увеличиваем счетчик ходов
        stats.turn_counter += 1

        game_over = False
        if stats.motivation <= 0:
            reply_text = "💀 Мотивация на нуле! Вы ушли есть шаверму. Игра окончена. (Используйте /reset)"
            buttons_data = []
            game_over = True

        # 🛡️ СТРАХОВКА кнопок
        if not game_over:
            if not buttons_data:
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
                        {"text": "💬 Поговорить с качком", "callback": "action_talk"},
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

        # Сохраняем обновленные статы
        await asyncio.to_thread(update_user_stats, user_id, stats)

        # Формируем компактный JSON для истории
        history_json = json.dumps({
            "t": reply_text,
            "b": "|".join([btn["text"] for btn in buttons_data]),
            "m": stats_change.get("motivation", 0),
            "s": stats_change.get("strength", 0),
            "e": stats_change.get("endurance", 0),
            "bgt": stats_change.get("budget", 0),
            "img": image_prompt
        }, ensure_ascii=False)

        if not game_over:
            await asyncio.to_thread(append_history, user_id, MessagesRole.USER.value, user_input_text)
            await asyncio.to_thread(append_history, user_id, MessagesRole.ASSISTANT.value, history_json)

        # Строим клавиатуру
        keyboard = None
        if buttons_data and not game_over:
            builder = InlineKeyboardBuilder()
            for btn in buttons_data:
                builder.button(text=btn["text"], callback_data=str(btn.get("callback", "btn"))[:64])
            builder.adjust(1)
            builder.button(text="📊 Мои статы", callback_data="show_stats")
            keyboard = builder.as_markup()

        # 🖼️ ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ
        image_bytes = None
        if image_prompt and not game_over:
            await bot.send_chat_action(message_obj.chat.id, "upload_photo")
            image_bytes = await generate_image_pollinations(image_prompt)

        # ОТПРАВКА ОТВЕТА
        if image_bytes:
            photo = BufferedInputFile(image_bytes, filename="scene.jpg")
            # caption имеет лимит 1024 символа
            caption = reply_text[:1020] if len(reply_text) > 1020 else reply_text
            await message_obj.answer_photo(photo=photo, caption=caption, reply_markup=keyboard)
        else:
            await message_obj.answer(reply_text, reply_markup=keyboard)

    except Exception as e:
        logging.error(f"GigaChat API error: {e}", exc_info=True)
        await message_obj.answer("Произошла ошибка. Попробуйте еще раз.")


# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await asyncio.to_thread(reset_user, user_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="💪 Осмотреться в зале", callback_data="look_around")
    builder.button(text="🗣️ Громко позвать тренера", callback_data="call_trainer")
    builder.button(text="🚪 Развернуться и уйти", callback_data="run_away")
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

    await message.answer(
        "🔄 История и статы сброшены. Начинаем сначала, боец!",
        reply_markup=builder.as_markup()
    )


@dp.message(F.text)
async def handle_text(message: types.Message):
    await process_turn(message, message.text, message)


@dp.callback_query(F.data)
async def handle_button_click(callback: types.CallbackQuery):
    await callback.answer()

    action_mapping = {
        "fallback_retry": "[Игрок растерялся и хочет попробовать что-то другое]",
        "fallback_help": "[Игрок кричит: 'Дед Михалыч, помоги!']",
        "fallback_look": "[Игрок осматривается в зале]",
        "fallback_call": "[Игрок зовет Деда Михалыча]",
        "fallback_shrug": "[Игрок пожимает плечами]",
    }

    action_text = action_mapping.get(callback.data, f"[Игрок выбрал действие: {callback.data}]")
    await process_turn(callback, action_text, callback.message)


@dp.callback_query(F.data == "show_stats")
async def show_stats_callback(callback: types.CallbackQuery):
    await callback.answer()
    stats = await asyncio.to_thread(get_user_stats, callback.from_user.id)

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Вернуться к тренировке", callback_data="look_around")

    await callback.message.answer(
        f"📊 *Ваши характеристики:*\n"
        f"💪 Сила: `{stats.strength}`\n"
        f"⚡ Выносливость: `{stats.endurance}`\n"
        f"🔥 Мотивация: `{stats.motivation}`\n"
        f"💰 Бюджет: `{stats.budget}` руб.\n"
        f"🎲 Ходов сыграно: `{stats.turn_counter}`",
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN
    )


# --- ЗАПУСК ---
async def main():
    await asyncio.to_thread(init_db)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")