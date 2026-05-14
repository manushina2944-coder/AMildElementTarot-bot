import os
import json
import random
import asyncio
import logging
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types.input_file import FSInputFile


# -----------------------------
# Логирование
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------------
# Настройки
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN или TOKEN")

CARDS_JSON = "cards.json"
IMAGES_DIR = "cards"


# -----------------------------
# Глобальная колода
# -----------------------------
TAROT_CARDS: List[Dict[str, Any]] = []


# -----------------------------
# Загрузка колоды
# -----------------------------
def load_cards(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Не найден файл {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "cards" not in data or not isinstance(data["cards"], list):
        raise ValueError(f"{path} должен содержать ключ 'cards'")

    return data["cards"]


def pick_description(card: Dict[str, Any]) -> str:
    variants = card.get("descriptions")

    if isinstance(variants, list) and variants:
        return random.choice(variants)

    return str(card.get("description", "")).strip()


# -----------------------------
# Клавиатура
# -----------------------------
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔮 Карта из Колоды Мягкой Стихии")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Прикоснись к Полю…",
    )


MAIN_MENU = main_menu_keyboard()


# -----------------------------
# Сообщение тихого Поля
# -----------------------------
def field_is_quiet_text() -> str:
    return "Сегодня Поле молчит чуть тише обычного 🤍"


# -----------------------------
# Отправка карты
# -----------------------------
async def send_one_card(
    message: Message,
    card: Dict[str, Any],
) -> None:

    name = str(card.get("name", "")).strip()
    image = str(card.get("image", "")).strip()
    text = pick_description(card)

    caption_parts = []

    if name:
        caption_parts.append(f"<b>{name}</b>")

    if text:
        caption_parts.append(text)

    caption = "\n\n".join(caption_parts).strip()

    photo_path = os.path.join(IMAGES_DIR, image)

    if image and os.path.exists(photo_path):
        await message.answer_photo(
            photo=FSInputFile(photo_path),
            caption=caption,
            reply_markup=MAIN_MENU,
        )
    else:
        await message.answer(
            caption or field_is_quiet_text(),
            reply_markup=MAIN_MENU,
        )


# -----------------------------
# Router
# -----------------------------
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(None)

    await message.answer(
        "Привет 🤍\n\n"
        "Это пространство Колоды Мягкой Стихии.\n\n"
        "Ты можешь вытянуть карту\n"
        "и побыть рядом с тем образом,\n"
        "который откликнется сегодня.",
        reply_markup=MAIN_MENU,
    )


@router.message(F.text == "🔮 Карта из Колоды Мягкой Стихии")
async def draw_card(message: Message, state: FSMContext):
    await state.set_state(None)

    if not TAROT_CARDS:
        await message.answer(
            field_is_quiet_text(),
            reply_markup=MAIN_MENU,
        )
        return

    card = random.choice(TAROT_CARDS)

    await send_one_card(message, card)

    await message.answer(
        "Поле открыто 🌿",
        reply_markup=MAIN_MENU,
    )


# -----------------------------
# Health server для Railway
# -----------------------------
async def _handle_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
):
    try:
        await reader.readline()

        while True:
            line = await reader.readline()

            if not line or line in (b"\r\n", b"\n"):
                break

        body = b"OK"

        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )

        await writer.drain()

    finally:
        writer.close()

        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server() -> Optional[asyncio.AbstractServer]:
    port = os.getenv("PORT")

    if not port:
        logger.info("PORT не задан — health server не запущен")
        return None

    server = await asyncio.start_server(
        _handle_http,
        "0.0.0.0",
        int(port),
    )

    logger.info("Health server listening on %s", port)

    return server


# -----------------------------
# main
# -----------------------------
async def main():
    global TAROT_CARDS

    try:
        TAROT_CARDS = load_cards(CARDS_JSON)
        logger.info("Loaded TAROT_CARDS: %d", len(TAROT_CARDS))

    except Exception:
        TAROT_CARDS = []
        logger.exception("Failed to load TAROT_CARDS")

    bot = Bot(
        TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(router)

    health = await start_health_server()

    try:
        logger.info("Bot started. Polling…")

        await dp.start_polling(bot)

    finally:
        if health:
            health.close()
            await health.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
