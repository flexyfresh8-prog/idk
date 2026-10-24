# Discord AI Self-Bot

A tiny Discord self-bot that replies to one person like a real human — random read/think/typing delays, short casual replies, and it even texts them first sometimes. Runs on a regular Discord USER ACCOUNT.

> ⚠️ **ToS WARNING**: Automating a regular Discord account violates Discord's [Terms of Service](https://discord.com/terms) and can get the account banned. Use a burner account. Educational purposes only.

## Setup

1. Install Python (with "Add to PATH" checked), then double-click **`start.bat`** — it installs dependencies and runs the bot.
2. On first launch the bot asks you for the 3 values in the console and saves them to `.env` automatically:
   - `DISCORD_USER_TOKEN` — your account token (browser: F12 → Network → any request → copy the `authorization` header)
   - `AI_API_KEY` — free key from https://openrouter.ai/keys
   - `TARGET_USER_ID` — right-click the person → Copy User ID (enable Developer Mode in Discord settings)

That's it. The bot now replies to that person (and DMs) with human-like timing, and texts them first every ~45 minutes. To change anything later, edit `.env` — or delete it and run again to redo setup.

## Built-in protection

- **Auto-restart** — if the internet drops or anything crashes, the bot restarts itself with capped backoff. A bad token stops with a clear message instead of retrying forever.
- **Secret-safe logs** — the token and API key are scrubbed out of every log line, so a crash dump can never leak them.
- **Locked-down `.env`** — the config file's permissions are restricted to your Windows user only.
- **No double replies** — reconnect replays and overlapping AI calls are deduplicated and serialized.
- **Fail-fast config check** — a mistyped token / user ID / key is caught before login with a hint on how to fix it.

## Files

```
bot.py            everything — the whole bot, one file
start.bat         double-click to run
.env.example      config template
requirements.txt  3 dependencies
```

Optional settings in `.env`: `AI_MODEL`, `SYSTEM_PROMPT` (personality), `PROACTIVE_MINUTES` (0 = never text first).
