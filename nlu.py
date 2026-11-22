import re
from dateparser.search import search_dates

SETTINGS = {"PREFER_DATES_FROM": "future", "TIMEZONE": "Asia/Kolkata",
            "RETURN_AS_TIMEZONE_AWARE": False}

TIME_HINT = re.compile(r"\d|am\b|pm\b|noon|midnight|morning|afternoon|evening|night", re.I)
EXPLICIT_AMPM = re.compile(r"am\b|pm\b|morning|afternoon|evening|night|noon|midnight", re.I)
RELATIVE = re.compile(r"\b(in|hours?|minutes?|mins?)\b", re.I)
FILLER = re.compile(r"^(please\s+)?(remind me to|remind me|i need to|i have to|"
                    r"i have an|i have a|i have|add|todo|task|event|schedule)\s+", re.I)

def parse(text):
    t = text.strip()
    low = t.lower()

    if low in ("today", "agenda", "schedule", "what's today"):
        return {"kind": "today"}
    if low in ("tasks", "todo", "todos", "list"):
        return {"kind": "tasks"}
    m = re.match(r"done\s+(\d+)$", low)
    if m:
        return {"kind": "done", "id": int(m.group(1))}

    force_task = low.startswith(("task ", "todo "))
    found = search_dates(t, settings=SETTINGS)
    phrase, when = (max(found, key=lambda x: len(x[0])) if found else (None, None))

    title = t
    if phrase:
        title = re.sub(re.escape(phrase), " ", t, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    title = FILLER.sub("", title)
    title = re.sub(r"\b(at|on|by|for|in|from)\s*$", "", title, flags=re.I).strip(" ,.-")
    title = title or "(untitled)"

    if when is None:
        return {"kind": "task", "title": title, "when": None}

    has_time = bool(TIME_HINT.search(phrase))
    if has_time and not EXPLICIT_AMPM.search(phrase) and not RELATIVE.search(phrase):
        if 1 <= when.hour <= 6:           # "at 3" almost always means 3 PM
            when = when.replace(hour=when.hour + 12)

    if force_task or not has_time:
        due = when if has_time else when.replace(hour=23, minute=59, second=0, microsecond=0)
        return {"kind": "task", "title": title, "when": due}
    return {"kind": "event", "title": title, "when": when}
