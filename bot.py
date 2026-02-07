import os
import json
import random
import hashlib
import datetime
import asyncio
import logging
from zoneinfo import ZoneInfo
from collections import defaultdict, deque
from typing import Any, Dict, List

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
# Логирование (Railway-friendly)
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------------
# Настройки
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN (или TOKEN) в переменных окружения Railway.")

CARDS_JSON = "cards.json"            # Таро
MIND_CARDS_JSON = "mind_cards.json"  # Карты отклика / образы
IMAGES_DIR = "cards"                 # папка с картинками

QUESTIONS_WINDOW_SECONDS = 30 * 60   # 30 минут
QUESTIONS_LIMIT = 5                  # после 5 вопросов показать предложение
COOLDOWN_SECONDS = 6 * 60 * 60       # 6 часов охлаждение
PAUSE_BEFORE_MENU_SECONDS = 2        # пауза перед возвратом к меню

# Часовой пояс для "карты дня"
USER_TZ = ZoneInfo("Europe/Amsterdam")


# -----------------------------
# Глобальные колоды (инициализируются в main)
# -----------------------------
TAROT_CARDS: List[Dict[str, Any]] = []
MIND_CARDS: List[Dict[str, Any]] = []


# -----------------------------
# FSM состояния
# -----------------------------
class AskQuestion(StatesGroup):
    waiting_for_question = State()


# -----------------------------
# Загрузка колод
# -----------------------------
def load_cards(path: str) -> List[Dict[str, Any]]:
    """
    Ожидаем формат:
    {
      "cards": [
        {
          "name": "...",
          "image": "file.jpg",
          "description": "...",
          "descriptions": ["вариант1", "вариант2"]   # опционально
        }
      ]
    }
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Не найден файл: {path} (проверь, что он в корне репозитория рядом с bot.py)"
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "cards" not in data or not isinstance(data["cards"], list):
        raise ValueError(f"{path} должен содержать ключ 'cards' со списком.")

    return data["cards"]


def pick_description(card: Dict[str, Any]) -> str:
    variants = card.get("descriptions")
    if isinstance(variants, list) and variants:
        return random.choice(variants)
    return str(card.get("description", "")).strip()


# -----------------------------
# Клавиатуры (кэшируем)
# -----------------------------
def _build_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Карта дня")],
            [KeyboardButton(text="🔮 Ответ на вопрос")],
            [KeyboardButton(text="🫧 Карта отклика")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие…",
    )


def _build_consult_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="deep_yes"),
                InlineKeyboardButton(text="Не сейчас", callback_data="deep_no"),
            ]
        ]
    )


MAIN_MENU = _build_main_menu_keyboard()
CONSULT_KB = _build_consult_keyboard()


# -----------------------------
# Стабильная карта дня
# -----------------------------
def stable_choice_for_user_today(user_id: int, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    today = datetime.datetime.now(USER_TZ).date().isoformat()
    seed = f"{user_id}:{today}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    idx = int(h, 16) % len(cards)
    return cards[idx]


# -----------------------------
# Отправка карты
# -----------------------------
async def send_one_card(message: Message, card: Dict[str, Any], prefix: str = "") -> None:
    name = str(card.get("name", "")).strip()
    image = str(card.get("image", "")).strip()
    text = pick_description(card)

    caption_parts = []
    if name:
        caption_parts.append(f"{prefix}<b>{name}</b>")
    if text:
        caption_parts.append(text)

    caption = "\n\n".join([p for p in caption_parts if p]).strip()
    photo_path = os.path.join(IMAGES_DIR, image)

    if image and os.path.exists(photo_path):
        photo = FSInputFile(photo_path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=MAIN_MENU)
    else:
        await message.answer(
            (caption or "Карта выбрана, но файл изображения не найден 😅"),
            reply_markup=MAIN_MENU,
        )


# -----------------------------
# Трекинг вопросов для предложения консультации
# -----------------------------
user_question_times: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
user_offer_until: Dict[int, float] = defaultdict(lambda: 0.0)


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
# Роутер
# -----------------------------
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет 🤍\n\n"
        "Я могу дать тебе одну карту — бережно и поддерживающе.\n"
        "Выбери следующий шаг:",
        reply_markup=MAIN_MENU,
    )


@router.message(F.text == "🌿 Карта дня")
async def day_card(message: Message, state: FSMContext):
    await state.clear()

    all_cards = TAROT_CARDS + MIND_CARDS
    if not all_cards:
        await message.answer("Пока нет ни одной карты в колодах 🥺", reply_markup=MAIN_MENU)
        return

    card = stable_choice_for_user_today(message.from_user.id, all_cards)
    await send_one_card(message, card, prefix="🌿 ")


@router.message(F.text == "🫧 Карта отклика")
async def mind_card(message: Message, state: FSMContext):
    await state.clear()

    if not MIND_CARDS:
        await message.answer(
            "🫧 Колода отклика пока наполняется. Загляни чуть позже 🤍",
            reply_markup=MAIN_MENU,
        )
        return

    card = random.choice(MIND_CARDS)
    await send_one_card(message, card, prefix="🫧 ")


@router.message(F.text == "🔮 Ответ на вопрос")
async def ask_question_start(message: Message, state: FSMContext):
    data = await state.get_data()
    seen_examples = data.get("seen_question_examples", False)

    await state.set_state(AskQuestion.waiting_for_question)

    if not seen_examples:
        await message.answer(
            "🔮 Напиши свой вопрос одним сообщением.\n\n"
            "Например:\n"
            "• Какой шаг будет самым верным на этой неделе?\n"
            "• На что мне стоит обратить внимание прямо сейчас?\n"
            "• Что мне важно знать о наших отношениях?\n"
            "• Как мне сейчас найти ясность?\n"
            "• Как мне лучше действовать в этой ситуации?\n\n"
            "Я достану одну карту Таро и дам бережное описание 🤍",
            reply_markup=MAIN_MENU,
        )
        await state.update_data(seen_question_examples=True)
    else:
        await message.answer(
            "🔮 Напиши свой вопрос одним сообщением 🤍",
            reply_markup=MAIN_MENU,
        )


@router.message(AskQuestion.waiting_for_question)
async def answer_question(message: Message, state: FSMContext):
    # ВАЖНО: никаких проверок длины — любое сообщение считаем вопросом
    now_ts = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
    should_offer = record_question_and_should_offer(message.from_user.id, now_ts)

    await state.clear()

    if not TAROT_CARDS:
        await message.answer(
            "Похоже, колода Таро пока не подключена 🥺\n"
            "Админ ещё не загрузил cards.json или карты.",
            reply_markup=MAIN_MENU,
        )
        return

    tarot_card = random.choice(TAROT_CARDS)
    await send_one_card(message, tarot_card, prefix="🔮 ")

    if should_offer:
        await message.answer(
            "Хочешь разобрать свои вопросы глубже через личную консультацию? 💬\n\n"
            "Мы можем посмотреть ситуацию внимательно и бережно.",
            reply_markup=CONSULT_KB,
        )


# -----------------------------
# Кнопки консультации
# -----------------------------
@router.callback_query(F.data == "deep_yes")
async def deep_yes(callback: Callback_query):  # <-- FIX below: wrong type name
    await callback.message.answer(
        "Хорошо 🤍\n\n"
        "Напиши мне в личные сообщения, и мы спокойно разберём твой вопрос глубже.",
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
# Запуск
# -----------------------------
async def main():
    global TAROT_CARDS, MIND_CARDS

    try:
        TAROT_CARDS = load_cards(CARDS_JSON)
        logger.info("Loaded TAROT_CARDS: %d", len(TAROT_CARDS))
    except Exception as e:
        TAROT_CARDS = []
        logger.exception("Failed to load %s: %s", CARDS_JSON, e)

    try:
        if os.path.exists(MIND_CARDS_JSON):
            MIND_CARDS = load_cards(MIND_CARDS_JSON)
            logger.info("Loaded MIND_CARDS: %d", len(MIND_CARDS))
        else:
            MIND_CARDS = []
            logger.info("%s not found, MIND_CARDS is empty (ok).", MIND_CARDS_JSON)
    except Exception as e:
        MIND_CARDS = []
        logger.exception("Failed to load %s: %s", MIND_CARDS_JSON, e)

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Bot started. Polling…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
