import os
import json
import random
import hashlib
import datetime
import asyncio
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
# Настройки
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN (или TOKEN) в переменных окружения Railway.")

CARDS_JSON = "cards.json"            # Таро
MIND_CARDS_JSON = "mind_cards.json"  # Карты отклика / образы
IMAGES_DIR = "cards"                 # папка с картинками

QUESTIONS_WINDOW_SECONDS = 30 * 60   # 30 минут
QUESTIONS_LIMIT = 5                 # после 5 вопросов показать предложение
COOLDOWN_SECONDS = 6 * 60 * 60      # 6 часов охлаждение
PAUSE_BEFORE_MENU_SECONDS = 2       # пауза перед возвратом к меню


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
        # Лучше упасть сразу, чтобы было понятно, что не залит файл
        raise FileNotFoundError(f"Не найден файл: {path} (проверь, что он в корне репозитория рядом с bot.py)")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "cards" not in data or not isinstance(data["cards"], list):
        raise ValueError(f"{path} должен содержать ключ 'cards' со списком.")

    return data["cards"]


def pick_description(card: Dict[str, Any]) -> str:
    """
    Если есть descriptions (список) — выбираем рандомно.
    Иначе берём description.
    """
    variants = card.get("descriptions")
    if isinstance(variants, list) and variants:
        return random.choice(variants)
    return str(card.get("description", "")).strip()


# -----------------------------
# Клавиатуры
# -----------------------------
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌞 Карта дня")],
            [KeyboardButton(text="🔮 Ответ на вопрос")],
            [KeyboardButton(text="🫧 Карта отклика")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие…",
    )


def consult_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data="deep_yes"),
            InlineKeyboardButton(text="Не сейчас", callback_data="deep_no"),
        ]
    ])


# -----------------------------
# Стабильная карта дня
# -----------------------------
def stable_choice_for_user_today(user_id: int, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Стабильный выбор карты на день для конкретного пользователя.
    """
    today = datetime.date.today().isoformat()
    seed = f"{user_id}:{today}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    idx = int(h, 16) % len(cards)
    return cards[idx]


# -----------------------------
# Отправка карты
# -----------------------------
async def send_one_card(message: Message, card: Dict[str, Any], prefix: str = "") -> None:
    """
    Отправляет одну карту: фото + подпись.
    Ожидаем card["image"] как имя файла внутри папки cards/
    """
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
        await message.answer_photo(photo=photo, caption=caption, reply_markup=main_menu_keyboard())
    else:
        # если картинки нет — хотя бы текст
        await message.answer(
            (caption or "Карта выбрана, но файл изображения не найден 😅"),
            reply_markup=main_menu_keyboard(),
        )


# -----------------------------
# Трекинг вопросов для предложения консультации
# -----------------------------
# Храним последние timestamps вопросов (только для "Ответ на вопрос")
user_question_times: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
# Храним время, когда последний раз показали оффер (для cooldown)
user_offer_until: Dict[int, float] = defaultdict(lambda: 0.0)


def record_question_and_should_offer(user_id: int, now_ts: float) -> bool:
    """
    Записываем вопрос и проверяем: надо ли показать предложение консультации.
    - показываем после 5 вопросов за 30 минут
    - только если не на cooldown
    """
    # cooldown check
    if now_ts < user_offer_until[user_id]:
        # даже если много вопросов — молчим до конца охлаждения
        user_question_times[user_id].append(now_ts)
        return False

    dq = user_question_times[user_id]
    dq.append(now_ts)

    # выкинуть всё старше окна
    cutoff = now_ts - QUESTIONS_WINDOW_SECONDS
    while dq and dq[0] < cutoff:
        dq.popleft()

    if len(dq) >= QUESTIONS_LIMIT:
        # ставим cooldown и сбрасываем очередь (чтобы снова не сработало мгновенно)
        user_offer_until[user_id] = now_ts + COOLDOWN_SECONDS
        dq.clear()
        return True

    return False


# -----------------------------
# Роутер / Диспетчер
# -----------------------------
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет 🤍\n\n"
        "Я могу дать тебе одну карту — бережно и поддерживающе.\n"
        "Выбери следующий шаг:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "🌞 Карта дня")
async def day_card(message: Message, state: FSMContext):
    await state.clear()

    # объединённая колода: tarot + mind, но карта одна
    all_cards = TAROT_CARDS + MIND_CARDS
    if not all_cards:
        await message.answer("Пока нет ни одной карты в колодах 🥺", reply_markup=main_menu_keyboard())
        return

    card = stable_choice_for_user_today(message.from_user.id, all_cards)
    await send_one_card(message, card, prefix="🌞 ")


@router.message(F.text == "🫧 Карта отклика")
async def mind_card(message: Message, state: FSMContext):
    await state.clear()

    if not MIND_CARDS:
        await message.answer("🫧 Колода отклика пока наполняется. Загляни чуть позже 🤍", reply_markup=main_menu_keyboard())
        return

    card = random.choice(MIND_CARDS)
    await send_one_card(message, card, prefix="🫧 ")


@router.message(F.text == "🔮 Ответ на вопрос")
async def ask_question_start(message: Message, state: FSMContext):
    await state.set_state(AskQuestion.waiting_for_question)
    await message.answer(
        "🔮 Напиши свой вопрос одним сообщением.\n\n"
        "Я достану одну карту Таро и дам бережное описание.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(AskQuestion.waiting_for_question)
async def answer_question(message: Message, state: FSMContext):
    # фиксируем факт вопроса для оффера (именно тут)
    now_ts = datetime.datetime.now().timestamp()
    should_offer = record_question_and_should_offer(message.from_user.id, now_ts)

    await state.clear()

    if not TAROT_CARDS:
        await message.answer("Похоже, колода Таро пока пустая 🥺", reply_markup=main_menu_keyboard())
        return

    # карта Таро
    tarot_card = random.choice(TAROT_CARDS)
    await send_one_card(message, tarot_card, prefix="🔮 ")

    # (по твоей логике) здесь НЕ добавляем карту отклика — только Tarot

    # показать оффер при условии
    if should_offer:
        await message.answer(
            "Хочешь разобрать свои вопросы глубже через личную консультацию? 💬\n\n"
            "Мы можем посмотреть ситуацию внимательно и бережно.",
            reply_markup=consult_keyboard(),
        )


# -----------------------------
# Кнопки консультации
# -----------------------------
@router.callback_query(F.data == "deep_yes")
async def deep_yes(callback: CallbackQuery):
    # Тут пока без ссылки/ника — ты позже решишь, где лучше: bio или кнопка со ссылкой.
    await callback.message.answer(
        "Хорошо 🤍\n\n"
        "Напиши мне в личные сообщения, и мы спокойно разберём твой вопрос глубже.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "deep_no")
async def deep_no(callback: CallbackQuery):
    await callback.message.answer("Хорошо 🌿")
    await callback.answer()

    # Пауза и возврат к меню
    await asyncio.sleep(PAUSE_BEFORE_MENU_SECONDS)
    await callback.message.answer("Выбери следующий шаг:", reply_markup=main_menu_keyboard())


# -----------------------------
# Запуск
# -----------------------------
async def main():
    global TAROT_CARDS, MIND_CARDS

    # грузим колоды
    TAROT_CARDS = load_cards(CARDS_JSON)
    MIND_CARDS = load_cards(MIND_CARDS_JSON) if os.path.exists(MIND_CARDS_JSON) else []

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
