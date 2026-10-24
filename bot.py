"""
Simple Discord AI self-bot. Human-like replies.

.env needs 3 things:
  DISCORD_USER_TOKEN=...  (user account token from browser DevTools)
  AI_API_KEY=...          (openrouter key)
  TARGET_USER_ID=...      (discord user id to talk to)

Run:  python bot.py
"""
import os
import re
import random
import logging
import asyncio
from pathlib import Path

import discord
from dotenv import load_dotenv
from openai import OpenAI

ENV_PATH = Path(__file__).with_name(".env")

REQUIRED_KEYS = [
    ("DISCORD_USER_TOKEN", "your discord user token (F12 -> Network -> any request -> authorization header)"),
    ("AI_API_KEY", "your openrouter key (free, from openrouter.ai/keys)"),
    ("TARGET_USER_ID", "the discord user id to talk to (right-click user -> Copy User ID)"),
]


def parse_env_file() -> dict:
    """Read .env manually so nothing ever gets quoted or mangled."""
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                v = v.strip()
                while v and v[0] in "\"'" and v[-1] == v[0]:
                    v = v[1:-1].strip()
                vals[k.strip()] = v
    return vals


def ensure_env() -> dict:
    """If anything required is missing, ask for it in the console and
    write .env deterministically (always the same keys, same order)."""
    vals = parse_env_file()
    missing = [k for k, _ in REQUIRED_KEYS if not vals.get(k)]
    if missing:
        print("=" * 60)
        print("first time setup - fill these in (they save to .env)")
        print("=" * 60)
        for key, hint in REQUIRED_KEYS:
            if key in missing:
                print()
                print(f"{key}  ({hint})")
                while True:
                    v = input("> ").strip().strip('"').strip("'").strip()
                    if v:
                        vals[key] = v
                        break
                    print("can't be empty, try again")
        lines = [f"{key}={vals[key]}" for key, _ in REQUIRED_KEYS]
        for key in ("AI_MODEL", "SYSTEM_PROMPT", "PROACTIVE_MINUTES"):
            if vals.get(key):
                lines.append(f"{key}={vals[key]}")
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print()
        print("saved to .env - starting the bot")
        print()
    return vals


ENV_VALS = ensure_env()
load_dotenv(ENV_PATH, override=True)


def _clean(v: str) -> str:
    """Strip whitespace and any wrapping quotes."""
    v = ENV_VALS.get(v) or os.getenv(v, "").strip()
    while v and v[0] in "\"'" and v[-1] == v[0]:
        v = v[1:-1].strip()
    return v


DISCORD_TOKEN = _clean("DISCORD_USER_TOKEN")
AI_KEY = _clean("AI_API_KEY")
TARGET_ID = _clean("TARGET_USER_ID")
AI_MODEL = _clean("AI_MODEL") or "meta-llama/llama-3.2-3b-instruct:free"

# how many minutes between the bot texting the target user first.
# set to 0 to disable. randomised a bit so it doesn't feel like a timer.
PROACTIVE_MINUTES = int(_clean("PROACTIVE_MINUTES") or 45)

# casual openers the bot picks from when texting first
OPENERS = [
    "hey what are you up to",
    "yo",
    "heyy",
    "wyd",
    "guess what",
    "im so bored",
    "you there",
    "hey you",
    "ok so random thought",
    "did you eat",
]

SYSTEM_PROMPT = _clean("SYSTEM_PROMPT") or (
    "You are a casual person texting a friend on Discord. "
    "Keep replies SHORT (one sentence, maybe two). "
    "all lowercase, no punctuation at the end. "
    "talk like a real person, never formal, never bullet points, "
    "never say 'as an ai' or mention being an assistant."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bot")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=AI_KEY,
    default_headers={"HTTP-Referer": "http://localhost", "X-Title": "discord"},
)

intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
bot = discord.Client(intents=intents)

# remember the last few messages per channel so replies have context
history: dict[int, list] = {}


def is_target(author) -> bool:
    return bool(TARGET_ID) and str(author.id) == TARGET_ID


def call_ai(messages: list) -> str | None:
    try:
        r = client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            max_tokens=80,
            temperature=1.0,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("ai error: %s", e)
        return None


@bot.event
async def on_ready():
    log.info("logged in as %s", bot.user)
    if PROACTIVE_MINUTES > 0 and TARGET_ID:
        bot.loop.create_task(proactive_loop())


async def proactive_loop():
    """Texts the target user first every so often, like a real friend would."""
    await bot.wait_until_ready()
    while True:
        # wait a randomised amount so it doesn't feel like a robot timer
        wait = PROACTIVE_MINUTES * 60 + random.uniform(-600, 600)
        await asyncio.sleep(max(300, wait))  # never faster than 5 min
        try:
            user = await bot.fetch_user(int(TARGET_ID))
            dm = await user.create_dm()
            opener = random.choice(OPENERS)
            log.info("texting first: %s", opener)
            await human_typing(dm, len(opener) * 0.05)
            await dm.send(opener)
        except discord.Forbidden:
            log.warning("can't dm the target user (they have dms off?)")
        except Exception as e:
            log.error("proactive dm failed: %s", e)


async def human_typing(channel, seconds):
    async with channel.typing():
        await asyncio.sleep(max(0.5, seconds))


@bot.event
async def on_message(msg):
    if msg.author == bot.user or msg.author.bot:
        return
    # respond to: the target user (anywhere), mentions, and dms
    if not (is_target(msg.author) or bot.user in msg.mentions
            or isinstance(msg.channel, discord.DMChannel)):
        return

    text = re.sub(r"<@!?\d+>\s*", "", msg.content).strip()
    if not text:
        return

    log.info("they said: %s", text[:60])

    # small conversation memory so it doesn't repeat itself
    h = history.setdefault(msg.channel.id, [])
    h.append({"role": "user", "content": text})
    h[:] = h[-6:]

    reply = call_ai([{"role": "system", "content": SYSTEM_PROMPT}] + h)
    if not reply:
        return
    h.append({"role": "assistant", "content": reply})

    # human timing: read it, think about it, then type it
    read_time = random.uniform(1.0, 3.0)
    think_time = random.uniform(0.5, 2.0)
    type_time = min(len(reply) * 0.05, 4.0)

    await asyncio.sleep(read_time)
    await human_typing(msg.channel, think_time + type_time)

    await msg.reply(reply)
    log.info("i said: %s", reply[:60])


if __name__ == "__main__":
    if not DISCORD_TOKEN or not AI_KEY or not TARGET_ID:
        print("something's still missing in .env — delete .env and run again to redo setup")
        raise SystemExit(1)
    bot.run(DISCORD_TOKEN)
