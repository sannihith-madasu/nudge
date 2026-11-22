import os, time, json, urllib.request
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
import db

WAKE = os.getenv("WAKE_TIME", "07:00")
REVIEW = os.getenv("REVIEW_TIME", "21:00")
START_H = int(os.getenv("CHECKIN_START", "9"))
END_H = int(os.getenv("CHECKIN_END", "21"))
NOTIFY = os.getenv("NOTIFY_TELEGRAM", "1") == "1"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["ALLOWED_CHAT_ID"]

def telegram(text):
    try:
        data = json.dumps({"chat_id": CHAT, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage", data,
            {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("telegram failed:", e)

def notify(kind, text, minutes=10):
    db.post_banner(kind, text, minutes)
    if NOTIFY:
        telegram(text)

def t12(iso):
    return datetime.fromisoformat(iso).strftime("%I:%M %p").lstrip("0")

def briefing():
    events = db.events_on(datetime.now().date())
    tasks = db.open_tasks()[:3]
    lines = ["Good morning!", "", "Today:"]
    lines += [f"{t12(e['start'])}  {e['title']}" for e in events] or ["No events"]
    lines += ["", "Top tasks:"]
    lines += [f"{t['id']}. {t['title']}" for t in tasks] or ["Nothing pending"]
    return "\n".join(lines)

def tick():
    now = datetime.now()
    today = now.date().isoformat()
    hm = now.strftime("%H:%M")

    if hm >= WAKE and now.hour < 12 and db.once(f"brief:{today}"):
        notify("briefing", briefing(), minutes=15)

    if hm >= REVIEW and db.once(f"review:{today}"):
        left = db.open_tasks()
        text = ("Evening review.\n" + (f"{len(left)} tasks still open. First: {left[0]['title']}"
                if left else "All tasks done. Nice."))
        notify("review", text, minutes=15)

    if START_H <= now.hour < END_H:
        tasks = db.open_tasks()
        if tasks and db.once(f"checkin:{today}:{now.hour}"):
            t = tasks[0]
            notify("checkin",
                   f"Check-in: are you working on '{t['title']}'?\n"
                   f"Reply 'done {t['id']}' in Telegram when finished.", minutes=10)

    for e in db.events_on(now.date()):
        start = datetime.fromisoformat(e["start"])
        if now <= start <= now + timedelta(minutes=15) and db.once(f"ev:{e['id']}"):
            mins = max(1, int((start - now).total_seconds() // 60))
            notify("event", f"In {mins} min: {e['title']}", minutes=mins + 1)

if __name__ == "__main__":
    db.init()
    db.init_extra()
    print("scheduler running")
    while True:
        try:
            tick()
        except Exception as e:
            print("tick error:", e)
        time.sleep(30)
