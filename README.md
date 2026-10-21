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

## Configuration

All settings live in `.env`. See `.env.example` for the full list. Key ones:

| Variable | Description |
|---|---|
| `DISCORD_USER_TOKEN` | **Required.** User account token (extract via Discord web client DevTools) |
| `AI_PROVIDER` | `openrouter` / `huggingface` / `openai` |
| `AI_API_KEY` | API key from your AI provider |
| `AI_MODEL` | Model name (e.g. `meta-llama/llama-3.2-3b-instruct:free`) |
| `TARGET_USER_ID` | Discord user ID — bot will respond to this user in any channel |
| `STARTUP_MESSAGE` | Optional one-time DM to target user on bot startup |

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

## License & Disclaimer

This code is provided for **educational purposes only**. Using it on a real Discord account violates Discord's Terms of Service. The authors take no responsibility for banned accounts, lost data, or any consequences. If you want a legitimate AI bot, build a normal Discord bot application instead.
