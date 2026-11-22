import os, asyncio
from dotenv import load_dotenv
load_dotenv()

from telegram.ext import Application, MessageHandler, filters
import db, brain

ALLOWED = int(os.environ["ALLOWED_CHAT_ID"])
only_me = filters.Chat(chat_id=ALLOWED)


async def handle_text(update, context):
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    reply = await brain.respond(update.message.text, context.user_data)
    await update.message.reply_text(reply)


async def on_start(app):
    asyncio.create_task(brain.warm())   # loads the local model without blocking startup


db.init()
db.init_extra()
app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).post_init(on_start).build()
app.add_handler(MessageHandler(only_me & filters.TEXT & ~filters.COMMAND, handle_text))
app.run_polling()
