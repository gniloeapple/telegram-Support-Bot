import sqlite3
import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_TOKEN')
SUPPORT_CHAT_ID = int(os.getenv('SUPPORT_CHAT_ID'))

# --- НОВОЕ: Чтение ID топика ---
raw_topic_id = os.getenv('SUPPORT_TOPIC_ID')
# Преобразуем в int только если значение существует и является числом
SUPPORT_TOPIC_ID = int(raw_topic_id) if raw_topic_id and raw_topic_id.strip().isdigit() else None

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
    await update.message.reply_text('Здравствуйте! Напишите ваше сообщение.\n\n/help — информация о работе поддержки')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🕘 Время работы поддержки: Пн - Вс, с 7:00 до 21:00 по МСК
❗️ Если вопрос связан с заказом, обязательно укажите номер (ID) проблемного заказа.
📝 Заполняйте тикет внимательно и кратко, но максимально подробно. Помните, что это не чат с техподдержкой в реальном времени. Все тикеты обрабатываются в порядке очереди.
⌛️ Возможно придётся подождать некоторое время, прежде чем вы получите ответ на свой вопрос."""
    await update.message.reply_text(help_text)

async def forward_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_chat_id = update.message.chat_id
    user_message_id = update.message.message_id
    
    header = f"📩 Сообщение от {user.first_name} (id: {user.id}):"
    
    # --- НОВОЕ: Подготовка параметров отправки ---
    # Если задан ID топика, добавляем его в параметры
    send_kwargs = {'chat_id': SUPPORT_CHAT_ID}
    if SUPPORT_TOPIC_ID:
        send_kwargs['message_thread_id'] = SUPPORT_TOPIC_ID

    sent_message = None

    try:
        # Фото (Photo)
        if update.message.photo:
            cap = update.message.caption or ''
            photos = update.message.photo
            file_id = photos[-1].file_id 
            sent_message = await context.bot.send_photo(
                photo=file_id,
                caption=f"{header}\n\n{cap}",
                **send_kwargs
            )

        # Видео (Video)
        elif update.message.video:
            cap = update.message.caption or ''
            sent_message = await context.bot.send_video(
                video=update.message.video.file_id,
                caption=f"{header}\n\n{cap}",
                **send_kwargs
            )

        # Документ (files, скрины и пр.)
        elif update.message.document:
            cap = update.message.caption or ''
            sent_message = await context.bot.send_document(
                document=update.message.document.file_id,
                caption=f"{header}\n\n{cap}",
                **send_kwargs
            )

        # Голосовое (Voice)
        elif update.message.voice:
            sent_message = await context.bot.send_voice(
                voice=update.message.voice.file_id,
                caption=header,
                **send_kwargs
            )

        # Аудио
        elif update.message.audio:
            cap = update.message.caption or ''
            sent_message = await context.bot.send_audio(
                audio=update.message.audio.file_id,
                caption=f"{header}\n\n{cap}",
                **send_kwargs
            )

        # Текстовое сообщение
        elif update.message.text:
            sent_message = await context.bot.send_message(
                text=f"{header}\n\n{update.message.text}",
                **send_kwargs
            )
        
        else:
            # Unsupported type
            return

        if sent_message:
            save_mapping(user_chat_id, user_message_id, sent_message.message_id)

    except Exception as e:
        logger.error(f"Ошибка при пересылке сообщения: {e}")

async def reply_from_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    # Проверка чата
    if message.chat_id != SUPPORT_CHAT_ID:
        return

    # --- НОВОЕ: Проверка топика ---
    # Если топик настроен, игнорируем сообщения из других топиков
    if SUPPORT_TOPIC_ID and message.message_thread_id != SUPPORT_TOPIC_ID:
        return

    if not message.reply_to_message:
        return

    replied_msg = message.reply_to_message
    found = find_user_by_support_message(replied_msg.message_id)

    if not found:
        return

    user_chat_id, user_message_id = found

    try:
        # Пересылка всех поддерживаемых медиа и текста обратно пользователю
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
            
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа пользователю: {e}")

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
