from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import FileResponse
import db

db.init()
db.init_extra()
app = FastAPI()

def t12(iso):
    return datetime.fromisoformat(iso).strftime("%I:%M %p").lstrip("0")

@app.get("/api/state")
def state():
    now = datetime.now()
    events = [{"time": t12(e["start"]), "title": e["title"]} for e in db.events_on(now.date())]
    tasks = []
    for t in db.open_tasks()[:6]:
        due = ""
        if t["due"]:
            d = datetime.fromisoformat(t["due"])
            due = d.strftime("%a") if (d.hour, d.minute) == (23, 59) else d.strftime("%a %I:%M %p")
        tasks.append({"id": t["id"], "title": t["title"], "due": due})
    b = db.current_banner()
    return {"date": now.strftime("%A, %d %B"), "events": events, "tasks": tasks,
            "banner": b["text"] if b else None, "kind": b["kind"] if b else None}

@app.get("/")
def index():
    return FileResponse("static/index.html")
