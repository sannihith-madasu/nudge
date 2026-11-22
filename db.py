import sqlite3
from datetime import datetime, timedelta

DB = "assistant.db"

def _run(sql, args=(), fetch=False):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(sql, args)
        rows = cur.fetchall() if fetch else None
        c.commit()
        return rows if fetch else cur
    finally:
        c.close()

def init():
    _run("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL)""")
    _run("""CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, due TEXT, done INTEGER DEFAULT 0)""")

def add_event(title, start):
    return _run("INSERT INTO events(title, start) VALUES (?,?)",
                (title, start.isoformat(timespec="seconds"))).lastrowid

def add_task(title, due=None):
    d = due.isoformat(timespec="seconds") if due else None
    return _run("INSERT INTO tasks(title, due) VALUES (?,?)", (title, d)).lastrowid

def events_on(day):
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    return _run("SELECT * FROM events WHERE start >= ? AND start < ? ORDER BY start",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                fetch=True)

def open_tasks():
    return _run("SELECT * FROM tasks WHERE done = 0 ORDER BY due IS NULL, due, id", fetch=True)

def complete_task(task_id):
    return _run("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,)).rowcount > 0

def init_extra():
    _run("""CREATE TABLE IF NOT EXISTS banners(
        id INTEGER PRIMARY KEY, kind TEXT, text TEXT, created TEXT, expires TEXT)""")
    _run("CREATE TABLE IF NOT EXISTS fired(key TEXT PRIMARY KEY)")

def post_banner(kind, text, minutes=10):
    now = datetime.now()
    _run("INSERT INTO banners(kind,text,created,expires) VALUES (?,?,?,?)",
         (kind, text, now.isoformat(timespec="seconds"),
          (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")))

def current_banner():
    rows = _run("SELECT * FROM banners WHERE expires > ? ORDER BY id DESC LIMIT 1",
                (datetime.now().isoformat(timespec="seconds"),), fetch=True)
    return dict(rows[0]) if rows else None

def once(key):
    """True the first time a key is seen, False afterwards (prevents repeat alerts)."""
    try:
        _run("INSERT INTO fired(key) VALUES (?)", (key,))
        return True
    except sqlite3.IntegrityError:
        return False

def usage_today(kind):
    _run("""CREATE TABLE IF NOT EXISTS usage(
        day TEXT, kind TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(day, kind))""")
    rows = _run("SELECT n FROM usage WHERE day=? AND kind=?",
                (datetime.now().date().isoformat(), kind), fetch=True)
    return rows[0]["n"] if rows else 0

def bump_usage(kind):
    usage_today(kind)  # makes sure the table exists
    _run("""INSERT INTO usage(day, kind, n) VALUES (?,?,1)
            ON CONFLICT(day, kind) DO UPDATE SET n = n + 1""",
         (datetime.now().date().isoformat(), kind))
