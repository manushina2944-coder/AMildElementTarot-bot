import asyncio
import json
import random
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

# Берём токен из переменных окружения Railway
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Загружаем все карты
with open("cards.json", "r", encoding="utf-8") as f:
    CARDS = json.load(f)["cards"]


# Клавиатура для главного меню
def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌿 Карта дня", callback_data="day_card")],
            [InlineKeyboardButton(text="🔮 Ответ на вопрос", callback_data="question")]
        ]
    )


# /start
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Я здесь, чтобы мягко подсветить важное.\n\nВыбери формат:",
        reply_markup=main_keyboard()
    )


# Кнопка "Карта дня"
@dp.callback_query(lambda c: c.data == "day_card")
async def day_card(callback: types.CallbackQuery):
    card = random.choice(CARDS)
    await send_card(callback.message, card)
    await callback.answer()


# Кнопка "Ответ на вопрос"
@dp.callback_query(lambda c: c.data == "question")
async def ask_question(callback: types.CallbackQuery):
    await callback.message.answer(
        "Сформулируй вопрос и отправь его.\nЯ выберу одну карту, которая поможет посмотреть на ситуацию глубже 🌿"
    )
    await callback.answer()


# Обработка любого текста как вопрос
@dp.message()
async def handle_question(message: types.Message):
    # Игнорируем команды
    if message.text.startswith("/"):
        return
    card = random.choice(CARDS)
    await send_card(message, card)


# Функция отправки карты
async def send_card(message, card):
    text = f"{card['title']}\n\n{card['description']}"
    # Создаём InputFile корректно для aiogram 3.4+
    photo = FSInputFile(path=f"cards/{card['image']}")
    await message.answer_photo(
        photo=photo,
        caption=text
    )


# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
