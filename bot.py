import asyncio
import logging
import os

import httpx
from urllib.parse import urlencode
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from db import (
    get_free_used,
    grant_paid,
    increment_free_used,
    init_db,
    is_paid_active,
    set_channel_member,
    upsert_user,
)

# Support .env files saved as UTF-8 with BOM (common on Windows editors).
load_dotenv(encoding="utf-8-sig")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
FREE_REQUESTS_LIMIT = int(os.getenv("FREE_REQUESTS_LIMIT", "4"))
PRICE_RUB = int(os.getenv("PRICE_RUB", "300"))
STARS_PRICE = int(os.getenv("STARS_PRICE", "300"))
PAID_DAYS = int(os.getenv("PAID_DAYS", "30"))

YOOMONEY_RECEIVER = os.getenv("YOOMONEY_RECEIVER", "")
YOOMONEY_TARGETS = os.getenv("YOOMONEY_TARGETS", "Подписка на 30 дней")
YOOMONEY_PAYMENT_TYPE = os.getenv("YOOMONEY_PAYMENT_TYPE", "AC")
YOOMONEY_QUICKPAY_FORM = os.getenv("YOOMONEY_QUICKPAY_FORM", "shop")
YOOMONEY_SUCCESS_URL = os.getenv("YOOMONEY_SUCCESS_URL", "")

MINI_APP_URL = os.getenv("MINI_APP_URL", "")
MINI_APP_API_URL = os.getenv("MINI_APP_API_URL", "")
MINI_APP_API_KEY = os.getenv("MINI_APP_API_KEY", "")
STUB_MODE = os.getenv("STUB_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "")  # example: @my_channel
REQUIRED_CHANNEL_URL = os.getenv("REQUIRED_CHANNEL_URL", "")  # example: https://t.me/my_channel

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "Ты - профессиональный кодер. Пишешь код и кратко поясняешь структуру и как запустить/установить.",
)
UNIVERSAL_SYSTEM_PROMPT = os.getenv(
    "UNIVERSAL_SYSTEM_PROMPT",
    "Ты универсальный ассистент. Отвечай подробно и полезно. Никогда не пиши код.",
)

CODE_MODELS = [
    "claude-sonnet-4-5",
]
PROMPT_HELP_MODEL = "claude-haiku-4-5"
IDEAS_MODEL = "claude-haiku-4-5"
FALLBACK_MODEL = "claude-haiku-4-5"

LANGUAGES = [
    ("python", "Python"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("csharp", "C#"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("java", "Java"),
    ("cpp", "C++"),
    ("sql", "SQL"),
    ("lua", "Lua"),
]


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def validate_env() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в .env")
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Не задан ANTHROPIC_API_KEY в .env")


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("💻 Код"), KeyboardButton("❓ Помощь")],
            [KeyboardButton("💡 Идеи"), KeyboardButton("🧠 Универсал")],
            [KeyboardButton("💳 Подписка")],
            [KeyboardButton("❌ Отмена")],
        ],
        resize_keyboard=True,
    )


def _get_lang_title(lang_key: str | None) -> str:
    if not lang_key:
        return "не выбран"
    for key, title in LANGUAGES:
        if key == lang_key:
            return title
    return lang_key


def split_for_telegram(text: str, max_len: int = 3500) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), max_len):
                chunks.append(line[i : i + max_len])
            continue
        if len(current) + len(line) > max_len:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


async def safe_reply_text(message, text: str, reply_markup=None, parse_mode=None) -> None:
    parts = split_for_telegram(text)
    for idx, part in enumerate(parts):
        if idx == 0:
            await message.reply_text(part, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.reply_text(part, parse_mode=parse_mode)


async def safe_reply_code(message, code_text: str, lang: str = "") -> None:
    clean = (code_text or "").strip()
    if clean.startswith("```"):
        clean = clean.strip("`")
    clean = clean.replace("```", "`\u200b``")
    for part in split_for_telegram(clean, max_len=3200):
        body = f"```{lang}\n{part}\n```" if lang else f"```\n{part}\n```"
        await message.reply_text(body, parse_mode="Markdown")


def channel_url() -> str:
    if REQUIRED_CHANNEL_URL:
        return REQUIRED_CHANNEL_URL
    if REQUIRED_CHANNEL.startswith("@"):
        return f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    return "https://t.me"


def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Подписаться", url=channel_url())]]
    )


def build_mini_app_url(response_id: str) -> str | None:
    if not MINI_APP_URL:
        return None
    base = MINI_APP_URL.rstrip("/")
    return f"{base}/?id={response_id}"


def build_mini_app_keyboard(response_id: str) -> InlineKeyboardMarkup | None:
    url = build_mini_app_url(response_id)
    if not url:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Открыть мини-приложение", web_app=WebAppInfo(url=url))]]
    )


def _api_base() -> str:
    return MINI_APP_API_URL.strip().rstrip("/")


async def api_post(path: str, payload: dict) -> dict:
    base = _api_base()
    if not base:
        raise RuntimeError("MINI_APP_API_URL is not set")
    headers = {}
    if MINI_APP_API_KEY:
        headers["X-API-Key"] = MINI_APP_API_KEY
    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(3):
            try:
                resp = await client.post(f"{base}{path}", json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ReadTimeout, httpx.TimeoutException):
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise


async def api_store_response(user_id: int | None, content: str) -> str:
    data = await api_post(
        "/api/response",
        {"user_id": user_id, "content": content},
    )
    return data.get("id", "")


def build_yoomoney_url(user_id: int) -> str | None:
    if not YOOMONEY_RECEIVER:
        return None
    params = {
        "receiver": YOOMONEY_RECEIVER,
        "quickpay-form": YOOMONEY_QUICKPAY_FORM,
        "targets": YOOMONEY_TARGETS,
        "sum": str(PRICE_RUB),
        "label": f"sub_{user_id}",
        "paymentType": YOOMONEY_PAYMENT_TYPE,
    }
    if YOOMONEY_SUCCESS_URL:
        params["successURL"] = YOOMONEY_SUCCESS_URL
    return "https://yoomoney.ru/quickpay/confirm.xml?" + urlencode(params)


def build_paywall_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("Оплатить Telegram Stars", callback_data="pay:stars")])
    yoomoney_url = build_yoomoney_url(user_id)
    if yoomoney_url:
        rows.append([InlineKeyboardButton("Оплатить YooMoney", url=yoomoney_url)])
    return InlineKeyboardMarkup(rows)


async def ensure_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return False

    upsert_user(user.id, user.username, user.first_name, user.last_name)

    # Future paid access (stars/subscription) can bypass channel membership.
    if is_paid_active(user.id):
        return True

    if not REQUIRED_CHANNEL:
        return True

    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        is_member = member.status in {"member", "administrator", "creator"}
        set_channel_member(user.id, is_member)
        if is_member:
            return True
    except BadRequest:
        logger.exception("Failed to check channel membership")

    await message.reply_text(
        "Доступ только для подписчиков канала.\n"
        "Подпишись и снова отправь команду.",
        reply_markup=subscribe_keyboard(),
    )
    return False


def build_code_settings_text(user_data: dict) -> str:
    model = user_data.get("selected_model", ANTHROPIC_MODEL)
    language = _get_lang_title(user_data.get("selected_language"))
    return (
        "Настройки режима /code\n\n"
        f"Модель: {model}\n"
        f"Язык: {language}\n\n"
        "1) Выбери модель\n"
        "2) Выбери язык кнопкой\n"
        "3) Напиши задачу текстом\n"
        "или нажми «Помоги с промтом», опиши идею и бот сам соберет промт и сразу сгенерирует код."
    )


def build_code_settings_keyboard(user_data: dict) -> InlineKeyboardMarkup:
    selected_model = user_data.get("selected_model", ANTHROPIC_MODEL)
    selected_language = user_data.get("selected_language")

    rows: list[list[InlineKeyboardButton]] = []

    model_row: list[InlineKeyboardButton] = []
    for model in CODE_MODELS:
        title = f"{model} {'✅' if model == selected_model else ''}".strip()
        model_row.append(InlineKeyboardButton(title, callback_data=f"model:{model}"))
        if len(model_row) == 2:
            rows.append(model_row)
            model_row = []
    if model_row:
        rows.append(model_row)

    language_row: list[InlineKeyboardButton] = []
    for key, title in LANGUAGES:
        text = f"{title} {'✅' if key == selected_language else ''}".strip()
        language_row.append(InlineKeyboardButton(text, callback_data=f"lang:{key}"))
        if len(language_row) == 3:
            rows.append(language_row)
            language_row = []
    if language_row:
        rows.append(language_row)

    rows.append([InlineKeyboardButton("Помоги с промтом", callback_data="code:help_prompt")])
    rows.append([InlineKeyboardButton("Обновить настройки", callback_data="code:refresh")])
    return InlineKeyboardMarkup(rows)


def build_universal_settings_text() -> str:
    return (
        "Режим /universal\n\n"
        "1) Опиши задачу текстом\n"
        "2) Или нажми «Помоги с промтом» и я соберу четкий запрос\n\n"
        "Важно: в этом режиме я не пишу код, только текстовые ответы."
    )


def build_universal_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Помоги с промтом", callback_data="universal:help_prompt")]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        upsert_user(user.id, user.username, user.first_name, user.last_name)

    context.user_data["code_mode"] = False
    context.user_data["awaiting_prompt_help"] = False
    context.user_data["universal_mode"] = False
    context.user_data["awaiting_universal_prompt_help"] = False
    context.user_data["selected_model"] = ANTHROPIC_MODEL
    context.user_data["selected_language"] = None

    text = (
        "👋 Привет!\n\n"
        f"🤖 Я AI-бот для генерации кода. Базовая модель: {ANTHROPIC_MODEL}.\n\n"
        f"🆓 Бесплатно: {FREE_REQUESTS_LIMIT} запрос(а), затем подписка {PRICE_RUB} руб/мес.\n\n"
        "🧩 Что умею:\n"
        "• /code — режим с кнопками: выбор модели и языка\n"
        "• /universal — универсальный режим без кода\n"
        "• /ideas — идеи задач от ИИ\n"
        "• /subscribe — подписка\n"
        "• /help — подсказки\n"
        "• /cancel — сброс режима /code"
    )
    await update.message.reply_text(text, reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Команды:\n"
        "• /start — приветствие и сброс\n"
        "• /code — мини-настройки модели и языка\n"
        "• /universal — универсальный режим без кода\n"
        "• /ideas — 5 идей\n"
        "• /subscribe — подписка\n"
        "• /cancel — выйти из режима /code\n\n"
        "Доступ к ИИ только после подписки на канал."
    )
    await update.message.reply_text(text, reply_markup=main_keyboard())


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text(
        f"Подписка: {PRICE_RUB} руб/мес или Telegram Stars.\nВыбери способ оплаты:",
        reply_markup=build_paywall_keyboard(user.id),
    )


async def send_stars_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    try:
        await context.bot.send_invoice(
            chat_id=user.id,
            title="Подписка на 30 дней",
            description=f"Доступ к боту на {PAID_DAYS} дней.",
            payload=f"stars:{user.id}",
            currency="XTR",
            prices=[LabeledPrice("Подписка", STARS_PRICE)],
            provider_token="",
        )
    except Exception:
        logger.exception("Failed to send Stars invoice")
        await update.effective_message.reply_text("Не удалось создать счет. Попробуй позже.")


async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.data == "pay:stars":
        await send_stars_invoice(update, context)


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return
    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.successful_payment:
        return
    user = update.effective_user
    if not user:
        return
    try:
        grant_paid(user.id, PAID_DAYS)
        await update.message.reply_text("Оплата получена. Подписка активирована ✅")
    except Exception:
        logger.exception("Failed to activate paid access")
        await update.message.reply_text("Оплата прошла, но активация не удалась. Напиши в поддержку.")


async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return

    context.user_data["code_mode"] = True
    context.user_data["awaiting_prompt_help"] = False
    context.user_data["universal_mode"] = False
    context.user_data["awaiting_universal_prompt_help"] = False
    context.user_data.setdefault("selected_model", ANTHROPIC_MODEL)

    await update.message.reply_text(
        build_code_settings_text(context.user_data),
        reply_markup=build_code_settings_keyboard(context.user_data),
    )


async def universal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return

    context.user_data["code_mode"] = False
    context.user_data["awaiting_prompt_help"] = False
    context.user_data["universal_mode"] = True
    context.user_data["awaiting_universal_prompt_help"] = False

    await update.message.reply_text(
        build_universal_settings_text(),
        reply_markup=build_universal_settings_keyboard(),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["code_mode"] = False
    context.user_data["awaiting_prompt_help"] = False
    context.user_data["universal_mode"] = False
    context.user_data["awaiting_universal_prompt_help"] = False
    context.user_data["selected_language"] = None
    context.user_data["selected_model"] = ANTHROPIC_MODEL
    await update.message.reply_text("Режим /code выключен.", reply_markup=main_keyboard())


async def ideas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return

    prompt = (
        "Дай 5 коротких идей запросов для coding AI-бота в Telegram. "
        "Формат: нумерованный список, каждый пункт в одну строку, на русском."
    )
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        ideas = await ask_anthropic_ai(prompt, model=IDEAS_MODEL, max_tokens=400)
    except Exception:
        logger.exception("Ideas generation error")
        ideas = (
            "1. Напиши Telegram-бота с командой /weather\n"
            "2. Сделай REST API на FastAPI с JWT-авторизацией\n"
            "3. Создай парсер сайта с сохранением в CSV\n"
            "4. Напиши unit-тесты для валидации email\n"
            "5. Отрефакторь этот код и объясни изменения"
        )
    await update.message.reply_text(ideas, reply_markup=main_keyboard())


async def ask_anthropic_ai(
    user_text: str,
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.7,
    use_fallback: bool = True,
    max_tokens: int = 2000,
) -> str:
    base = ANTHROPIC_BASE_URL.strip().rstrip("/")
    url = f"{base}/v1/messages"

    headers = {
        "x-api-key": ANTHROPIC_API_KEY or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    chosen_model = model or ANTHROPIC_MODEL
    payload = {
        "model": chosen_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt or SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_text}],
    }

    timeout = httpx.Timeout(90.0, connect=15.0)
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(3):
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {429, 529} and attempt < 2:
                    await asyncio.sleep(2.5 * (attempt + 1))
                    continue
                if (
                    use_fallback
                    and status in {400, 404, 422, 500, 502, 503, 504}
                    and chosen_model != FALLBACK_MODEL
                    and attempt == 0
                ):
                    logger.warning(
                        "Model '%s' failed with %s, retrying with fallback '%s'",
                        chosen_model,
                        status,
                        FALLBACK_MODEL,
                    )
                    payload["model"] = FALLBACK_MODEL
                    chosen_model = FALLBACK_MODEL
                    await asyncio.sleep(0.8)
                    continue
                if status >= 500 and attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise
        else:
            if last_error:
                raise last_error
            raise RuntimeError("Не удалось выполнить запрос к модели.")

    blocks = data.get("content", [])
    if isinstance(blocks, list):
        text_parts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        merged = "\n".join(part for part in text_parts if part).strip()
        return merged or "Модель не вернула ответ."
    return "Модель не вернула ответ."


async def code_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    fake_update = Update(update.update_id, message=query.message)
    fake_update._effective_user = query.from_user
    if not await ensure_access(fake_update, context):
        return

    data = query.data or ""

    if data.startswith("model:"):
        picked = data.split(":", 1)[1]
        if picked in CODE_MODELS:
            context.user_data["selected_model"] = picked
    elif data.startswith("lang:"):
        picked = data.split(":", 1)[1]
        if any(key == picked for key, _ in LANGUAGES):
            context.user_data["selected_language"] = picked
    elif data == "code:help_prompt":
        if not context.user_data.get("selected_language"):
            await query.message.reply_text("Сначала выбери язык кнопкой в настройках /code.")
            return
        context.user_data["code_mode"] = True
        context.user_data["awaiting_prompt_help"] = True
        context.user_data["universal_mode"] = False
        context.user_data["awaiting_universal_prompt_help"] = False
        await query.message.reply_text(
            "Опиши, что тебе нужно в свободной форме.\n"
            "Я соберу идеальный промт и сразу запущу генерацию кода."
        )
        return
    elif data == "universal:help_prompt":
        context.user_data["code_mode"] = False
        context.user_data["awaiting_prompt_help"] = False
        context.user_data["universal_mode"] = True
        context.user_data["awaiting_universal_prompt_help"] = True
        await query.message.reply_text(
            "Опиши, что тебе нужно в свободной форме.\n"
            "Я соберу четкий промт и сразу отвечу."
        )
        return

    await query.edit_message_text(
        build_code_settings_text(context.user_data),
        reply_markup=build_code_settings_keyboard(context.user_data),
    )


def build_code_generation_prompt(task: str, language_title: str) -> str:
    return (
        "Сгенерируй ПОЛНЫЙ и рабочий код по задаче ниже.\n"
        f"Целевой язык: {language_title}.\n"
        "Формат ответа:\n"
        "1) КРАТКОЕ пояснение структуры (2-4 пункта).\n"
        "2) Как и куда ставить/запускать (шаги установки/запуска, пути/файлы).\n"
        "3) Полный код (без markdown-оформления).\n"
        "Комментарии внутри кода не добавляй.\n"
        "Не обрывай код на середине: файл должен заканчиваться логически завершенно.\n\n"
        f"Задача: {task}"
    )


def build_stub_code(language_key: str | None) -> str:
    title = _get_lang_title(language_key)
    return (
        f"# Demo stub output for {title}\n"
        "# This is a long placeholder to test Telegram splitting and mini app rendering.\n\n"
        "import asyncio\n"
        "import json\n"
        "from dataclasses import dataclass\n"
        "from datetime import datetime\n\n"
        "@dataclass\n"
        "class Task:\n"
        "    id: int\n"
        "    title: str\n"
        "    done: bool = False\n\n"
        "class TaskStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._tasks = []\n"
        "        self._next_id = 1\n\n"
        "    def add(self, title: str) -> Task:\n"
        "        task = Task(self._next_id, title)\n"
        "        self._next_id += 1\n"
        "        self._tasks.append(task)\n"
        "        return task\n\n"
        "    def complete(self, task_id: int) -> bool:\n"
        "        for task in self._tasks:\n"
        "            if task.id == task_id:\n"
        "                task.done = True\n"
        "                return True\n"
        "        return False\n\n"
        "    def to_json(self) -> str:\n"
        "        return json.dumps([task.__dict__ for task in self._tasks], indent=2)\n\n"
        "async def run_demo() -> None:\n"
        "    store = TaskStore()\n"
        "    for i in range(1, 120):\n"
        "        store.add(f'Demo task {i}')\n"
        "    store.complete(3)\n"
        "    store.complete(7)\n"
        "    print('Generated at:', datetime.utcnow().isoformat())\n"
        "    print(store.to_json())\n\n"
        "if __name__ == '__main__':\n"
        "    asyncio.run(run_demo())\n"
    )


def build_universal_prompt(task: str) -> str:
    return (
        "Ответь подробно и по делу, без кода и без псевдокода.\n"
        "Оформи ответ как простой текст БЕЗ markdown.\n"
        "Формат:\n"
        "✨ Тема: <короткий заголовок>\n"
        "💡 Краткий вывод: <1-3 предложения>\n"
        "🧭 Шаги:\n"
        "1) ...\n"
        "2) ...\n"
        "3) ...\n\n"
        "Запрещено: любые #, **, ``` и другой markdown.\n\n"
        f"Запрос: {task}"
    )


def format_universal_response(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        clean = "✨ Тема: Ответ пуст\n💡 Краткий вывод: Повтори запрос.\n🧭 Шаги:\n1) Повтори запрос."
    return clean


async def generate_code_for_task(update: Update, context: ContextTypes.DEFAULT_TYPE, task_text: str) -> None:
    lang_key = context.user_data.get("selected_language")
    model = context.user_data.get("selected_model", ANTHROPIC_MODEL)

    if not lang_key:
        await update.message.reply_text(
            "Сначала выбери язык в /code.",
            reply_markup=build_code_settings_keyboard(context.user_data),
        )
        return

    user = update.effective_user
    if not user:
        await update.message.reply_text("Не удалось определить пользователя.")
        return

    if not is_paid_active(user.id):
        used = get_free_used(user.id)
        if used >= FREE_REQUESTS_LIMIT:
            await update.message.reply_text(
                f"Лимит бесплатных запросов исчерпан ({used}/{FREE_REQUESTS_LIMIT}).\n"
                f"Подписка: {PRICE_RUB} руб/мес.\n"
                "Выбери способ оплаты:",
                reply_markup=build_paywall_keyboard(user.id),
            )
            return
        increment_free_used(user.id)

    language_title = _get_lang_title(lang_key)
    prompt = build_code_generation_prompt(task_text, language_title)

    await update.message.chat.send_action(ChatAction.TYPING)
    if STUB_MODE:
        answer = build_stub_code(lang_key)
    else:
        logger.info("🕐Проверяю модель... (%s)", model)
        await ask_anthropic_ai(
            "Ответь строго одним словом: OK",
            model=model,
            system_prompt="Ты проверка доступности модели. Отвечай только: OK",
            temperature=0.0,
            max_tokens=32,
        )
        logger.info("🟩Модель работает!")
        logger.info("💻Пишу код...")
        answer = await ask_anthropic_ai(
            prompt,
            model=model,
            system_prompt="Ты сильный senior-разработчик. Пиши практичный, запускаемый код без воды.",
            temperature=0.5,
            max_tokens=4200,
        )
    response_id = ""
    try:
        response_id = await api_store_response(update.effective_user.id if update.effective_user else None, answer)
    except Exception:
        logger.exception("Failed to store response via API")
    mini_app_keyboard = build_mini_app_keyboard(response_id)
    if mini_app_keyboard:
        await update.message.reply_text(
            "Код готов. Открой в мини-приложении, чтобы увидеть весь ответ.",
            reply_markup=mini_app_keyboard,
        )
    else:
        await update.message.reply_text(
            "Не удалось сохранить ответ для мини-приложения. Проверь MINI_APP_API_URL."
        )


async def generate_universal_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, task_text: str) -> None:
    user = update.effective_user
    if not user:
        await update.message.reply_text("Не удалось определить пользователя.")
        return

    if not is_paid_active(user.id):
        used = get_free_used(user.id)
        if used >= FREE_REQUESTS_LIMIT:
            await update.message.reply_text(
                f"Лимит бесплатных запросов исчерпан ({used}/{FREE_REQUESTS_LIMIT}).\n"
                f"Подписка: {PRICE_RUB} руб/мес.\n"
                "Выбери способ оплаты:",
                reply_markup=build_paywall_keyboard(user.id),
            )
            return
        increment_free_used(user.id)

    await update.message.chat.send_action(ChatAction.TYPING)
    if STUB_MODE:
        answer = (
            "✨ Тема: Демо-ответ для проверки стиля\n"
            "💡 Краткий вывод: Это пример форматирования без кода.\n"
            "🧭 Шаги:\n"
            "1) Собери требования.\n"
            "2) Составь план из 3-5 пунктов.\n"
            "3) Проверь результат на одном примере."
        )
    else:
        prompt = build_universal_prompt(task_text)
        answer = await ask_anthropic_ai(
            prompt,
            model=ANTHROPIC_MODEL,
            system_prompt=UNIVERSAL_SYSTEM_PROMPT,
            temperature=0.6,
            max_tokens=1600,
        )

    await safe_reply_text(update.message, format_universal_response(answer))


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if user:
        upsert_user(user.id, user.username, user.first_name, user.last_name)

    user_text = update.message.text.strip()
    if not user_text:
        return

    lowered = user_text.lower()
    if lowered in {"help", "❓ помощь"}:
        await help_command(update, context)
        return
    if lowered in {"code", "код", "💻 код"}:
        await code_command(update, context)
        return
    if lowered in {"ideas", "идеи", "💡 идеи"}:
        await ideas_command(update, context)
        return
    if lowered in {"universal", "универсал", "🧠 универсал"}:
        await universal_command(update, context)
        return
    if lowered in {"subscribe", "подписка", "💳 подписка"}:
        await subscribe_command(update, context)
        return
    if lowered in {"cancel", "отмена", "❌ отмена"}:
        await cancel_command(update, context)
        return

    if not await ensure_access(update, context):
        return

    try:
        if context.user_data.get("awaiting_prompt_help"):
            context.user_data["awaiting_prompt_help"] = False
            language_title = _get_lang_title(context.user_data.get("selected_language"))
            if STUB_MODE:
                polished_prompt = f"{language_title}: {user_text}"
            else:
                helper_prompt = (
                    "Составь качественный промт для AI-кодогенератора. "
                    "Он должен быть конкретным, структурированным, с требованиями к коду, "
                    "обработкой ошибок и кратким форматом ответа.\n"
                    f"Целевой язык: {language_title}.\n"
                    f"Черновое описание пользователя: {user_text}"
                )

                await update.message.chat.send_action(ChatAction.TYPING)
                polished_prompt = await ask_anthropic_ai(
                    helper_prompt,
                    model=PROMPT_HELP_MODEL,
                    system_prompt="Ты эксперт по prompt engineering для coding-LLM.",
                    temperature=0.3,
                    max_tokens=900,
                )
            await safe_reply_text(
                update.message,
                "**Промт собран. Запускаю генерацию кода.**",
                parse_mode="Markdown",
            )
            await safe_reply_code(update.message, polished_prompt)
            await generate_code_for_task(update, context, polished_prompt)
            return

        if context.user_data.get("awaiting_universal_prompt_help"):
            context.user_data["awaiting_universal_prompt_help"] = False
            if STUB_MODE:
                polished_prompt = user_text
            else:
                helper_prompt = (
                    "Составь качественный промт для универсального AI-ассистента. "
                    "Он должен быть конкретным, структурированным, с требованиями к результату.\n"
                    f"Черновое описание пользователя: {user_text}"
                )
                await update.message.chat.send_action(ChatAction.TYPING)
                polished_prompt = await ask_anthropic_ai(
                    helper_prompt,
                    model=PROMPT_HELP_MODEL,
                    system_prompt="Ты эксперт по prompt engineering.",
                    temperature=0.3,
                    max_tokens=700,
                )
            await safe_reply_text(
                update.message,
                "**Промт собран. Запускаю ответ.**",
                parse_mode="Markdown",
            )
            await safe_reply_text(update.message, polished_prompt)
            await generate_universal_answer(update, context, polished_prompt)
            return

        if context.user_data.get("code_mode"):
            await generate_code_for_task(update, context, user_text)
            return

        if context.user_data.get("universal_mode"):
            await generate_universal_answer(update, context, user_text)
            return

        await update.message.reply_text(
            "Выбери режим: /code или /universal.\n"
            "Нажми «💻 Код» для генерации кода или «🧠 Универсал» для текстовых ответов.\n"
            "Или нажми «❓ Помощь».",
            reply_markup=main_keyboard(),
        )
    except httpx.HTTPStatusError as exc:
        logger.exception("Anthropic API HTTP error")
        status = exc.response.status_code
        if status == 529:
            await update.message.reply_text(
                "Anthropic перегружен (529). Это временно: попробуй снова через 15-60 секунд."
            )
        elif status in {500, 502, 503, 504}:
            await update.message.reply_text(
                "Anthropic сейчас отвечает ошибкой сервера (5xx). "
                "Попробуй повторить через 10-30 секунд."
            )
        elif status == 429:
            await update.message.reply_text(
                "Сработал лимит Anthropic (429). Подожди немного и попробуй снова."
            )
        elif status in {401, 403}:
            await update.message.reply_text(
                "Ошибка авторизации Anthropic. Проверь ANTHROPIC_API_KEY в .env."
            )
        else:
            await update.message.reply_text(
                f"Ошибка API: {status}. Проверь ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY и модель."
            )
    except (httpx.ConnectError, httpx.TimeoutException):
        logger.exception("Anthropic network error")
        await update.message.reply_text(
            "Сервер Anthropic сейчас недоступен или сеть нестабильна. Попробуй через 10-30 секунд."
        )
    except Exception:
        logger.exception("Unexpected error")
        await update.message.reply_text("Произошла ошибка при обращении к ИИ.")


def main() -> None:
    validate_env()
    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("universal", universal_command))
    app.add_handler(CommandHandler("ideas", ideas_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(code_settings_callback, pattern=r"^(model:|lang:|code:|universal:).+"))
    app.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay:.+"))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()


