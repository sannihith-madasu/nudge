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
REMIND = re.compile(r"^\s*(please\s+)?remind me\b", re.I)
WHEN_STOP = {"at", "on", "by", "the", "in", "of", "next", "this", "a", "an", "am", "pm",
             "and", "to", "from"}
FIND_STOP = {"done", "with", "that", "thing", "finished", "just", "task", "tasks", "have",
             "from", "this", "the", "did", "been", "completed"}

# ---------- the prompt (static part first, so Ollama can cache it) ----------

PARSER_PROMPT = """You are the brain of a personal assistant on the user's Raspberry Pi. Read the user's MESSAGE and reply with one JSON object.

Fields:
- intent: one of
  add_event: something at a specific clock time (appointment, meeting, call, class), or "remind me ... at <time>".
  add_task: a to-do with no clock time, or only a date/deadline ("by friday", "tomorrow"). "remind me to X" with no time is add_task.
  complete_task: the user says they finished/did a task from OPEN TASKS. Set task_id.
  list_today: asks what is on today / their schedule.
  list_tasks: asks what is pending / their to-dos.
  ask_web: needs facts from the internet: news, current events, scores, prices, weather, anything recent, facts about specific people/places/products/companies, health/legal/money questions, "look up ...".
  chat: feelings, struggling, procrastinating or feeling unmotivated ("i can't get started"), motivation, small talk, jokes, opinions, advice, thanks, simple explanations of how things work, writing help, anything about the user themselves. Never turn feelings into a task.
- title: short action phrase for add_event/add_task, WITHOUT any date or time words. For every other intent: "".
- when: date/time words copied exactly from the MESSAGE ("tomorrow at 3pm", "friday", "in 2 hours", "23rd"). Never use NOW. If the MESSAGE has no date or time words, when is "". For every other intent: "".
- task_id: id from OPEN TASKS for complete_task, otherwise 0.
- query: only for ask_web: the question rewritten to stand alone (max 15 words) with NO personal details or feelings. Otherwise "".
- reply: only for chat. 1-3 short plain sentences, warm and direct. If you don't know something, say so. Never invent facts about the user's schedule. Otherwise "".

People rarely say "add a task". Infer the intent from natural speech.
If DRAFT is not "none", the user is probably correcting or completing it: keep the draft's title and intent unless they change them, and put only the new date/time words in "when". If the message is clearly about something else, ignore the draft.

Examples:
MESSAGE: i have a haircut thursday at 5
{"intent":"add_event","title":"haircut","when":"thursday at 5","task_id":0,"query":"","reply":""}
MESSAGE: gotta send the invoice by monday
{"intent":"add_task","title":"send the invoice","when":"monday","task_id":0,"query":"","reply":""}
MESSAGE: remind me to stretch
{"intent":"add_task","title":"stretch","when":"","task_id":0,"query":"","reply":""}
MESSAGE: remind me to take my medicine at 9pm
{"intent":"add_event","title":"take my medicine","when":"9pm","task_id":0,"query":"","reply":""}
MESSAGE: finished the laundry   (OPEN TASKS: 7. do laundry)
{"intent":"complete_task","title":"do laundry","when":"","task_id":7,"query":"","reply":""}
MESSAGE: anything on tonight?
{"intent":"list_today","title":"","when":"","task_id":0,"query":"","reply":""}
MESSAGE: who won the match last night
{"intent":"ask_web","title":"","when":"","task_id":0,"query":"who won the match last night","reply":""}
MESSAGE: should i take paracetamol for a headache
{"intent":"ask_web","title":"","when":"","task_id":0,"query":"is paracetamol safe to take for a headache","reply":""}
MESSAGE: stressed about exams, any news on the jee results?
{"intent":"ask_web","title":"","when":"","task_id":0,"query":"latest news on JEE results","reply":""}
MESSAGE: i keep putting off my project
{"intent":"chat","title":"","when":"","task_id":0,"query":"","reply":"That happens to everyone. What is the smallest first step, something you could do in five minutes? Start there."}
MESSAGE: ugh i'm so bored and unmotivated
{"intent":"chat","title":"","when":"","task_id":0,"query":"","reply":"Rough patch. Pick the easiest thing on your list and give it five minutes, then see how you feel."}
MESSAGE: how does wifi work
{"intent":"chat","title":"","when":"","task_id":0,"query":"","reply":"Wi-Fi sends data as radio waves between your device and a router, which is connected to the internet."}
MESSAGE: make it saturday   (DRAFT: add_event, title "gym", when "friday at 6")
{"intent":"add_event","title":"gym","when":"saturday","task_id":0,"query":"","reply":""}
MESSAGE: next week   (DRAFT: add_task, title "book flights")
{"intent":"add_task","title":"book flights","when":"next week","task_id":0,"query":"","reply":""}
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


def grounded(when_text, text):
    """True if the date words really come from the user's message (small models sometimes
    copy the NOW line from the prompt instead)."""
    low = text.lower()
    toks = [t for t in re.findall(r"[a-z]+|\d+", when_text.lower()) if t not in WHEN_STOP]
    if not toks:
        return True
    return sum(1 for t in toks if t in low) / len(toks) >= 0.5

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


def find_task(task_id, title, text=""):
    tasks = db.open_tasks()
    for t in tasks:
        if t["id"] == task_id:
            return t
    name = (title or "").lower().strip()
    if name:
        hits = [t for t in tasks if name in t["title"].lower() or t["title"].lower() in name]
        if len(hits) == 1:
            return hits[0]
    words = {w for w in re.findall(r"[a-z]{4,}", f"{title} {text}".lower()) if w not in FIND_STOP}
    hits = [t for t in tasks if any(w in t["title"].lower() for w in words)]
    return hits[0] if len(hits) == 1 else None

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
          "You are Nudge, a warm, concise personal assistant on a Raspberry Pi with no internet "
          "access. Answer in 1-3 plain sentences. If the user is struggling, be kind and suggest "
          "one tiny next step. If you are not sure of a fact, say so instead of guessing."},
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

    # guard 1: date words must come from the user's message, not be invented
    if when_text and not grounded(when_text, text):
        when_text = ""
    # guard 2: "remind me to X" with no time is a task, not an event
    if intent == "add_event" and not when_text and REMIND.match(text):
        intent = "add_task"
    # guard 3: obvious web topics the model filed under chat (never for personal messages)
    if intent == "chat" and WEB_HINTS.search(text) and not PERSONAL.search(text):
        intent = "ask_web"

    if intent == "list_today":
        return today_text()
    if intent == "list_tasks":
        return tasks_text()
    if intent == "complete_task":
        t = find_task(data.get("task_id") or 0, title, text)
        if not t:
            return "Which task did you finish? Say 'done N'.\n" + tasks_text()
        db.complete_task(t["id"])
        return f"Done: {t['title']}"
    if intent in ("add_event", "add_task"):
        return make_draft(intent, title, when_text, st)
    if intent == "ask_web":
        return await ask_web((data.get("query") or "").strip() or text, st)

    st["last_q"] = text
    reply = (data.get("reply") or "").strip()
    if not reply:                       # guard 4: the model forgot to write a chat reply
        reply = await local_answer(text, st)
    return reply


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
