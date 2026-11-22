import os, re, json, time
from datetime import datetime, time as dtime
from dotenv import load_dotenv
load_dotenv()

import dateparser
import db, llm, cloud

SETTINGS = {"PREFER_DATES_FROM": "future",
            "TIMEZONE": os.getenv("TIMEZONE", "Asia/Kolkata"),
            "RETURN_AS_TIMEZONE_AWARE": False}

YES = {"yes", "y", "ok", "okay", "yep", "yeah", "sure", "confirm"}
NO = {"no", "n", "nope", "cancel", "nevermind", "never mind"}
PENDING_TTL = 600  # seconds before an unfinished draft is forgotten

CLOUD_ON = os.getenv("CLOUD_ENABLED", "1") == "1"
CLOUD_LIMIT = int(os.getenv("CLOUD_DAILY_LIMIT", "25"))

FORCE_WEB = re.compile(r"^(?:web|search|google|look up)\s*[:,]\s*(.+)$", re.I)
FORCE_LOCAL = re.compile(r"^(?:local|offline)\s*:\s*(.+)$", re.I)
RECHECK = {"check online", "check that online", "check that", "search that",
           "search online", "are you sure", "verify that"}
WEB_HINTS = re.compile(r"\b(latest|news|headlines|score of|stock|share price|price of|who won|"
                       r"release date|forecast|weather|temperature|trending)\b", re.I)
PERSONAL = re.compile(r"\b(i|i'm|im|i've|my|me|myself|mine)\b", re.I)

# ---------- the prompt (static part first, so Ollama can cache it) ----------

PARSER_PROMPT = """You are the brain of a personal assistant on the user's Raspberry Pi. Read the user's MESSAGE and reply with one JSON object.

Fields:
- intent: one of
  add_event: something at a specific clock time (appointment, meeting, call, class), or "remind me ... at <time>".
  add_task: a to-do with no clock time, or only a date/deadline ("by friday", "tomorrow").
  complete_task: the user says they finished/did a task from OPEN TASKS. Set task_id.
  list_today: asks what is on today / their schedule.
  list_tasks: asks what is pending / their to-dos.
  ask_web: needs facts from the internet: news, current events, scores, prices, weather, anything recent, facts about specific people/places/products/companies, health/legal/money questions, "look up ...", or anything you could not know for sure.
  chat: feelings, motivation, small talk, opinions, advice, thanks, simple explanations, writing help, anything about the user themselves.
- title: short action phrase for add_event/add_task, WITHOUT any date or time words. Otherwise "".
- when: the date/time words exactly as the user wrote them ("tomorrow at 3pm", "friday", "in 2 hours", "23rd"). "" if none. Never invent one.
- task_id: id from OPEN TASKS for complete_task, otherwise 0.
- query: only for ask_web: the question rewritten to stand alone (max 15 words) with NO personal details. Otherwise "".
- reply: only for chat. 1-3 short plain sentences, warm and direct. If you don't know something, say so. Never invent facts about the user's schedule. Otherwise "".

People rarely say "add a task". Infer the intent from natural speech.
If DRAFT is not "none", the user is probably correcting or completing it: keep the draft's title and intent unless they change them, and put only the new date/time words in "when". If the message is clearly about something else, ignore the draft.

Examples:
MESSAGE: i have a dentist appointment tomorrow at 3
{"intent":"add_event","title":"dentist appointment","when":"tomorrow at 3","task_id":0,"query":"","reply":""}
MESSAGE: gotta submit the report by friday
{"intent":"add_task","title":"submit the report","when":"friday","task_id":0,"query":"","reply":""}
MESSAGE: remind me to call mom at 7pm
{"intent":"add_event","title":"call mom","when":"7pm","task_id":0,"query":"","reply":""}
MESSAGE: just finished buying milk   (OPEN TASKS: 4. buy milk)
{"intent":"complete_task","title":"buy milk","when":"","task_id":4,"query":"","reply":""}
MESSAGE: what do i have today?
{"intent":"list_today","title":"","when":"","task_id":0,"query":"","reply":""}
MESSAGE: what's the latest news on the mars mission
{"intent":"ask_web","title":"","when":"","task_id":0,"query":"latest news on the Mars mission","reply":""}
MESSAGE: is it safe to take ibuprofen with paracetamol
{"intent":"ask_web","title":"","when":"","task_id":0,"query":"is it safe to take ibuprofen with paracetamol","reply":""}
MESSAGE: i'm feeling really low today
{"intent":"chat","title":"","when":"","task_id":0,"query":"","reply":"I'm sorry today feels heavy. Want to try one tiny thing, just five minutes on something easy, and see how you feel after?"}
MESSAGE: on the 23rd   (DRAFT: add_task, title "clear phone storage")
{"intent":"add_task","title":"clear phone storage","when":"23rd","task_id":0,"query":"","reply":""}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["add_event", "add_task", "complete_task",
                                              "list_today", "list_tasks", "ask_web", "chat"]},
        "title": {"type": "string"},
        "when": {"type": "string"},
        "task_id": {"type": "integer"},
        "query": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["intent", "title", "when", "task_id", "query", "reply"],
}

# ---------- date handling (plain code, no LLM) ----------

TIME_HINT = re.compile(
    r"\d{1,2}:\d{2}|\b\d{1,2}\s*(am|pm)\b|\bat\s+\d{1,2}\b"
    r"|\b(noon|midnight|morning|afternoon|evening|tonight)\b"
    r"|\bin\s+(an?|\d+)\s*(hours?|hrs?|minutes?|mins?)\b", re.I)
EXPLICIT_AMPM = re.compile(r"\b(am|pm)\b|noon|midnight|morning|afternoon|evening|tonight|night", re.I)
PM_WORDS = re.compile(r"afternoon|evening|tonight|night", re.I)
RELATIVE = re.compile(r"\bin\s+(an?|\d+)\s*(hours?|hrs?|minutes?|mins?)\b", re.I)
TIME_ONLY = re.compile(r"^\s*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", re.I)


def parse_time_only(t):
    m = TIME_ONLY.match(t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if h > 23 or mi > 59:
        return None
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 6:      # a bare "3" almost always means 3 PM
        h += 12
    return dtime(h, mi)


def resolve(when_text, base_date=None):
    """Turn the user's date words into (datetime or None, has_clock_time)."""
    t = when_text.strip()
    if not t:
        return None, False
    if base_date:
        tm = parse_time_only(t)
        if tm:
            return datetime.combine(base_date, tm), True
    dt = dateparser.parse(t, settings=SETTINGS)
    if dt is None:
        return None, False
    has_time = bool(TIME_HINT.search(t))
    if has_time and not RELATIVE.search(t):
        if not EXPLICIT_AMPM.search(t) and 1 <= dt.hour <= 6:
            dt = dt.replace(hour=dt.hour + 12)
        elif PM_WORDS.search(t) and dt.hour < 12:
            dt = dt.replace(hour=dt.hour + 12)
    return dt.replace(second=0, microsecond=0), has_time

# ---------- formatting ----------


def fmt(dt):
    return dt.strftime("%a %d %b, %I:%M %p")


def today_text():
    events = db.events_on(datetime.now().date())
    if not events:
        return "Today:\n  No events"
    lines = ["Today:"]
    lines += [f"  {datetime.fromisoformat(e['start']).strftime('%I:%M %p')}  {e['title']}"
              for e in events]
    return "\n".join(lines)


def tasks_text():
    tasks = db.open_tasks()
    if not tasks:
        return "No open tasks."
    out = []
    for t in tasks:
        due = ""
        if t["due"]:
            d = datetime.fromisoformat(t["due"])
            due = f" (due {d.strftime('%a %d %b') if (d.hour, d.minute) == (23, 59) else fmt(d)})"
        out.append(f"{t['id']}. {t['title']}{due}")
    return "\n".join(out)


def confirm_text(p):
    label = "Event" if p["kind"] == "event" else "Task"
    if p["when"] is None:
        when = "no deadline"
    elif p["has_time"]:
        when = fmt(p["when"])
    else:
        when = p["when"].strftime("%a %d %b") + " (any time)"
    return f"{label}: {p['title']}\nWhen: {when}\nSave? (yes / no, or tell me what to change)"


def save(p):
    if p["kind"] == "event":
        db.add_event(p["title"], p["when"])
    else:
        db.add_task(p["title"], p["when"])
    return f"Saved: {p['title']}"


def find_task(task_id, title):
    tasks = db.open_tasks()
    for t in tasks:
        if t["id"] == task_id:
            return t
    name = (title or "").lower().strip()
    if name:
        hits = [t for t in tasks if name in t["title"].lower() or t["title"].lower() in name]
        if len(hits) == 1:
            return hits[0]
    return None

# ---------- the local LLM call ----------


async def understand(text, st):
    p = st.get("pending")
    tasks = db.open_tasks()[:15]
    events = db.events_on(datetime.now().date())
    ctx = [f"NOW: {datetime.now().strftime('%A %d %B %Y, %I:%M %p')}",
           "OPEN TASKS:\n" + ("\n".join(f"{t['id']}. {t['title']}" for t in tasks) or "none"),
           "TODAY'S EVENTS:\n" + ("\n".join(
               f"{datetime.fromisoformat(e['start']).strftime('%I:%M %p')} {e['title']}"
               for e in events) or "none")]
    if p:
        ctx.append("DRAFT: " + json.dumps({
            "intent": "add_event" if p["kind"] == "event" else "add_task",
            "title": p["title"], "when": p["when_text"]}))
    else:
        ctx.append("DRAFT: none")
    hist = st.get("history", [])[-6:]
    if hist:
        ctx.append("RECENT MESSAGES:\n" + "\n".join(f"{r}: {t[:150]}" for r, t in hist))
    ctx.append(f"MESSAGE: {text}")

    raw = await llm.chat(
        [{"role": "system", "content": PARSER_PROMPT},
         {"role": "user", "content": "\n".join(ctx)}],
        schema=SCHEMA)
    print("LLM:", raw)  # shows up in journalctl; very handy for tuning
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"intent": "chat", "reply": "Sorry, I got tangled up. Could you rephrase that?"}


async def warm():
    try:
        await llm.chat([{"role": "system", "content": PARSER_PROMPT},
                        {"role": "user", "content": "MESSAGE: hi"}],
                       schema=SCHEMA, max_tokens=1)
    except Exception as e:
        print("warm-up failed:", repr(e))


async def local_answer(question, st, note=""):
    raw = await llm.chat(
        [{"role": "system", "content":
          "You are a friendly, concise assistant on a Raspberry Pi with no internet access. "
          "Answer in 1-3 plain sentences. If you are not sure, say so instead of guessing."},
         {"role": "user", "content": question}],
        temperature=0.5, max_tokens=150)
    return note + raw.strip()

# ---------- the cloud path ----------


async def ask_web(question, st):
    st["last_q"] = question
    if CLOUD_ON and db.usage_today("cloud") < CLOUD_LIMIT:
        try:
            text, sources = await cloud.ask(question)
            db.bump_usage("cloud")
            links = "\n".join(f"{i}. {t} - {u}" for i, (t, u) in enumerate(sources, 1))
            return f"{text}\n\nSources:\n{links}" if links else text
        except Exception as e:
            print("cloud error:", repr(e))
    return await local_answer(
        question, st,
        note="I couldn't reach the web (or hit today's limit), so this is from memory and may be wrong:\n")

# ---------- acting on what the model understood ----------


def make_draft(intent, title, when_text, st):
    p = st.get("pending")
    kind = "event" if intent == "add_event" else "task"
    if not title and p:
        title = p["title"]
    if not title:
        return "Sorry, I didn't catch what that's about. Could you rephrase?"

    base = p["when"].date() if (p and p["when"]) else None
    if when_text:
        dt, has_time = resolve(when_text, base)
    elif p:
        dt, has_time, when_text = p["when"], p["has_time"], p["when_text"]
    else:
        dt, has_time = None, False

    need = None
    if when_text and dt is None:
        need = f"I couldn't understand '{when_text}'. When is it? (e.g. 'friday 5pm')"
    elif kind == "event" and dt is None:
        need = "When is it? (e.g. 'tomorrow 3pm')"
    elif kind == "event" and not has_time:
        need = f"What time on {dt.strftime('%a %d %b')}?"
    if kind == "task" and dt is not None and not has_time and need is None:
        dt = dt.replace(hour=23, minute=59)

    st["pending"] = {"kind": kind, "title": title, "when": dt, "when_text": when_text,
                     "has_time": has_time, "need": need, "ts": time.time()}
    return need or confirm_text(st["pending"])


async def act(data, text, st):
    intent = data.get("intent", "chat")
    title = (data.get("title") or "").strip()
    when_text = (data.get("when") or "").strip()

    # safety net: obvious web topics the model filed under chat (never for personal messages)
    if intent == "chat" and WEB_HINTS.search(text) and not PERSONAL.search(text):
        intent = "ask_web"

    if intent == "list_today":
        return today_text()
    if intent == "list_tasks":
        return tasks_text()
    if intent == "complete_task":
        t = find_task(data.get("task_id") or 0, title)
        if not t:
            return "Which task did you finish? Say 'done N'.\n" + tasks_text()
        db.complete_task(t["id"])
        return f"Done: {t['title']}"
    if intent in ("add_event", "add_task"):
        return make_draft(intent, title, when_text, st)
    if intent == "ask_web":
        return await ask_web((data.get("query") or "").strip() or text, st)

    st["last_q"] = text
    return (data.get("reply") or "").strip() or "Hmm, I'm not sure what to say to that."


def remember(st, user_text, bot_text):
    h = st.setdefault("history", [])
    h.append(("user", user_text))
    h.append(("assistant", bot_text))
    del h[:-8]

# ---------- entry point used by bot.py ----------


async def respond(text, st):
    text = text.strip()
    low = text.lower().strip(" .!?")

    p = st.get("pending")
    if p and time.time() - p["ts"] > PENDING_TTL:
        st.pop("pending")
        p = None

    if p:
        if low in NO:
            st.pop("pending")
            return "Cancelled."
        if low in YES:
            if p["need"]:
                return p["need"]
            st.pop("pending")
            return save(p)
        if p["need"] and p["when"] is not None:      # answering "What time?"
            tm = parse_time_only(low)
            if tm:
                p.update(when=datetime.combine(p["when"].date(), tm),
                         has_time=True, need=None, ts=time.time())
                return confirm_text(p)

    m = re.fullmatch(r"done\s+(\d+)", low)
    if m:
        return "Marked done." if db.complete_task(int(m.group(1))) else "No open task with that number."
    if low in ("today", "agenda", "schedule"):
        return today_text()
    if low in ("tasks", "todo", "todos", "list"):
        return tasks_text()

    try:
        m_web = FORCE_WEB.match(text)
        m_loc = FORCE_LOCAL.match(text)
        if m_web:
            out = await ask_web(m_web.group(1).strip(), st)
        elif m_loc:
            out = await local_answer(m_loc.group(1).strip(), st)
        elif low in RECHECK and st.get("last_q"):
            out = await ask_web(st["last_q"], st)
        else:
            data = await understand(text, st)
            out = await act(data, text, st)
    except Exception as e:
        print("error:", repr(e))
        return "My brain isn't responding. Basic commands still work: today, tasks, done N."
    remember(st, text, out)
    return out
