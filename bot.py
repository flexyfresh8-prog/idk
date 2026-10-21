"""
Discord AI Self-Bot (User Account)
==================================
A self-bot that runs on a regular Discord USER ACCOUNT (not a bot application)
and automatically types AI-generated responses.

!! ToS WARNING !!
Automating a regular Discord user account violates Discord's Terms of Service.
If Discord detects this, the account can be PERMANENTLY BANNED. Use a burner/alt
account you don't mind losing. You have been warned.

Features:
- Runs on a regular user account token (extracted from Discord client).
- Responds to messages from a TARGET_USER in any channel (no mention needed),
  and/or to anyone who @mentions the account, and/or to DMs.
- Can proactively DM the TARGET_USER via the !send command, and optionally
  auto-message them on startup.
- Simulates human "typing..." indicator before sending.
- Remembers last N messages per channel for conversational continuity.
- Supports Hugging Face, OpenRouter, OpenAI, or any OpenAI-compatible endpoint.
- Prefix commands (only the account owner can run them):
    !ping   — latency check
    !clear  — clear bot memory in the current channel
    !send   — DM the target user with a message
    !ai     — ask the AI a one-off question without history

Usage:
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and fill in DISCORD_USER_TOKEN + AI + TARGET_USER_ID
  3. python bot.py
"""

from __future__ import annotations

import os
import asyncio
import logging
from collections import deque
from typing import Deque, Dict, List

import discord
from discord.ext import commands
from dotenv import load_dotenv
from openai import OpenAI

# NOTE: This file uses the `discord.py-self` fork (installed as the `discord` package).
# pip install discord.py-self  (NOT discord.py — they conflict!)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN", "")
# NOTE: This must be a USER ACCOUNT token (extracted from the Discord client),
# NOT a bot application token from the Developer Portal. Using a bot token here
# will fail to connect. See README for how to extract your user token.

# ---------------------------------------------------------------------------
# AI provider (all of these are OpenAI-compatible — pick one)
# Set AI_PROVIDER and the corresponding API key + model in your .env.
#   openrouter   -> https://openrouter.ai/api/v1 (free models with :free suffix)
#   huggingface  -> https://router.huggingface.co/v1 (HF Inference Providers router)
#   openai       -> https://api.openai.com/v1
#   custom       -> use AI_BASE_URL/AI_API_KEY/AI_MODEL directly
# ---------------------------------------------------------------------------
AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter").lower().strip()

_PROVIDER_PRESETS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.2-3b-instruct:free",
        "extra_headers": {
            # Optional ranking headers (recommended by OpenRouter):
            "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://github.com/local/discord-bot"),
            "X-Title": os.getenv("OPENROUTER_TITLE", "Discord AI Bot"),
        },
    },
    "huggingface": {
        "base_url": "https://router.huggingface.co/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "extra_headers": {},
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "extra_headers": {},
    },
}

_preset = _PROVIDER_PRESETS.get(AI_PROVIDER, {})
AI_BASE_URL = os.getenv("AI_BASE_URL") or _preset.get("base_url", "https://openrouter.ai/api/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")  # provider-specific key (HF token / OR key / OpenAI key)
AI_MODEL = os.getenv("AI_MODEL") or _preset.get("model", "meta-llama/llama-3.2-3b-instruct:free")
AI_EXTRA_HEADERS = _preset.get("extra_headers", {})

# ---------------------------------------------------------------------------
# Target user — bot will respond to this user in ANY channel, and !send will DM them.
# To find a user ID: enable Developer Mode in Discord (Settings -> Advanced),
# then right-click a user -> Copy ID.
# Leave empty to disable (bot will only respond to @mentions and DMs).
# ---------------------------------------------------------------------------
TARGET_USER_ID = os.getenv("TARGET_USER_ID", "").strip()

# Optional: message to DM the target user once when the bot comes online.
# Leave empty to disable.
STARTUP_MESSAGE = os.getenv("STARTUP_MESSAGE", "").strip()


# Behavior settings
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful, concise AI assistant responding in a Discord chat. "
    "Keep replies focused and naturally conversational — no bullet-point spam, "
    "no markdown headers unless explicitly asked. Use plain text style.",
)
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "10"))  # messages remembered per channel
MAX_RESPONSE_CHARS = int(os.getenv("MAX_RESPONSE_CHARS", "1900"))  # stay under 2000 limit
TYPING_BASE_SEC = float(os.getenv("TYPING_BASE_SEC", "1.5"))  # minimum typing delay
TYPING_PER_CHAR_SEC = float(os.getenv("TYPING_PER_CHAR_SEC", "0.012"))  # added per char
TYPING_MAX_SEC = float(os.getenv("TYPING_MAX_SEC", "8.0"))  # cap on typing delay

# Whitelist (empty = all channels bot can see). Comma-separated channel IDs.
CHANNEL_WHITELIST = [
    cid.strip() for cid in os.getenv("CHANNEL_WHITELIST", "").split(",") if cid.strip()
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ai-bot")

# ---------------------------------------------------------------------------
# OpenAI-compatible client — works with Hugging Face, OpenRouter, OpenAI, etc.
# ---------------------------------------------------------------------------
ai_client = OpenAI(
    base_url=AI_BASE_URL,
    api_key=AI_API_KEY or "missing-key",  # some providers (e.g. local) tolerate a dummy
    default_headers=AI_EXTRA_HEADERS or None,
)


def call_ai(messages: List[dict]) -> str:
    """Call the AI completion endpoint and return the assistant text."""
    try:
        resp = ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("AI call failed: %s", e)
        return f"[AI error: {type(e).__name__}: {str(e)[:200]}]"


# ---------------------------------------------------------------------------
# Per-channel conversation memory (in-memory; lost on restart)
# ---------------------------------------------------------------------------
conversation_memory: Dict[int, Deque[dict]] = {}


def get_history(channel_id: int) -> Deque[dict]:
    if channel_id not in conversation_memory:
        conversation_memory[channel_id] = deque(maxlen=MAX_HISTORY * 2 + 1)
    return conversation_memory[channel_id]


def append_history(channel_id: int, role: str, content: str) -> None:
    get_history(channel_id).append({"role": role, "content": content})


def build_messages(channel_id: int) -> List[dict]:
    """Prepend system prompt + history. History already capped by deque."""
    return [{"role": "system", "content": SYSTEM_PROMPT}] + list(get_history(channel_id))


# ---------------------------------------------------------------------------
# Typing simulation
# ---------------------------------------------------------------------------
async def simulate_typing(channel: discord.abc.Messageable, response: str) -> None:
    """Show the typing indicator for a realistic delay before sending."""
    delay = TYPING_BASE_SEC + min(len(response), 400) * TYPING_PER_CHAR_SEC
    delay = min(delay, TYPING_MAX_SEC)
    async with channel.typing():
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # required to read message text
intents.dm_messages = True      # to respond in DMs

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.warning("========================================")
    log.warning("SELF-BOT ACTIVE — automating a user account violates Discord ToS.")
    log.warning("Use a burner account. You can be banned.")
    log.warning("========================================")
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    log.info("AI provider: %s | endpoint: %s | model: %s", AI_PROVIDER, AI_BASE_URL, AI_MODEL)
    if TARGET_USER_ID:
        log.info("Target user: %s (bot will respond to their messages anywhere)", TARGET_USER_ID)
    # NOTE: Slash commands are NOT supported on user accounts — no tree.sync() here.

    # Optional: send the configured startup message to the target user (once)
    if STARTUP_MESSAGE and TARGET_USER_ID:
        try:
            user = await bot.fetch_user(int(TARGET_USER_ID))
            dm_channel = await user.create_dm()
            await dm_channel.send(STARTUP_MESSAGE)
            log.info("Sent startup DM to target user %s", user)
        except discord.NotFound:
            log.warning("Startup DM failed: user %s not found.", TARGET_USER_ID)
        except discord.Forbidden:
            log.warning("Startup DM failed: cannot DM user %s (DMs disabled or no shared guild).",
                        TARGET_USER_ID)
        except Exception as e:
            log.warning("Startup DM failed: %s", e)


def is_target_user(author: discord.abc.User) -> bool:
    """True if the author is the configured TARGET_USER."""
    if not TARGET_USER_ID:
        return False
    try:
        return author.id == int(TARGET_USER_ID)
    except (ValueError, TypeError):
        return False


def should_respond(message: discord.Message) -> bool:
    """Decide if the bot should respond to this message."""
    # Never reply to self
    if message.author == bot.user:
        return False
    # Don't reply to other bots (prevents loops) — UNLESS the target user is themselves a bot.
    # If you set TARGET_USER_ID to a bot account you own, allow it explicitly:
    if message.author.bot and not is_target_user(message.author):
        return False
    # Channel whitelist check (empty list = allow all)
    if CHANNEL_WHITELIST:
        if str(message.channel.id) not in CHANNEL_WHITELIST:
            return False
    # DMs — always respond
    if isinstance(message.channel, discord.DMChannel):
        return True
    # Server messages — respond if (a) bot is mentioned OR (b) message is from TARGET_USER
    if bot.user in message.mentions:
        return True
    if is_target_user(message.author):
        return True
    return False


def clean_mention(text: str) -> str:
    """Strip the bot mention from the message text."""
    # discord.py puts <@botid> or <@!botid>; just strip any <@...> prefix
    import re
    cleaned = re.sub(r"^<@!?\d+>\s*", "", text).strip()
    return cleaned


@bot.event
async def on_message(message: discord.Message):
    # Self-bot rule: only the user account itself can trigger prefix commands.
    # Other users' messages never trigger !ping / !send / !clear / !ai.
    if message.author == bot.user:
        await bot.process_commands(message)
        return  # never AI-respond to our own messages

    if not should_respond(message):
        return

    user_text = clean_mention(message.content)
    if not user_text:
        # Target user sent an empty/mention-only message in a server channel — ignore silently.
        return

    log.info("Replying to %s in channel %s: %s",
             message.author, message.channel, user_text[:80])

    # Record user message
    append_history(message.channel.id, "user", f"{message.author.display_name}: {user_text}")

    # Build full context
    messages_for_ai = build_messages(message.channel.id)

    # Generate AI reply
    reply = call_ai(messages_for_ai)
    if not reply:
        reply = "(I didn't get a response — try again?)"

    # Truncate to stay under Discord's 2000-char limit
    if len(reply) > MAX_RESPONSE_CHARS:
        reply = reply[: MAX_RESPONSE_CHARS - 3] + "..."

    # Simulate typing then send
    await simulate_typing(message.channel, reply)
    await message.reply(reply)

    # Record assistant reply
    append_history(message.channel.id, "assistant", reply)


# ---------------------------------------------------------------------------
# Utility commands (only the user account itself can trigger these — see on_message)
# ---------------------------------------------------------------------------
@bot.command(name="ping")
async def ping_cmd(ctx: commands.Context):
    """Check latency."""
    latency_ms = round(bot.latency * 1000)
    await ctx.reply(f"Pong! Latency: {latency_ms}ms")


@bot.command(name="ai")
async def ai_cmd(ctx: commands.Context, *, question: str):
    """One-off AI question (no history used). Usage: !ai what is 2+2"""
    if not question:
        await ctx.reply("Usage: `!ai <your question>`")
        return
    await ctx.trigger_typing()
    reply = call_ai([{"role": "user", "content": question}])
    if len(reply) > MAX_RESPONSE_CHARS:
        reply = reply[: MAX_RESPONSE_CHARS - 3] + "..."
    await ctx.reply(reply)


@bot.command(name="clear")
async def clear_cmd(ctx: commands.Context):
    """Clear conversation memory in the current channel."""
    if ctx.channel.id in conversation_memory:
        conversation_memory[ctx.channel.id].clear()
    await ctx.reply("Cleared my memory for this channel.")


@bot.command(name="send")
async def send_cmd(ctx: commands.Context, *, text: str):
    """DM the TARGET_USER with the given text.
    Usage: !send Hey, just checking in!
    """
    if not TARGET_USER_ID:
        await ctx.reply("TARGET_USER_ID is not set in .env — nothing to send.")
        return
    if not text:
        await ctx.reply("Usage: `!send <your message here>`")
        return
    try:
        user = await bot.fetch_user(int(TARGET_USER_ID))
        dm = await user.create_dm()
        # Truncate to stay under Discord's 2000-char limit
        payload = text[:MAX_RESPONSE_CHARS] if len(text) > MAX_RESPONSE_CHARS else text
        await dm.send(payload)
        await ctx.reply(f"Sent DM to {user.mention} ({len(payload)} chars).")
        log.info("Sent DM to target user %s via !send", user)
    except discord.NotFound:
        await ctx.reply(f"Couldn't find a user with ID {TARGET_USER_ID}.")
    except discord.Forbidden:
        await ctx.reply("Can't DM that user — they have DMs disabled or share no guild.")
    except Exception as e:
        await ctx.reply(f"Error sending DM: {type(e).__name__}: {e}")


@send_cmd.error
async def send_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!send <your message here>`")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Run the bot standalone (python bot.py)."""
    if not DISCORD_USER_TOKEN:
        log.error("DISCORD_USER_TOKEN is not set. Copy .env.example to .env and fill it in.")
        log.error("This must be a USER ACCOUNT token, NOT a bot application token.")
        raise SystemExit(1)
    if not AI_API_KEY:
        log.warning("AI_API_KEY is not set — AI calls will likely fail. "
                    "Get a key from your provider (OpenRouter / Hugging Face / OpenAI).")

    log.warning("SELF-BOT mode — automating a user account. This violates Discord ToS.")
    log.info("Bot starting | provider=%s model=%s", AI_PROVIDER, AI_MODEL)
    log.info("Memory: %d msgs/channel | Typing: %.1fs–%.1fs",
             MAX_HISTORY, TYPING_BASE_SEC, TYPING_MAX_SEC)
    if TARGET_USER_ID:
        log.info("Target user: %s", TARGET_USER_ID)
    else:
        log.info("No target user set — bot will only respond to mentions and DMs.")

    bot.run(DISCORD_USER_TOKEN)


if __name__ == "__main__":
    main()
