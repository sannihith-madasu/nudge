# Pi Assistant

A small, self-hosted personal assistant for a Raspberry Pi. You send it plain-English messages on Telegram ("dentist tomorrow at 3", "finish assignment by friday"), it stores them in a local database, and a small display on your desk shows your day and nudges you about what to do next.

Built for people who don't keep a calendar and procrastinate: the assistant *is* the calendar, the to-do list, and the reminder system.

## Features

- **Add things by text.** Events and tasks are parsed from natural language and confirmed before saving.
- **Local storage.** Everything lives in a SQLite file on the Pi.
- **Morning briefing.** Today's events and top tasks at your wake time.
- **Hourly check-ins.** Asks about your top open task during your active hours.
- **Event reminders.** A heads-up 15 minutes before each event.
- **Evening review.** Summary of what's still open.
- **Always-on display.** Full-screen dashboard with clock, today's events, and tasks. Alerts take over the screen.
- **Private by design.** The bot only responds to your own Telegram account.

## How it works

```
Telegram message -> bot.py -> nlu.py (parsing) -> db.py (SQLite)
                                                     |
                      scheduler.py (briefing, check-ins, reminders)
                                                     |
                  ui.py (FastAPI) -> static/index.html (kiosk display)
```

Three small services run side by side:

| Service | File | Job |
|---|---|---|
| Bot | `bot.py` | Receives Telegram messages, asks for confirmation, saves data |
| Scheduler | `scheduler.py` | Posts briefings, check-ins, and reminders |
| Display server | `ui.py` | Serves the dashboard page and a small JSON API |

## Requirements

- Raspberry Pi (developed on a Pi 5, 4 GB) running Raspberry Pi OS
- Python 3.11+
- A display (developed with a 5" 800x480 HDMI screen)
- A Telegram account and a bot token from [@BotFather](https://t.me/BotFather)
- Internet access (for Telegram only; there are no paid services)

## Setup

**1. Get the code and install dependencies**

```
git clone <your-repo-url>
cd <repo-folder>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**2. Create your Telegram bot**

1. Message @BotFather, send `/newbot`, and save the token.
2. Send your new bot a message.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy the number in `"chat": {"id": ...}`. That is your chat ID.

**3. Configure**

```
cp .env.example .env
nano .env
chmod 600 .env
```

| Variable | Meaning | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather | required |
| `ALLOWED_CHAT_ID` | Your chat ID; all other senders are ignored | required |
| `TIMEZONE` | Your timezone, e.g. `Asia/Kolkata` | - |
| `WAKE_TIME` | Morning briefing time (24h) | `07:00` |
| `REVIEW_TIME` | Evening review time | `21:00` |
| `CHECKIN_START` / `CHECKIN_END` | Hours for hourly check-ins | `9` / `21` |
| `NOTIFY_TELEGRAM` | Also send alerts to Telegram (`1`/`0`) | `1` |

Also set the Pi's own timezone so dates resolve correctly:

```
sudo timedatectl set-timezone Your/Timezone
```

**4. Run it manually (to test)**

Use three terminals, each with the virtual environment active:

```
python bot.py
python scheduler.py
uvicorn ui:app --host 127.0.0.1 --port 8000
```

Then open `http://localhost:8000` in a browser, or in kiosk mode:

```
chromium --kiosk http://localhost:8000
```

**5. Run it on boot (systemd)**

Template service files are in `deploy/`. Edit the placeholders (`/path/to/assistant` and `YOUR_USERNAME`), then:

```
sudo cp deploy/assistant-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now assistant-bot assistant-scheduler assistant-ui
```

To launch the dashboard automatically on a labwc desktop, add the line from `deploy/labwc-autostart` to `~/.config/labwc/autostart`.

## Usage

Message your bot:

| You send | What happens |
|---|---|
| `dentist tomorrow at 3` | Creates an event, asks you to confirm |
| `finish assignment by friday` | Creates a task with a deadline |
| `task buy milk` | Creates a task with no deadline |
| `remind me to call mom at 7pm` | Creates an event at 7 PM |
| `today` | Lists today's events |
| `tasks` | Lists open tasks |
| `done 3` | Marks task 3 as done |
| `yes` / `no` | Confirms or cancels the pending item |

Nothing is saved until you confirm. A bare hour like "at 3" is read as 3 PM; say "6am" for mornings.

## Project layout

```
bot.py            Telegram bot
nlu.py            Text -> structured event/task (dateparser)
db.py             SQLite storage helpers
scheduler.py      Briefings, check-ins, reminders
ui.py             Display server (FastAPI)
static/index.html Dashboard page (800x480)
deploy/           Template systemd services and kiosk autostart
.env.example      Configuration template
```

## Privacy and security

- Your token and chat ID live in `.env`, which is git-ignored. Never commit it.
- The bot ignores every chat except `ALLOWED_CHAT_ID`.
- The display server binds to `127.0.0.1`, so it is not reachable from your network.
- Your data stays in a local `assistant.db` file, also git-ignored.
- If a token ever leaks, revoke it with `/revoke` in @BotFather.

## Roadmap

- Camera check-ins (one still frame every ~30 minutes, processed locally and never stored)
- Automatic time-blocking of tasks
- Weather in the morning briefing
- Local speech: wake word, speech-to-text, and text-to-speech
- Small local LLM fallback for messy phrasing
- 3D-printed enclosure

## Known limitations

- Date parsing is rule-based and can misread unusual phrasing; the confirmation step is the safety net.
- The display is output-only; confirmations happen in Telegram.
- Single user only.

## License

MIT.
