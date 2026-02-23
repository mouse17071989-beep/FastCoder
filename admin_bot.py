import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from db import get_user, grant_paid, init_db, recent_users, revoke_paid, stats, upsert_user

load_dotenv(encoding="utf-8-sig")

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def require_admin(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("Нет доступа.")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    if not await require_admin(update):
        return
    await update.message.reply_text(
        "Admin panel ready.\n"
        "/stats\n"
        "/grant <user_id> <days>\n"
        "/revoke <user_id>\n"
        "/user <user_id>\n"
        "/recent"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    s = stats()
    await update.message.reply_text(
        f"Users: {s['total']}\nSubscribed: {s['members']}\nPaid: {s['paid']}"
    )


async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("Использование: /grant <user_id> <days>")
        return
    user_id = int(context.args[0])
    days = int(context.args[1])
    upsert_user(user_id, None, None, None)
    until = grant_paid(user_id, days)
    await update.message.reply_text(f"Выдан paid-доступ user_id={user_id} до {until}")


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /revoke <user_id>")
        return
    user_id = int(context.args[0])
    revoke_paid(user_id)
    await update.message.reply_text(f"Paid-доступ снят для user_id={user_id}")


async def user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /user <user_id>")
        return
    row = get_user(int(context.args[0]))
    if not row:
        await update.message.reply_text("Пользователь не найден")
        return
    await update.message.reply_text(
        f"ID: {row['user_id']}\n"
        f"Username: @{row['username'] or '-'}\n"
        f"Member: {row['is_channel_member']}\n"
        f"Paid: {row['is_paid']}\n"
        f"Paid until: {row['paid_until'] or '-'}"
    )


async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    rows = recent_users(20)
    if not rows:
        await update.message.reply_text("Пользователей пока нет")
        return
    text = "\n".join(
        f"{r['user_id']} @{r['username'] or '-'} {r['last_seen']}" for r in rows
    )
    await update.message.reply_text(text)


def main() -> None:
    if not ADMIN_BOT_TOKEN:
        raise RuntimeError("Не задан ADMIN_BOT_TOKEN в .env")
    if not ADMIN_IDS:
        raise RuntimeError("Не заданы ADMIN_IDS в .env")

    init_db()
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("grant", grant_cmd))
    app.add_handler(CommandHandler("revoke", revoke_cmd))
    app.add_handler(CommandHandler("user", user_cmd))
    app.add_handler(CommandHandler("recent", recent_cmd))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
