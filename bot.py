import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from telegram.ext import Application, MessageHandler, filters
import db, nlu

ALLOWED = int(os.environ["ALLOWED_CHAT_ID"])
only_me = filters.Chat(chat_id=ALLOWED)

YES = {"yes", "y", "ok", "yep", "yeah", "sure"}
NO = {"no", "n", "nope", "cancel"}

def fmt(dt):
    return dt.strftime("%a %d %b, %I:%M %p")

async def handle_text(update, context):
    text = update.message.text.strip()
    low = text.lower()
    reply = update.message.reply_text

    # 1. Answering a pending confirmation
    pending = context.user_data.get("pending")
    if pending and low in YES | NO:
        context.user_data.pop("pending")
        if low in NO:
            return await reply("Cancelled.")
        if pending["kind"] == "event":
            db.add_event(pending["title"], pending["when"])
        else:
            db.add_task(pending["title"], pending["when"])
        return await reply(f"Saved: {pending['title']}")

    # 2. New request
    req = nlu.parse(text)
    kind = req["kind"]

    if kind == "today":
        events = db.events_on(datetime.now().date())
        lines = ["Today:"]
        lines += [f"  {datetime.fromisoformat(e['start']).strftime('%I:%M %p')}  {e['title']}"
                  for e in events] or ["  No events"]
        return await reply("\n".join(lines))

    if kind == "tasks":
        tasks = db.open_tasks()
        if not tasks:
            return await reply("No open tasks.")
        lines = []
        for t in tasks:
            due = f" (due {fmt(datetime.fromisoformat(t['due']))})" if t["due"] else ""
            lines.append(f"{t['id']}. {t['title']}{due}")
        return await reply("\n".join(lines))

    if kind == "done":
        ok = db.complete_task(req["id"])
        return await reply("Marked done." if ok else "No open task with that number.")

    # event or task: ask for confirmation first
    context.user_data["pending"] = req
    label = "Event" if kind == "event" else "Task"
    when = fmt(req["when"]) if req["when"] else "no deadline"
    await reply(f"{label}: {req['title']}\nWhen: {when}\nSave? (yes / no)")

db.init_extra()
app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
app.add_handler(MessageHandler(only_me & filters.TEXT & ~filters.COMMAND, handle_text))
app.run_polling()
