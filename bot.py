import os
import json
import random
import hashlib
import datetime
import asyncio
import logging
from zoneinfo import ZoneInfo
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
    raise RuntimeError("Не найден BOT_TOKEN (или TOKEN)")

CARDS_JSON = "cards.json"
MIND_CARDS_JSON = "mind_cards.json"
IMAGES_DIR = "cards"

QUESTIONS_WINDOW_SECONDS = 30 * 60
QUESTIONS_LIMIT = 5
COOLDOWN_SECONDS = 6 * 60 * 60
PAUSE_BEFORE_MENU_SECONDS = 2

USER_TZ = ZoneInfo("Europe/Amsterdam")


# -----------------------------
# Глобальные колоды
# -----------------------------
TAROT_CARDS: List[Dict[str, Any]] = []
MIND_CARDS: List[Dict[str, Any]] = []


# -----------------------------
# FSM
# -----------------------------
class AskQuestion(StatesGroup):
    waiting_for_question = State()


# -----------------------------
# Загрузка колод
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
# Клавиатуры
# -----------------------------
def _build_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Карта дня")],
            [KeyboardButton(text="🔮 Ответ на вопрос")],
            [KeyboardButton(text="🫧 Карта отклика")],
        ],
        resize_keyboard=True,
    )


def _build_consult_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да 🌙", callback_data="deep_yes"),
                InlineKeyboardButton(text="Не сейчас", callback_data="deep_no"),
            ]
        ]
    )


MAIN_MENU = _build_main_menu_keyboard()
CONSULT_KB = _build_consult_keyboard()


# -----------------------------
# Карта дня
# -----------------------------
def stable_choice_for_user_today(user_id: int, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    today = datetime.datetime.now(USER_TZ).date().isoformat()
    seed = f"{user_id}:{today}"
    h = hashlib.sha256(seed.encode()).hexdigest()
    return cards[int(h, 16) % len(cards)]


# -----------------------------
# Отправка карты
# -----------------------------
async def send_one_card(message: Message, card: Dict[str, Any], prefix: str = ""):
    name = card.get("name", "")
    image = card.get("image", "")
    text = pick_description(card)

    caption = "\n\n".join(p for p in [f"{prefix}<b>{name}</b>" if name else "", text] if p)

    path = os.path.join(IMAGES_DIR, image)
    if image and os.path.exists(path):
        await message.answer_photo(
            photo=FSInputFile(path),
            caption=caption,
            reply_markup=MAIN_MENU,
        )
    else:
        await message.answer(caption or "Карта без изображения 🤍", reply_markup=MAIN_MENU)


# -----------------------------
# Счётчик вопросов
# -----------------------------
user_question_times: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
user_offer_until: Dict[int, float] = defaultdict(float)


def record_question_and_should_offer(user_id: int, now_ts: float) -> bool:
    if now_ts < user_offer_until[user_id]:
        user_question_times[user_id].append(now_ts)
        return False

    dq = user_question_times[user_id]
    dq.append(now_ts)

    cutoff = now_ts - QUESTIONS_WINDOW_SECONDS
    while dq and dq[0] < cutoff:
        dq.popleft()

    if len(dq) >= QUESTIONS_LIMIT:
        user_offer_until[user_id] = now_ts + COOLDOWN_SECONDS
        dq.clear()
        return True

    return False


# -----------------------------
# Router
# -----------------------------
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Важно: НЕ state.clear(), чтобы не стирать data в FSM
    await state.set_state(None)
    await message.answer("Привет 🤍\n\nРад тебя видеть.\n" "Что ты хочешь спросить у Карт Мягкой Стихии?", reply_markup=MAIN_MENU)


@router.message(F.text == "🌿 Карта дня")
async def day_card(message: Message, state: FSMContext):
    await state.set_state(None)

    cards = TAROT_CARDS + MIND_CARDS
    if not cards:
        await message.answer("Колоды пусты 🥺", reply_markup=MAIN_MENU)
        return

    await send_one_card(message, stable_choice_for_user_today(message.from_user.id, cards), "🌿 ")
    await message.answer("Хочешь ещё поговорить с Картами?", reply_markup=MAIN_MENU)


@router.message(F.text == "🫧 Карта отклика")
async def mind_card(message: Message, state: FSMContext):
    await state.set_state(None)

    if not MIND_CARDS:
        await message.answer("Колода отклика пустая 🤍", reply_markup=MAIN_MENU)
        return

    await send_one_card(message, random.choice(MIND_CARDS), "🫧 ")
    await message.answer("Выбери следующий шаг:", reply_markup=MAIN_MENU)


@router.message(F.text == "🔮 Ответ на вопрос")
async def ask_question_start(message: Message, state: FSMContext):
    data = await state.get_data()
    seen = data.get("seen_examples", False)

    await state.set_state(AskQuestion.waiting_for_question)

    if not seen:
        await message.answer(
            "🔮 Напиши вопрос одним сообщением.\n\n"
            "Например:\n"
            "• Какой шаг будет верным на этой неделе?\n"
            "• Что мне важно знать о наших отношениях?\n"
            "• Как мне лучше действовать сейчас?\n\n"
            "Я дам ответ через одну карту 🤍",
            reply_markup=MAIN_MENU,
        )
        await state.update_data(seen_examples=True)
    else:
        await message.answer("🔮 Напиши свой вопрос 🤍", reply_markup=MAIN_MENU)


@router.message(AskQuestion.waiting_for_question)
async def answer_question(message: Message, state: FSMContext):
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    offer = record_question_and_should_offer(message.from_user.id, now_ts)

    # Важно: НЕ state.clear(), чтобы не стирать seen_examples
    await state.set_state(None)

    if not TAROT_CARDS:
        await message.answer("Колода Таро не подключена 🥺", reply_markup=MAIN_MENU)
        return

    await send_one_card(message, random.choice(TAROT_CARDS), "🔮 ")

    if offer:
        await message.answer(
            "Хочешь разобрать ситуацию глубже через личную консультацию? 💬",
            reply_markup=CONSULT_KB,
        )
    else:
        await message.answer("Выбери следующий шаг:", reply_markup=MAIN_MENU)


@router.callback_query(F.data == "deep_yes")
async def deep_yes(callback: CallbackQuery):
    await callback.message.answer(
        "Хорошо 🤍 Напиши мне в личные сообщения @olga_febr",
        reply_markup=MAIN_MENU,
    )
    await callback.answer()


@router.callback_query(F.data == "deep_no")
async def deep_no(callback: CallbackQuery):
    await callback.message.answer("Хорошо 🌿")
    await callback.answer()
    await asyncio.sleep(PAUSE_BEFORE_MENU_SECONDS)
    await callback.message.answer("Выбери следующий шаг:", reply_markup=MAIN_MENU)


# -----------------------------
# Health server для Railway
# -----------------------------
async def _handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
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
        logger.info("PORT не задан — health-сервер не запущен")
        return None
    server = await asyncio.start_server(_handle_http, "0.0.0.0", int(port))
    logger.info("Health server listening on %s", port)
    return server


# -----------------------------
# main
# -----------------------------
async def main():
    global TAROT_CARDS, MIND_CARDS

    try:
        TAROT_CARDS = load_cards(CARDS_JSON)
        logger.info("Loaded TAROT_CARDS: %d", len(TAROT_CARDS))
    except Exception:
        TAROT_CARDS = []
        logger.exception("Failed to load TAROT_CARDS")

    try:
        if os.path.exists(MIND_CARDS_JSON):
            MIND_CARDS = load_cards(MIND_CARDS_JSON)
            logger.info("Loaded MIND_CARDS: %d", len(MIND_CARDS))
    except Exception:
        MIND_CARDS = []
        logger.exception("Failed to load MIND_CARDS")

    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
