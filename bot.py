import asyncio
import json
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

with open("cards.json", "r", encoding="utf-8") as f:
    CARDS = json.load(f)["cards"]


def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌿 Карта дня", callback_data="day_card")],
            [InlineKeyboardButton(text="🔮 Ответ на вопрос", callback_data="question")]
        ]
    )


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Я здесь, чтобы мягко подсветить важное.\n\nВыбери формат:",
        reply_markup=main_keyboard()
    )


@dp.callback_query(lambda c: c.data == "day_card")
async def day_card(callback: types.CallbackQuery):
    card = random.choice(CARDS)
    await send_card(callback.message, card)
    await callback.answer()


@dp.callback_query(lambda c: c.data == "question")
async def ask_question(callback: types.CallbackQuery):
    await callback.message.answer(
        "Сформулируй вопрос и отправь его.\nЯ выберу одну карту, которая поможет посмотреть на ситуацию глубже 🌿"
    )
    await callback.answer()


@dp.message()
async def handle_question(message: types.Message):
    card = random.choice(CARDS)
    await send_card(message, card)


async def send_card(message, card):
    text = f"**{card['title']}**\n\n{card['description']}"
    with open(f"cards/{card['image']}", "rb") as img:
        await message.answer_photo(
            photo=img,
            caption=text,
            parse_mode="Markdown"
        )


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
