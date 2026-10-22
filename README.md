# Discord AI Self-Bot — Dashboard Edition

A Discord self-bot with a **web dashboard** — start/stop the bot, send DMs to the target user, and view live logs from any browser. Runs on a regular Discord USER ACCOUNT (ToS-violating — see warning below).

> ⚠️ **CRITICAL ToS WARNING — READ FIRST**
>
> Automating a regular Discord user account violates Discord's [Terms of Service](https://discord.com/terms). Discord actively detects and bans self-bots.
>
> - Use a **burner/alt account** you don't mind losing
> - Don't spam, don't mass-DM, don't 24/7 it
> - This project is for educational purposes only

## What this version adds

Compared to the standalone `bot.py`, this version includes a Flask web app (`app.py`) that wraps the bot:

- 📊 **Live dashboard** at `/` — bot status, uptime, recent logs (auto-refreshes every 2s)
- ▶️ **Start / Stop buttons** — control the bot from the browser
- 📨 **Send DM form** — DM the `TARGET_USER` directly from the dashboard
- ♻️ **Reload .env button** — edit config without restarting the web server
- 🚀 **Deploy anywhere** — Dockerfile, Procfile, render.yaml, fly.toml included
- 🗣️ **A voice** — replies as a 15 year old girl texting in 2026 slang (lowercase, short, current Gen Z / Gen Alpha vocabulary). Override with `SYSTEM_PROMPT`
- ⏱️ **A human pace** — reads the message, thinks, then types it out. Long replies go out as two messages
- 🫠 **Human tells, not bot tells** — a quick reaction before the reply, the occasional typo, no markdown, no "let me know if you need anything", and never a question at the end of every sentence
- 🎲 **It doesn't answer everything** — the bot decides whether to reply, react only, or stay silent, and it goes quiet in long bursts
- 🔁 **Two free tiers, one bot** — OpenRouter primary, Hugging Face as automatic failover, so a rate limit moves the load instead of going silent

## Quick Start (local)

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — fill in DISCORD_USER_TOKEN, AI_API_KEY, TARGET_USER_ID

# 3. Start the dashboard (the bot stays off until you click "Start Bot")
python app.py
```

Open <http://localhost:5000> in your browser → click **Start Bot**.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Flask web app (port 5000)                       │
│  ┌────────────────────────────────────────────┐  │
│  │  Browser dashboard (HTML/JS)              │  │
│  │  - Status, Start/Stop, Send DM, Logs       │  │
│  └────────────┬───────────────────────────────┘  │
│               │ HTTP                               │
│  ┌────────────▼───────────────────────────────┐  │
│  │  Flask routes (app.py)                     │  │
│  │  - GET  /api/status                        │  │
│  │  - POST /api/start  /api/stop              │  │
│  │  - POST /api/send  /api/reload             │  │
│  └────────────┬───────────────────────────────┘  │
│               │ in-process shared state           │
│  ┌────────────▼───────────────────────────────┐  │
│  │  Background thread (asyncio loop)          │  │
│  │  - discord.py-self Bot instance            │  │
│  │  - long-lived WebSocket to Discord         │  │
│  └────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

The Flask process and the Discord bot live in the same OS process — Flask handles HTTP requests in the main thread, the bot runs on its own asyncio loop in a background thread. This is why deployment uses `gunicorn -w 1 --threads 4` (single worker so state is shared; multi-threaded so the dashboard stays responsive while the bot runs).

## Deployment Options

### Option A — Render.com (easiest, but free tier spins down)

1. Push this repo to GitHub
2. Go to <https://render.com> → New → Web Service → connect the repo
3. Render will auto-detect `render.yaml` → click Apply
4. In the dashboard, set the secret env vars:
   - `DISCORD_USER_TOKEN`
   - `AI_API_KEY`
   - `TARGET_USER_ID`
5. Deploy

⚠️ **Free tier caveat**: Render free web services spin down after 15 min of inactivity, which kills the bot. For 24/7 operation, upgrade to the $7/mo "Starter" plan.

### Option B — Fly.io (free tier with always-on — recommended)

1. Install `flyctl`: <https://fly.io/docs/hands-on/install-flyctl/>
2. In the project folder:
   ```bash
   fly launch --no-deploy
   fly secrets set DISCORD_USER_TOKEN=your_user_token
   fly secrets set AI_API_KEY=your_ai_key
   fly secrets set TARGET_USER_ID=your_target_id
   fly deploy
   ```
3. Open the URL Fly gives you (e.g. `https://discord-ai-selfbot.fly.dev`)

`fly.toml` is configured with `min_machines_running = 1` so the bot stays alive.

### Option C — Docker on a VPS

1. SSH into your VPS
2. `git clone` this repo
3. `cp .env.example .env` and fill in secrets
4. Run:
   ```bash
   docker build -t discord-selfbot .
   docker run -d --name discord-selfbot --restart=always -p 5000:5000 --env-file .env discord-selfbot
   ```
5. Open `http://YOUR_VPS_IP:5000` — consider putting it behind Caddy/Nginx with HTTPS

### Option D — Local always-on (Raspberry Pi, home server)

1. `git clone` this repo
2. `cp .env.example .env` and fill in secrets
3. `pip install -r requirements.txt`
4. Run with `nohup` or systemd:
   ```bash
   nohup gunicorn -w 1 --threads 4 -t 120 --bind 0.0.0.0:5000 app:app > bot.log 2>&1 &
   ```
5. Or create a systemd service for auto-restart on boot

### Option E — Windows Auto-Start on Boot

**Method 1: Task Scheduler (Recommended)**
1. Right-click `setup-startup.bat` → Run as Administrator
2. The bot will automatically start when you log in to Windows
3. To test immediately: `schtasks /run /tn "Discord Bot"`
4. To remove: `schtasks /delete /tn "Discord Bot" /f`

**Method 2: Startup Folder (Simpler)**
1. Run `add-to-startup.bat` (no admin rights needed)
2. The bot will start when you log in
3. To remove: Delete `Discord-Bot-startup.bat` from `shell:startup`

**Method 3: Manual Startup Folder**
1. Copy `startup-hidden.bat` to your Startup folder:
   - Press Win+R, type `shell:startup`, press Enter
   - Copy `startup-hidden.bat` to that folder
2. Bot will start on next login

## Configuration

All settings live in `.env`. See `.env.example` for the full list. Key ones:

| Variable | Description |
|---|---|
| `DISCORD_USER_TOKEN` | **Required.** User account token (extract via Discord web client DevTools) |
| `AI_PROVIDER` | `openrouter` / `huggingface` / `openai` |
| `AI_API_KEY` | API key from your AI provider |
| `HUGGINGFACE_TOKEN` | Optional **second free tier** — HF token used as failover when the primary provider is rate-limited |
| `FALLBACK_MODELS` | Comma-separated models tried on the same provider, in order |
| `HF_FALLBACK_MODELS` | Comma-separated models tried on the Hugging Face router |
| `AI_MODEL` | Model name (e.g. `poolside/laguna-s-2.1:free`) |
| `TARGET_USER_ID` | Discord user ID — bot will respond to this user in any channel |
| `STARTUP_MESSAGE` | Optional one-time DM to target user on bot startup |
| `SYSTEM_PROMPT` | Leave empty to use the built-in persona (a 15 year old girl texting in 2026 slang). Set it to override the voice. |
| `THINK_BASE_SEC` / `THINK_MAX_SEC` / `THINK_TAIL_SEC` | How long the bot "reads" a message before it starts typing (incl. a long tail, sometimes minutes of silence) |
| `TYPING_BASE_SEC` / `TYPING_PER_CHAR_SEC` / `TYPING_MAX_SEC` | How long the typing indicator stays up, scaled to reply length |
| `JITTER_SEC` | Random variance so no two replies take the same amount of time |
| `TEEN_STYLE` | `1` = rewrite replies into teen texting style (lowercase, no apostrophes) |
| `SPLIT_MESSAGES` | `1` = send long replies as two messages, like a person |
| `EMOJI_CHANCE` | `0`–`1` chance of adding a reaction emoji to a reply |
| `REACTION_CHANCE` | `0`–`1` chance of a quick reaction *before* the reply ("no way", "stoppp") |
| `TYPO_CHANCE` | `0`–`1` chance that a reply has one human typo in it |
| `AI_TEMPERATURE` | Higher = chattier and more slangy |
| `REPLY_CHANCE` | `0`–`1` chance of answering an eligible message. The single most important knob — a bot that answers everything is a bot |
| `REACT_ONLY_CHANCE` | When it doesn't reply, chance it drops an emoji reaction instead of staying dead silent |
| `USE_REACTIONS` | `1` = use real Discord reactions |
| `FOLLOWUP_CHANCE` | `0`–`1` chance of a short second message after the real one |
| `TYPING_PAUSE_CHANCE` | `0`–`1` chance the typing indicator drops off mid-message |
| `PING_REPLY_CHANCE` | `0`–`1` reply (ping) vs plain channel message |
| `DEBOUNCE_SEC` | Messages arriving within this window get one reply, not three |
| `MAX_REPLIES_PER_MIN` | Hard ceiling — above it the bot goes quiet, like a person who stopped reading |
| `PROACTIVE_ENABLED` | `1` = bot sends unprompted messages based on conversation context |
| `PROACTIVE_BASE_SEC` | Base interval for proactive messages (default: 4s, context-aware: 4-10s) |
| `PROACTIVE_VARIANCE_SEC` | Random variance in proactive timing (default: 6s) |

## Free tiers: OpenRouter + Hugging Face

Both providers are configured at the same time and the bot walks the chain:

```
OpenRouter free → Hugging Face fallback
```

A `429` on one model just moves to the next; nothing is ever sent to Discord
except a real reply.

**OpenRouter** — free tier gives 50 requests/day across the `:free` models.
That is the real constraint, which is why `REPLY_CHANCE` matters: a selective
bot spends its quota on the messages that matter.

**Hugging Face** — a read token is enough to use the router, but the account
must have inference credits (`huggingface.co/settings/billing`) or every request
fails with `402 You have no remaining credits`. Until then the HF leg of the
chain is inert — it is wired and ready, it just has nothing to serve.

Verified working OpenRouter free models (as of now):

| Model | Notes |
|---|---|
| `poolside/laguna-s-2.1:free` | Best all-rounder, 1–3.5s |
| `minimax/minimax-m3.1-flash:free` | Fast, good chat |
| `google/gemma-4-31b-it:free` | Good, frequently 429s |
| `meta-llama/llama-3.3-70b-instruct:free` | Smart, slower |

## Making it sound human

The voice is three layers, and all three have to work together — a good prompt
with a bad post-processor still reads as a bot.

1. **The prompt** (`DEFAULT_SYSTEM_PROMPT` in `bot.py`) is short on rules and
   long on example exchanges. A long list of rules ("use rizz, always react,
   always ask a question") teaches a small model to tick a checklist every
   message, which is exactly how an AI sounds. Examples teach rhythm: short
   answers, no trailing question, reaction-only replies. The prompt also tells
   the model **not** to copy the examples verbatim — small models otherwise
   parrot them, which is the single loudest tell there is.
2. **The style pass** (`style_reply`) removes the things a person on a phone
   never types: markdown, capitals, apostrophes, a full stop at the end, double
   spaces. It also strips assistant habits — "as an ai", "let me know if you
   need anything", "hope this helps" — because those phrases are the loudest
   tell there is.
3. **The quirks** — a quick reaction fired off before the real reply, a
   dropped or doubled letter somewhere in the message, a reply that goes out in
   two pieces with a pause between them.
4. **The restraint** (`engagement_gate`) — before any AI call the bot decides
   whether to reply, react only, or stay silent. Messages that mention it,
   reply to it, or come from the target user score high; messages to third
   parties in a busy channel score low. On top of that there's a per-minute
   ceiling and a debounce window. **A bot that answers every message is a bot**,
   so this layer matters more than the wording.

Tuning:

- Too robotic? Lower `REPLY_CHANCE` first (0.7 → 0.5), raise `REACTION_CHANCE`
  and `TYPO_CHANCE`, and try a bigger model —
  `poolside/laguna-s-2.1:free` and `meta-llama/llama-3.3-70b-instruct:free` are
  far more convincing than the 3B default, at the cost of slower replies.
- Too sloppy? Set `TYPO_CHANCE=0` and `REACTION_CHANCE=0`.
- Replies too slow? Lower `TYPING_BASE_SEC` and `TYPING_PER_CHAR_SEC`.
- Replies too fast/robotic? Raise `THINK_BASE_SEC` and `THINK_TAIL_SEC`.
- Reads like a customer-service bot? Set `TEEN_STYLE=0` and write your own
  `SYSTEM_PROMPT` — the style pass is what enforces the texting voice.

## Files

```
discord-bot/
├── bot.py              # Discord self-bot logic (responds to target user, AI calls)
├── app.py              # Flask web dashboard + bot lifecycle management
├── templates/
│   └── dashboard.html  # Dashboard UI (Bootstrap-based, dark theme)
├── requirements.txt    # Python dependencies
├── .env.example        # Configuration template (copy to .env)
├── .gitignore          # Excludes .env, __pycache__, etc.
├── start.bat           # Main startup script (shows console window)
├── startup-hidden.bat  # Hidden startup script (no console, for auto-start)
├── setup-startup.bat   # Sets up Task Scheduler for auto-start (run as admin)
├── add-to-startup.bat  # Adds to Windows Startup folder (no admin needed)
├── Dockerfile          # Container build
├── Procfile            # Heroku-style start command
├── render.yaml         # Render.com Blueprint
├── fly.toml            # Fly.io config (always-on)
└── README.md           # This file
```

## Security Notes

- `.env` is in `.gitignore` — your secrets will NOT be pushed to GitHub
- The dashboard has **no authentication** — anyone who can reach your URL can control the bot
  - For local use this is fine
  - For cloud deployment, put it behind a reverse proxy with HTTP Basic Auth (e.g. Caddy)
  - Or restrict by IP at the firewall level
- Never commit `.env` to git. If you accidentally do, revoke all tokens immediately.

## Troubleshooting

**`Bot is not responding to target user messages`**
- Verify `TARGET_USER_ID` is correct in `.env`
- Check the live logs for `Replying to ...` lines
- Make sure the target user shares a server with your account

**`bot.start() failed: Improper token has been passed`**
- `DISCORD_USER_TOKEN` is missing or invalid
- See README for how to extract a user account token

**Dashboard shows "Bot starting..." forever**
- Check the logs panel — likely an AI API error or token issue
- Click "Stop Bot", fix `.env`, click "Reload .env", then "Start Bot" again

**`!send` returns "Can't DM that user"**
- Target user has DMs disabled from server members
- Ask them to send the bot a DM first to open a channel

**Render free tier: bot dies after 15 min**
- Render spins down free web services when idle
- Upgrade to paid plan, or use Fly.io's free always-on tier

**Windows auto-start not working**
- For Task Scheduler: Make sure you ran `setup-startup.bat` as Administrator
- Check Task Scheduler: Open Task Scheduler → Task Scheduler Library → Find "Discord Bot"
- For Startup folder: Run `shell:startup` and verify `Discord-Bot-startup.bat` exists
- Check if the bot is running: Open Task Manager → Look for pythonw.exe or python.exe

## License & Disclaimer

This code is provided for **educational purposes only**. Using it on a real Discord account violates Discord's Terms of Service. The authors take no responsibility for banned accounts, lost data, or any consequences. If you want a legitimate AI bot, build a normal Discord bot application instead.
