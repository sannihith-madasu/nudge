# Nudge

A self-hosted personal assistant for the Raspberry Pi. You talk to it on Telegram in plain English, and it runs your calendar, to-do list, and reminders, chats with you, and looks things up on the web with sources. A small display on your desk shows your day and nudges you about what to do next.

Built for people who don't keep a calendar and procrastinate: the assistant *is* the calendar, the to-do list, and the accountability partner.

> Status: personal project, actively developed. It works end to end, but expect rough edges (see [Known gaps](#known-gaps)).

## Features

- **Natural language scheduling.** "i have a dentist thing friday 4pm" or "gotta submit the report by sunday" becomes an event or task. You don't need to say "add a task".
- **Local LLM brain.** A small model (Qwen 2.5 1.5B by default) runs on the Pi through Ollama. It understands your messages, keeps context, and chats. Personal and emotional messages never leave the device.
- **Reliable dates.** The model only picks out the date words; plain code turns them into real timestamps. Nothing is saved until you confirm, and you can correct a draft ("on the 23rd", "make it friday").
- **Web answers with sources.** Questions about news, scores, weather, or facts are sent (as a short anonymous question) to Tavily for search, and Gemini writes a concise answer. Real source links are attached.
- **Smart routing.** The assistant decides what stays local and what goes to the web. You can override it.
- **Morning briefing, hourly check-ins, event reminders, evening review.**
- **Always-on display.** Full-screen dashboard with clock, today's events, and tasks. Alerts take over the screen.
- **Private by design.** The bot only responds to your own Telegram account, and the display server is not exposed to your network.

## How it works

```
Telegram message -> bot.py -> brain.py
                                |-- instant commands: today / tasks / done N
                                |-- local LLM (Ollama): intent + chat
                                |     |-- add_event / add_task -> dateparser -> confirm -> SQLite (db.py)
                                |     |-- chat -> reply from the local model
                                |     `-- ask_web -> cloud.py: Tavily search -> Gemini writes answer -> sources
                                `-- overrides: "web: ...", "local: ...", "are you sure?"

scheduler.py: briefing, check-ins, reminders -> banners -> ui.py (FastAPI) -> static/index.html (kiosk display)
```

Four services run side by side:

| Service | File | Job |
|---|---|---|
| Bot | `bot.py`, `brain.py` | Receives Telegram messages, routes them, confirms and saves data |
| Scheduler | `scheduler.py` | Posts briefings, check-ins, and reminders |
| Display server | `ui.py` | Serves the dashboard page and a small JSON API |
| Model server | Ollama | Runs the local LLM on `127.0.0.1` |

### What stays local, what goes to the cloud

| Stays on the Pi | Goes to the web |
|---|---|
| Tasks, schedule, memory of the conversation | News, scores, prices, weather |
| Feelings, motivation, small talk | Facts about people, places, products |
| Advice, opinions, simple explanations | Health, legal, and money questions |
| Writing help, brainstorming | Anything you ask with `web:` |

## Requirements

- Raspberry Pi 5 (developed on 4 GB) running Raspberry Pi OS
- Python 3.11+
- [Ollama](https://ollama.com) with a small model (about 1 GB of RAM)
- A display (developed with a 5" 800x480 HDMI screen)
- A Telegram account and a bot token from [@BotFather](https://t.me/BotFather)
- Optional, for web answers: a free [Tavily](https://app.tavily.com) key and a free [Gemini](https://aistudio.google.com) key. Neither needs a credit card.

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

**3. Install the local model**

```
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b
ollama run qwen2.5:1.5b "say hi"
```

Other small models work too. Set `LLM_MODEL` in `.env`, and use `compare.py` to benchmark them on your own Pi.

**4. Get the optional web keys**

- Tavily: sign up at app.tavily.com (1,000 free credits per month, no card).
- Gemini: create a key at aistudio.google.com.

Without these keys the assistant still works. Web questions are answered by the local model with a "may be wrong" warning.

**5. Configure**

```
cp .env.example .env
nano .env
chmod 600 .env
```

| Variable | Meaning | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather | required |
| `ALLOWED_CHAT_ID` | Your chat ID; all other senders are ignored | required |
| `TIMEZONE` | Your timezone, e.g. `Asia/Kolkata` | `Asia/Kolkata` |
| `WAKE_TIME` | Morning briefing time (24h) | `07:00` |
| `REVIEW_TIME` | Evening review time | `21:00` |
| `CHECKIN_START` / `CHECKIN_END` | Hours for hourly check-ins | `9` / `21` |
| `NOTIFY_TELEGRAM` | Also send alerts to Telegram (`1`/`0`) | `1` |
| `LLM_MODEL` | Local Ollama model | `qwen2.5:1.5b` |
| `LLM_KEEP_ALIVE` | How long the model stays in RAM | `30m` |
| `OLLAMA_URL` | Ollama address | `http://127.0.0.1:11434` |
| `TAVILY_API_KEY` | Web search key | optional |
| `GEMINI_API_KEY` | Answer-writing key | optional |
| `CLOUD_MODELS` | Gemini models to try, in order | `gemini-3.5-flash,gemini-3.6-flash,gemini-3.5-flash-lite` |
| `CLOUD_DAILY_LIMIT` | Max web questions per day | `25` |
| `CLOUD_ENABLED` | Turn the web path on/off (`1`/`0`) | `1` |

Also set the Pi's own timezone so dates and alerts line up:

```
sudo timedatectl set-timezone Your/Timezone
```

**6. Run it manually (to test)**

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

**7. Run it on boot (systemd)**

Template service files are in `deploy/`. Edit the placeholders (`/path/to/assistant` and `YOUR_USERNAME`), then:

```
sudo cp deploy/assistant-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now assistant-bot assistant-scheduler assistant-ui
```

To launch the dashboard automatically on a labwc desktop, add the line from `deploy/labwc-autostart` to `~/.config/labwc/autostart`. Ollama installs its own service.

After editing `.env`, restart the services that read it: `sudo systemctl restart assistant-bot assistant-scheduler`.

## Usage

Just talk to it.

| You send | What happens |
|---|---|
| `i have a dentist appointment tomorrow at 3` | Drafts an event, asks you to confirm |
| `gotta submit the report by friday` | Drafts a task with a deadline |
| `remind me to drink water` | Drafts a task with no deadline |
| `remind me to call mom at 7pm` | Drafts an event at 7 PM |
| `on the 23rd` / `3` / `make it friday` | Corrects or completes the pending draft |
| `yes` / `no` | Confirms or cancels the pending draft |
| `i finished the milk one` | Marks the matching task done |
| `today` / `tasks` / `done 3` | Instant commands, no model needed |
| `what's the latest news on ISRO` | Searches the web, answers with sources |
| `i'm feeling down` | Stays local, replies with a short supportive message |
| `web: ...` / `local: ...` | Forces the web or the local model |
| `are you sure?` / `check online` | Re-asks your last question on the web |

Nothing is saved until you confirm. A bare hour like "at 3" is read as 3 PM; say "6am" for mornings.

## Project layout

```
bot.py            Telegram adapter
brain.py          Router, prompts, dates, drafts, local and web paths
llm.py            Local model client (Ollama)
cloud.py          Web answers: Tavily search + Gemini writer
db.py             SQLite storage helpers
scheduler.py      Briefings, check-ins, reminders
ui.py             Display server (FastAPI)
static/index.html Dashboard page (800x480)
compare.py        Benchmarks local models on your Pi
tests/            Helper scripts (e.g. Gemini model checker)
deploy/           Template systemd services and kiosk autostart
.env.example      Configuration template
```

## Privacy and security

- Secrets live in `.env`, which is git-ignored. Never commit it.
- The bot ignores every chat except `ALLOWED_CHAT_ID`.
- The display server and Ollama bind to `127.0.0.1`, so they are not reachable from your network.
- Your data stays in a local `assistant.db` file, also git-ignored.
- **What leaves the Pi:** your Telegram messages travel through Telegram. Web questions are rewritten by the local model into a short, standalone question (emails and long numbers are masked). Tavily receives that question, and Gemini receives it plus public search snippets. Tasks, schedule, and personal messages are never sent. Free tiers may use submitted data to improve the provider's products, so read their terms.
- If a key ever leaks, revoke it with the provider and replace it.

## Performance notes

On a Pi 5 with 4 GB and a 1.5B model:

- Instant commands (`today`, `tasks`, `done N`) are immediate.
- Scheduling and chat replies take roughly 5 to 15 seconds.
- Web answers take longer, since they add a search and a second model call.
- RAM is tight with the display and services running. Stick to ~1 to 1.5B-parameter models.

## Known gaps

- **Check-ins don't understand replies.** If Nudge asks "are you working on X?" and you answer "yeah" or "later", it is treated as a brand new message.
- **Event alerts are approximate.** They fire up to 15 minutes before, not at the exact start time, and there is no snooze.
- **No long-term memory yet.** It remembers only the last few messages, not facts about you.
- **The display is output-only.** It shows the clock, events, tasks, and alerts, but not chat answers, and touch is not set up.
- **Weather uses a web search** instead of a dedicated free weather API.
- **The local model is small.** It can misclassify a message, and it is weak at facts, which is why factual questions are routed to the web. The confirmation step is the safety net.
- **Web answers depend on free tiers** that can change or run out. If the search fails, you get a clearly marked local answer instead.
- **No camera, voice, or enclosure yet** (see the roadmap).
- Single user only.

## Roadmap

- Memory, persona, and mood: "remember that...", a daily mood check-in, a warmer tone, and a fixed safety response for serious distress
- Smarter check-ins that understand natural replies, plus exact-time alerts and snooze
- Free local weather tool
- Camera check-ins (one still frame every ~30 minutes, processed locally and never stored)
- Automatic time-blocking of tasks
- Voice: wake word, speech-to-text, and text-to-speech
- 3D-printed enclosure

## License

MIT. See [LICENSE](LICENSE).
