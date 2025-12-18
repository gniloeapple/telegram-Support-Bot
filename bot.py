import sqlite3
import logging
import os
from datetime import datetime, timezone
import pytz

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID"))

raw_topic_id = os.getenv("SUPPORT_TOPIC_ID")
SUPPORT_TOPIC_ID = int(raw_topic_id) if raw_topic_id and raw_topic_id.strip().isdigit() else None

# Временная зона МСК
MSK = pytz.timezone('Europe/Moscow')

conn = sqlite3.connect("support_bot.db", check_same_thread=False)
cursor = conn.cursor()

# ---- таблица маппинга сообщений ----
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS messages_mapping (
    user_chat_id      INTEGER,
    user_message_id   INTEGER,
    support_message_id INTEGER,
    ticket_id         INTEGER,
    PRIMARY KEY(user_chat_id, user_message_id)
)
"""
)

# ---- таблица тикетов ----
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS tickets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_chat_id  INTEGER NOT NULL,
    username      TEXT,
    first_name    TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""
)

# ----------------- Утилиты -----------------
def format_datetime(iso_string: str) -> str:
    """Конвертирует ISO datetime в читаемый формат МСК"""
    dt = datetime.fromisoformat(iso_string)
    # Если время UTC, конвертируем в МСК
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_msk = dt.astimezone(MSK)
    return dt_msk.strftime("%d.%m.%Y %H:%M")


# ----------------- Работа с БД / тикетами -----------------
def get_open_ticket(user_chat_id: int):
    cursor.execute(
        """
        SELECT id FROM tickets
        WHERE user_chat_id = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_chat_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def create_ticket(user_chat_id: int, username: str = None, first_name: str = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO tickets (user_chat_id, username, first_name, status, created_at, updated_at)
        VALUES (?, ?, ?, 'open', ?, ?)
        """,
        (user_chat_id, username, first_name, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def update_ticket_status(ticket_id: int, status: str):
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        UPDATE tickets
        SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, now, ticket_id),
    )
    conn.commit()



def get_ticket_by_support_message(support_message_id: int):
    cursor.execute(
        """
        SELECT ticket_id FROM messages_mapping
        WHERE support_message_id = ?
        """,
        (support_message_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def save_mapping(user_chat_id, user_message_id, support_message_id, ticket_id):
    cursor.execute(
        """
        INSERT OR REPLACE INTO messages_mapping (
            user_chat_id, user_message_id, support_message_id, ticket_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (user_chat_id, user_message_id, support_message_id, ticket_id),
    )
    conn.commit()


def find_user_by_support_message(support_message_id):
    cursor.execute(
        """
        SELECT user_chat_id, user_message_id, ticket_id
        FROM messages_mapping
        WHERE support_message_id = ?
        """,
        (support_message_id,),
    )
    return cursor.fetchone()


def get_all_open_tickets(limit: int = 50):
    cursor.execute(
        """
        SELECT id, user_chat_id, username, first_name, created_at, updated_at
        FROM tickets
        WHERE status = 'open'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()
    
def get_user_chat_id_by_ticket(ticket_id: int):
    cursor.execute(
        """
        SELECT user_chat_id FROM tickets
        WHERE id = ?
        """,
        (ticket_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None

# ----------------- Хендлеры пользователя -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! Напишите ваше сообщение.\n\nНапишите Ваш вопрос, и мы ответим Вам в ближайшее время.\n\n🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК\n\n"
        "📝 Заполняйте тикет внимательно и кратко, но максимально подробно. Помните, что это не чат с техподдержкой в реальном времени. Все тикеты обрабатываются в порядке очереди.\n\n"
        "⌛️ Возможно придётся подождать некоторое время, прежде чем вы получите ответ на свой вопрос."
    )
    await update.message.reply_text(help_text)


async def forward_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    user_chat_id = message.chat_id
    user_message_id = message.message_id

    # ищем открытый тикет или создаём новый
    ticket_id = get_open_ticket(user_chat_id)
    new_ticket = False
    if ticket_id is None:
        ticket_id = create_ticket(user_chat_id, user.username, user.first_name)
        new_ticket = True

    # Формируем username с @ или "Не указан"
    username = f"@{user.username}" if user.username else "Не указан"
    
    # Формируем красивый заголовок
    if new_ticket:
        header = (
            f"🎫 НОВЫЙ ТИКЕТ\n\n"
            f"🆔 Тикет: {ticket_id}\n"
            f"👤 Пользователь: {user.first_name or 'Не указано'}\n"
            f"🆔 Telegram ID: {user.id}\n"
            f"📱 Username: {username}"
        )
    else:
        # Для дополнительных сообщений в уже открытом тикете
        header = (
            f"💬 Тикет #{ticket_id}\n"
            f"👤 {user.first_name or 'Не указано'} ({username}):"
        )

    # если новый тикет — уведомляем пользователя
    if new_ticket:
        await message.reply_text(
            f"✅ Ваш тикет #{ticket_id} создан. Оператор поддержки скоро ответит."
        )

    send_kwargs = {"chat_id": SUPPORT_CHAT_ID}
    if SUPPORT_TOPIC_ID:
        send_kwargs["message_thread_id"] = SUPPORT_TOPIC_ID

    sent_message = None

    try:
        if message.photo:
            cap = message.caption or ""
            file_id = message.photo[-1].file_id
            caption_text = f"{header}\n\n{cap}" if cap else header
            sent_message = await context.bot.send_photo(
                photo=file_id,
                caption=caption_text,
                **send_kwargs,
            )
        elif message.video:
            cap = message.caption or ""
            caption_text = f"{header}\n\n{cap}" if cap else header
            sent_message = await context.bot.send_video(
                video=message.video.file_id,
                caption=caption_text,
                **send_kwargs,
            )
        elif message.document:
            cap = message.caption or ""
            caption_text = f"{header}\n\n{cap}" if cap else header
            sent_message = await context.bot.send_document(
                document=message.document.file_id,
                caption=caption_text,
                **send_kwargs,
            )
        elif message.voice:
            sent_message = await context.bot.send_voice(
                voice=message.voice.file_id,
                caption=header,
                **send_kwargs,
            )
        elif message.audio:
            cap = message.caption or ""
            caption_text = f"{header}\n\n{cap}" if cap else header
            sent_message = await context.bot.send_audio(
                audio=message.audio.file_id,
                caption=caption_text,
                **send_kwargs,
            )
        elif message.text:
            sent_message = await context.bot.send_message(
                text=f"{header}\n\n{message.text}",
                **send_kwargs,
            )
        else:
            return

        if sent_message:
            save_mapping(
                user_chat_id,
                user_message_id,
                sent_message.message_id,
                ticket_id,
            )
    except Exception as e:
        logger.error(f"Ошибка при пересылке сообщения: {e}")

# ----------------- Хендлеры поддержки -----------------
async def reply_from_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if SUPPORT_TOPIC_ID and message.message_thread_id != SUPPORT_TOPIC_ID:
        return
    if not message.reply_to_message:
        return

    replied_msg = message.reply_to_message
    found = find_user_by_support_message(replied_msg.message_id)
    if not found:
        return

    user_chat_id, user_message_id, ticket_id = found

    try:
        if message.photo:
            cap = message.caption or ""
            await context.bot.send_photo(
                chat_id=user_chat_id,
                photo=message.photo[-1].file_id,
                caption=cap,
            )
        elif message.video:
            cap = message.caption or ""
            await context.bot.send_video(
                chat_id=user_chat_id,
                video=message.video.file_id,
                caption=cap,
            )
        elif message.document:
            cap = message.caption or ""
            await context.bot.send_document(
                chat_id=user_chat_id,
                document=message.document.file_id,
                caption=cap,
            )
        elif message.voice:
            await context.bot.send_voice(
                chat_id=user_chat_id,
                voice=message.voice.file_id,
                caption=message.caption or "",
            )
        elif message.audio:
            cap = message.caption or ""
            await context.bot.send_audio(
                chat_id=user_chat_id,
                audio=message.audio.file_id,
                caption=cap,
            )
        elif message.text:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text=message.text,
            )

    except Exception as e:
        logger.error(f"Ошибка при отправке ответа пользователю: {e}")


# --------- Команды для операторов в чате поддержки ---------
async def open_tickets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    # Команда только из чата поддержки
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if SUPPORT_TOPIC_ID and message.message_thread_id != SUPPORT_TOPIC_ID:
        return

    rows = get_all_open_tickets()

    if not rows:
        await message.reply_text("Открытых тикетов нет ✅")
        return

    lines = ["📂 Открытые тикеты:\n"]
    for ticket_id, user_chat_id, username, first_name, created_at, updated_at in rows:
        created_fmt = format_datetime(created_at)
        username_display = f"@{username}" if username else "Не указан"
        first_name_display = first_name or "Не указано"
        
        lines.append(
            f"🎫 Тикет #{ticket_id}\n"
            f"👤 {first_name_display}\n"
            f"📱 {username_display}\n"
            f"🆔 ID: {user_chat_id}\n"
            f"📅 Создан: {created_fmt}\n"
        )

    text = "\n".join(lines)
    await message.reply_text(text)

async def close_ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if SUPPORT_TOPIC_ID and message.message_thread_id != SUPPORT_TOPIC_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Команду /close нужно вызывать ответом на сообщение тикета.")
        return

    ticket_id = get_ticket_by_support_message(message.reply_to_message.message_id)
    if not ticket_id:
        await message.reply_text("Не удалось определить тикет для этого сообщения.")
        return

    # Получаем user_chat_id для уведомления
    user_chat_id = get_user_chat_id_by_ticket(ticket_id)
    
    update_ticket_status(ticket_id, "closed")
    await message.reply_text(f"✅ Тикет #{ticket_id} закрыт.")
    
    # Уведомляем пользователя
    if user_chat_id:
        try:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text="✅ Обращение завершено"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления пользователю {user_chat_id}: {e}")


async def reopen_ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if SUPPORT_TOPIC_ID and message.message_thread_id != SUPPORT_TOPIC_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Команду /reopen нужно вызывать ответом на сообщение тикета.")
        return

    ticket_id = get_ticket_by_support_message(message.reply_to_message.message_id)
    if not ticket_id:
        await message.reply_text("Не удалось определить тикет для этого сообщения.")
        return

    update_ticket_status(ticket_id, "open")
    await message.reply_text(f"♻️ Тикет #{ticket_id} снова открыт.")


async def ticket_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID:
        return
    if SUPPORT_TOPIC_ID and message.message_thread_id != SUPPORT_TOPIC_ID:
        return
    if not message.reply_to_message:
        await message.reply_text("Команду /ticket нужно вызывать ответом на сообщение тикета.")
        return

    ticket_id = get_ticket_by_support_message(message.reply_to_message.message_id)
    if not ticket_id:
        await message.reply_text("Не удалось определить тикет для этого сообщения.")
        return

    cursor.execute(
        """
        SELECT user_chat_id, status, created_at, updated_at
        FROM tickets
        WHERE id = ?
        """,
        (ticket_id,),
    )
    row = cursor.fetchone()
    if not row:
        await message.reply_text("Тикет не найден в базе.")
        return

    user_chat_id, status, created_at, updated_at = row
    created_fmt = format_datetime(created_at)
    updated_fmt = format_datetime(updated_at)

    text = (
        f"📄 Тикет #{ticket_id}\n"
        f"Пользователь: {user_chat_id}\n"
        f"Статус: {status}\n"
        f"Создан: {created_fmt}\n"
        f"Обновлён: {updated_fmt}"
    )
    await message.reply_text(text)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")


def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # команды для операторов
    application.add_handler(CommandHandler("close", close_ticket_cmd))
    application.add_handler(CommandHandler("reopen", reopen_ticket_cmd))
    application.add_handler(CommandHandler("ticket", ticket_info_cmd))
    application.add_handler(CommandHandler("open_tickets", open_tickets_cmd))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.ALL ^ filters.COMMAND),
            forward_to_support,
        )
    )
    application.add_handler(MessageHandler(filters.REPLY, reply_from_support))

    application.add_error_handler(error_handler)

    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()