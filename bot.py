import sqlite3
import logging
import os
from dotenv import load_dotenv
from telegram import Update, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_TOKEN')
SUPPORT_CHAT_ID = int(os.getenv('SUPPORT_CHAT_ID'))

conn = sqlite3.connect('support_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS messages_mapping (
    user_chat_id INTEGER,
    user_message_id INTEGER,
    support_message_id INTEGER,
    PRIMARY KEY(user_chat_id, user_message_id)
)
''')
conn.commit()

def save_mapping(user_chat_id, user_message_id, support_message_id):
    cursor.execute('''
    INSERT OR REPLACE INTO messages_mapping (user_chat_id, user_message_id, support_message_id)
    VALUES (?, ?, ?)
    ''', (user_chat_id, user_message_id, support_message_id))
    conn.commit()

def find_user_by_support_message(support_message_id):
    cursor.execute('''
    SELECT user_chat_id, user_message_id FROM messages_mapping WHERE support_message_id=?
    ''', (support_message_id,))
    return cursor.fetchone()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Здравствуйте!\n\nНапишите Ваш вопрос, и мы ответим Вам в ближайшее время.\n\n🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК

📝 Заполняйте тикет внимательно и кратко, но максимально подробно. Помните, что это не чат с техподдержкой в реальном времени. Все тикеты обрабатываются в порядке очереди.
⌛️ Возможно придётся подождать некоторое время, прежде чем вы получите ответ на свой вопрос."""
    await update.message.reply_text(help_text)

async def forward_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_chat_id = update.message.chat_id
    user_message_id = update.message.message_id

    header = f"📩 Сообщение от {user.first_name} (id: {user.id}):"
    sent_message = None
    
    # Фото (Photo)
    if update.message.photo:
        cap = update.message.caption or ''
        photos = update.message.photo
        file_id = photos[-1].file_id  # самое большое по размеру
        sent_message = await context.bot.send_photo(
            chat_id=SUPPORT_CHAT_ID,
            photo=file_id,
            caption=f"{header}\n\n{cap}"
        )
    # Видео (Video)
    elif update.message.video:
        cap = update.message.caption or ''
        sent_message = await context.bot.send_video(
            chat_id=SUPPORT_CHAT_ID,
            video=update.message.video.file_id,
            caption=f"{header}\n\n{cap}"
        )
    # Документ (files, скрины и пр.)
    elif update.message.document:
        cap = update.message.caption or ''
        sent_message = await context.bot.send_document(
            chat_id=SUPPORT_CHAT_ID,
            document=update.message.document.file_id,
            caption=f"{header}\n\n{cap}"
        )
    # Голосовое (Voice)
    elif update.message.voice:
        sent_message = await context.bot.send_voice(
            chat_id=SUPPORT_CHAT_ID,
            voice=update.message.voice.file_id,
            caption=header
        )
    # Аудио
    elif update.message.audio:
        cap = update.message.caption or ''
        sent_message = await context.bot.send_audio(
            chat_id=SUPPORT_CHAT_ID,
            audio=update.message.audio.file_id,
            caption=f"{header}\n\n{cap}"
        )
    # Текстовое сообщение
    elif update.message.text:
        sent_message = await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            text=f"{header}\n\n{update.message.text}"
        )
    else:
        # Unsupported type, ignore or log
        return

    if sent_message:
        save_mapping(user_chat_id, user_message_id, sent_message.message_id)

async def reply_from_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != SUPPORT_CHAT_ID or not message.reply_to_message:
        return

    replied_msg = message.reply_to_message
    found = find_user_by_support_message(replied_msg.message_id)
    if not found:
        return
    user_chat_id, user_message_id = found

    # Пересылка всех поддерживаемых медиа и текста обратно пользователю
    # Фото
    if message.photo:
        cap = message.caption or ''
        await context.bot.send_photo(chat_id=user_chat_id, photo=message.photo[-1].file_id, caption=cap)
    elif message.video:
        cap = message.caption or ''
        await context.bot.send_video(chat_id=user_chat_id, video=message.video.file_id, caption=cap)
    elif message.document:
        cap = message.caption or ''
        await context.bot.send_document(chat_id=user_chat_id, document=message.document.file_id, caption=cap)
    elif message.voice:
        await context.bot.send_voice(chat_id=user_chat_id, voice=message.voice.file_id, caption=message.caption or '')
    elif message.audio:
        cap = message.caption or ''
        await context.bot.send_audio(chat_id=user_chat_id, audio=message.audio.file_id, caption=cap)
    elif message.text:
        await context.bot.send_message(chat_id=user_chat_id, text=message.text)
    # Можно добавить другие типы при желании

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    # Важно! Обрабатываем любые типы сообщений из лички пользователя
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.ALL ^ filters.COMMAND), forward_to_support))
    # Обрабатываем любые типы ответов поддержки
    application.add_handler(MessageHandler(filters.REPLY, reply_from_support))

    application.add_error_handler(error_handler)

    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
