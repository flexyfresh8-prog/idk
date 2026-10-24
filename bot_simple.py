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

import discord
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def _clean(v: str) -> str:
    """Strip whitespace and any wrapping quotes."""
    v = os.getenv(v, "").strip()
    while v and v[0] in "\"'" and v[-1] == v[0]:
        v = v[1:-1].strip()
    return v


DISCORD_TOKEN = _clean("DISCORD_USER_TOKEN")
AI_KEY = _clean("AI_API_KEY")
TARGET_ID = _clean("TARGET_USER_ID")
AI_MODEL = _clean("AI_MODEL") or "meta-llama/llama-3.2-3b-instruct:free"

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
    async with msg.channel.typing():
        await asyncio.sleep(think_time + type_time)

    await msg.reply(reply)
    log.info("i said: %s", reply[:60])


if __name__ == "__main__":
    if not DISCORD_TOKEN or not AI_KEY:
        print("put DISCORD_USER_TOKEN and AI_API_KEY in .env first")
        raise SystemExit(1)
    bot.run(DISCORD_TOKEN)
