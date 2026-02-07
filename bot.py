import asyncio
import json
import os
import random
import datetime
import time
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.types.input_file import FSInputFile


# =========================
# Настройки
# =========================

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN env var is not set")

CONSULT_URL = "https://t.me/olga_febr"

OFFER_AFTER_N_ANSWERS = 5
OFFER_WINDOW_SECONDS = 30 * 60          # 30 минут
OFFER_COOLDOWN_SECONDS = 6 * 60 * 60    # 6 часов


# =========================
# Инициализация бота
# =========================

bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()


# =========================
# FSM
# =========================

class Flow(StatesGroup):
    waiting_tarot_question = State()


# =========================
# Загрузка колод
# =========================

def load_cards(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "cards" not in data or not isinstance(data["cards"], list):
        raise ValueError(f"{path} must contain {{'cards': [...]}}")
    return data["cards"]


TAROT_CARDS = load_cards("cards.json")
MIND_CARDS = load_cards("mind_cards.json")


# =========================
# Постоянное меню
# =========================

def persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Карта дня")],
            [KeyboardButton(text="🔮 Ответ на вопрос")],
            [KeyboardButton(text="🫧 Карта отклика")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери режим…",
    )


# =========================
# Карта дня (стабильная)
# =========================

def stable_day_card_for_user(user_id: int) -> dict:
    today = datetime.date.today().isoformat()
    seed = f"{user_id}-{today}"
    rnd = random.Random(seed)
    return rnd.choice(TAROT_CARDS + MIND_CARDS)


# =========================
# Тексты: description / descriptions
# =========================

def pick_description(card: dict) -> str:
    descs = card.get("descriptions")
    if isinstance(descs, list) and descs:
        return random.choice(descs)
    return card.get("description", "")


def image_path(card: dict) -> str:
    return f"cards/{card.get('image', '')}"


# =========================
# Предложение "глубже" — только после Ответа на вопрос
# =========================

USER_ANSWERS = defaultdict(lambda: deque())    # user_id -> deque[timestamps]
USER_LAST_OFFER = defaultdict(lambda: 0.0)    # user_id -> last_offer_ts

def should_prompt_deeper(user_id: int) -> bool:
    now = time.time()

    q = USER_ANSWERS[user_id]
    q.append(now)

    cutoff = now - OFFER_WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()

    # кулдаун на предложение
    if now - USER_LAST_OFFER[user_id] < OFFER_COOLDOWN_SECONDS:
        return False

    if len(q) >= OFFER_AFTER_N_ANSWERS:
        USER_LAST_OFFER[user_id] = now
        return True

    return False


def prompt_deeper_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да 🌙", callback_data="deeper_yes"),
                InlineKeyboardButton(text="Не сейчас", callback_data="deeper_no"),
            ]
        ]
    )


def consult_button_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Разобрать вопрос глубже", url=CONSULT_URL)]
        ]
    )


# =========================
# Отправка карты (всегда 1 карта)
# =========================

async def send_one_card(message: types.Message, card: dict, prefix: str = ""):
    title = card.get("title", "Карта")
    desc = pick_description(card).strip()
    caption = f"{prefix}<b>{title}</b>\n\n{desc}".strip()

    img = card.get("image", "")
    path = image_path(card)

    if not img or not os.path.exists(path):
        await message.answer(
            caption + (f"\n\n(⚠️ Нет файла изображения: {img})" if img else "\n\n(⚠️ Не указано поле image)"),
            reply_markup=persistent_keyboard(),
        )
        return

    photo = FSInputFile(path)
    await message.answer_photo(photo=photo, caption=caption)
    # чтобы меню не терялось на iOS/клиентах — продублируем
    await message.answer("Выбери следующий шаг:", reply_markup=persistent_keyboard())


# =========================
# Хэндлеры
# =========================

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Я рядом 🌿\n\nВыбери режим:", reply_markup=persistent_keyboard())


@dp.message(F.text == "🌿 Карта дня")
async def day_card(message: types.Message, state: FSMContext):
    await state.clear()
    card = stable_day_card_for_user(message.from_user.id)

    await message.answer("Пауза… вдох…")
    await asyncio.sleep(1)

    await send_one_card(message, card, prefix="🌿 ")


@dp.message(F.text == "🫧 Карта отклика")
async def mind_card(message: types.Message, state: FSMContext):
    await state.clear()
    card = random.choice(MIND_CARDS)

    await message.answer("Пусть проявится образ…")
    await asyncio.sleep(1)

    await send_one_card(message, card, prefix="🫧 ")


@dp.message(F.text == "🔮 Ответ на вопрос")
async def ask_question(message: types.Message, state: FSMContext):
    await state.set_state(Flow.waiting_tarot_question)
    await message.answer("Напиши вопрос одним сообщением — и я дам одну карту.")


@dp.message(Flow.waiting_tarot_question)
async def tarot_answer(message: types.Message, state: FSMContext):
    await state.clear()

    card = random.choice(TAROT_CARDS)

    await message.answer("Настраиваюсь на вопрос…")
    await asyncio.sleep(1)

    await send_one_card(message, card, prefix="🔮 ")

    # мягкое предложение (только после ответа на вопрос)
    if should_prompt_deeper(message.from_user.id):
        await message.answer(
            "Кажется, ты сейчас в глубоком процессе.\n"
            "Хочешь разобрать вопрос глубже и бережнее?",
            reply_markup=prompt_deeper_keyboard(),
        )


@dp.callback_query(F.data == "deeper_yes")
async def deeper_yes(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Хорошо 🌙 Если захочется — нажми кнопку ниже:",
        reply_markup=consult_button_keyboard(),
    )


@dp.callback_query(F.data == "deeper_no")
async def deeper_no(callback: types.CallbackQuery):
    await callback.answer("Хорошо 🤍")
    await callback.message.answer(
        "Ок. Я рядом и без спешки.",
        reply_markup=persistent_keyboard(),
    )


@dp.message()
async def fallback(message: types.Message):
    await message.answer("Выбери режим кнопками ниже 👇", reply_markup=persistent_keyboard())


# =========================
# Запуск
# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
