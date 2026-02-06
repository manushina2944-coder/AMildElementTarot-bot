import asyncio
import json
import random
import os
import datetime
import hashlib

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
)

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Flow(StatesGroup):
    waiting_tarot_question = State()


def load_cards(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["cards"]


# Колоды
TAROT_CARDS = load_cards("cards.json")           # Таро
MIND_CARDS = load_cards("mind_cards.json")       # Карты отклика/образы

# Общий пул для "Карты дня"
DAY_CARDS = TAROT_CARDS + MIND_CARDS


def persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Карта дня")],
            [KeyboardButton(text="🔮 Ответ на вопрос")],
            [KeyboardButton(text="🫧 Карта отклика")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие ниже…",
    )


def stable_day_card_for_user(user_id: int) -> dict:
    """
    Стабильная "Карта дня" для конкретного пользователя:
    зависит от даты и user_id, поэтому не меняется в течение дня.
    Выбор идёт из общего пула (Таро + Отклик).
    """
    today = datetime.date.today().isoformat()  # 'YYYY-MM-DD'
    key = f"{today}:{user_id}".encode("utf-8")

    # Стабильный хэш (в отличие от hash(), который может меняться между запусками)
    digest = hashlib.sha256(key).hexdigest()
    idx = int(digest[:8], 16) % len(DAY_CARDS)

    return DAY_CARDS[idx]


async def send_one_card(message: types.Message, card: dict, prefix: str = ""):
    """
    card формат:
    { "title": "...", "image": "...", "description": "..." }
    картинки лежат в папке cards/
    """
    caption = f"{prefix}{card['title']}\n\n{card['description']}"
    photo = FSInputFile(f"cards/{card['image']}")
    await message.answer_photo(photo=photo, caption=caption)


@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Я здесь, чтобы мягко подсветить важное.",
        reply_markup=persistent_keyboard()
    )
    await message.answer("Выбери формат кнопкой ниже 🌿")


# --- ReplyKeyboard handlers (кнопки всегда видны) ---

@dp.message(lambda m: m.text == "🌿 Карта дня")
async def day_card_text(message: types.Message, state: FSMContext):
    await state.clear()
    card = stable_day_card_for_user(message.from_user.id)

    await message.answer("Твоя карта дня уже выбрана. Дай себе мгновение тишины…")
    await asyncio.sleep(0.9)

    await send_one_card(message, card, prefix="🌿 ")


@dp.message(lambda m: m.text == "🫧 Карта отклика")
async def mind_card_text(message: types.Message, state: FSMContext):
    await state.clear()
    card = random.choice(MIND_CARDS)

    await message.answer("Хорошо. Позволь образу прийти мягко…")
    await asyncio.sleep(0.9)

    await send_one_card(message, card, prefix="🫧 ")


@dp.message(lambda m: m.text == "🔮 Ответ на вопрос")
async def tarot_question_text(message: types.Message, state: FSMContext):
    await state.set_state(Flow.waiting_tarot_question)
    await message.answer(
        "Сформулируй вопрос и отправь его.\n"
        "Я вытащу одну карту Таро 🔮"
    )


# --- FSM: ждём вопрос после кнопки ---

@dp.message(Flow.waiting_tarot_question)
async def handle_tarot_question(message: types.Message, state: FSMContext):
    # если вдруг человек нажал кнопки вместо вопроса — мягко перенаправляем
    if message.text in ("🌿 Карта дня", "🔮 Ответ на вопрос", "🫧 Карта отклика"):
        await message.answer("Сначала пришли текст вопроса 🌿")
        return

    await message.answer(
        "Я услышал(а) твой вопрос.\n"
        "Позволь на мгновение остановиться…"
    )
    await asyncio.sleep(1.0)

    card = random.choice(TAROT_CARDS)
    await send_one_card(message, card, prefix="🔮 ")

    await state.clear()


@dp.message()
async def fallback(message: types.Message):
    # На команды реагировать не мешаем
    if message.text and message.text.startswith("/"):
        await message.answer("Нажми кнопку снизу, чтобы продолжить 🌿", reply_markup=persistent_keyboard())
        return

    await message.answer(
        "Нажми кнопку снизу: 🌿 Карта дня / 🔮 Ответ на вопрос / 🫧 Карта отклика",
        reply_markup=persistent_keyboard()
    )


async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
