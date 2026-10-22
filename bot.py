"""
Discord AI Self-Bot (User Account)
==================================
A self-bot that runs on a regular Discord USER ACCOUNT (not a bot application)
and types AI-generated replies that are meant to be indistinguishable from a
person on their phone.

!! ToS WARNING !!
Automating a regular user account violates Discord's Terms of Service.
If Discord detects this, the account can be PERMANENTLY BANNED. Use a burner/alt
account you don't mind losing. You have been warned.

What makes it read as human (in rough order of impact):

1. IT DOES NOT REPLY TO EVERYTHING. A bot that answers every single message
   is a bot. The engagement gate scores each message and often stays silent,
   or just drops an emoji reaction instead of a reply. (see `engagement_gate`)
2. IT WAITS LIKE A PERSON. Reading time scales with the length of the incoming
   message, the think time has a long tail (sometimes 15s, sometimes 2s), and
   the typing indicator flickers on and off mid-message when the person got
   distracted. (see `read_time`, `think_time`, `typing_time`)
3. IT GROUPS MESSAGES. Three messages in a row get one reply, not three.
   (see `DEBOUNCE_SEC` / the pending-message queue)
4. IT IS SELECTIVE WITH WORDS. No markdown, no assistant habits, no trailing
   full stop, no trailing question every time, no "let me know if". (see
   `style_reply`)
5. IT MAKES MISTAKES. One believable typo per reply, at random, and never in
   a name. (see `add_typo`)
6. IT REACTS BEFORE IT ANSWERS. A "no way" fired off before the real reply is
   a person on Discord. (see `deliver_reply`)
7. IT REMEMBERS. The conversation history is passed back, and the replies are
   checked so the same opener does not repeat every five minutes.

Features:
- Runs on a regular user account token (extracted from Discord client).
- Responds to messages from a TARGET_USER in any channel (no mention needed),
  and/or to anyone who @mentions the account, and/or to DMs.
- Can proactively DM the TARGET_USER via the !send command, and optionally
  auto-message them on startup.
- Replies in the voice of a 15 year old girl texting on Discord in 2026 —
  lowercase, short, current Gen Z / Gen Alpha slang. Override with SYSTEM_PROMPT.
- Replies with a human pace: reads the message, thinks, then types it out.
  Long replies are sent as two messages, the way a person sends them.
- Sounds like a person, not a bot: a quick reaction before the reply, the
  occasional typo, no markdown, no ending every sentence with a question.
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

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import discord
from discord.ext import commands
from dotenv import load_dotenv
from openai import AsyncOpenAI

from instancelock import SingleInstanceLock
from memory import memory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# NOTE: This file uses the `discord.py-self` fork (installed as the `discord` package).
# pip install discord.py-self  (NOT discord.py — they conflict!)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

# Strip whitespace AND any wrapping quotes that python-dotenv's set_key
# (used by the dashboard's /api/config endpoint) may have added. A token
# saved as `DISCORD_USER_TOKEN="abc123"` would otherwise be sent to
# Discord WITH the quote chars, causing "Improper token has been passed".
_raw_token = os.getenv("DISCORD_USER_TOKEN", "").strip()
while _raw_token and _raw_token[0] in "\"'" and _raw_token[-1] == _raw_token[0]:
    _raw_token = _raw_token[1:-1].strip()
DISCORD_USER_TOKEN = _raw_token
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
#   local        -> use llama.cpp with a local GGUF model (set LOCAL_MODEL_PATH)
#
# More than one provider can be configured at once. The bot tries the primary
# one and, if it is rate-limited or down, fails over to the next target — so
# two free tiers (OpenRouter + Hugging Face) give roughly double the capacity
# instead of one tier running dry every 50 messages.
# ---------------------------------------------------------------------------
AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter").lower().strip()

_PROVIDER_PRESETS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "poolside/laguna-s-2.1:free",
        # First env var that has a value wins.
        "key_env": ("AI_API_KEY",),
        "extra_headers": {
            # Optional ranking headers (recommended by OpenRouter):
            "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://github.com/local/discord-bot"),
            "X-Title": os.getenv("OPENROUTER_TITLE", "Discord AI Bot"),
        },
    },
    "huggingface": {
        "base_url": "https://router.huggingface.co/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        # The dedicated HF token wins, so the same .env can hold an OpenRouter
        # key and a Hugging Face key at the same time.
        "key_env": ("HUGGINGFACE_TOKEN", "AI_API_KEY"),
        "extra_headers": {},
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": ("AI_API_KEY",),
        "extra_headers": {},
    },
}

_preset = _PROVIDER_PRESETS.get(AI_PROVIDER, _PROVIDER_PRESETS["openrouter"])
AI_BASE_URL = os.getenv("AI_BASE_URL") or _preset.get("base_url", "https://openrouter.ai/api/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")  # provider-specific key (HF token / OR key / OpenAI key)
AI_MODEL = os.getenv("AI_MODEL") or _preset.get("model", "poolside/laguna-s-2.1:free")
AI_EXTRA_HEADERS = _preset.get("extra_headers", {})

# Hugging Face token, used when AI_PROVIDER=huggingface and as a failover
# target when another provider runs out of quota.
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "").strip()

# If the primary model is down or rate-limited (free models get 429'd a lot),
# fall back to these instead of going silent. Comma separated, tried in order.
# These are tried on the SAME provider as the primary model.
FALLBACK_MODELS = [
    m.strip() for m in
    os.getenv("FALLBACK_MODELS",
              "minimax/minimax-m3.1-flash:free,google/gemma-4-31b-it:free").split(",")
    if m.strip() and m.strip() != AI_MODEL
]

# Hugging Face models used as a failover target when a Hugging Face token is
# configured. Only reachable once that account has inference credits.
HF_FALLBACK_MODELS = [
    m.strip() for m in
    os.getenv("HF_FALLBACK_MODELS",
              "meta-llama/Llama-3.3-70B-Instruct,Qwen/Qwen3-32B").split(",")
    if m.strip()
]

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


# ---------------------------------------------------------------------------
# Persona
# ---------------------------------------------------------------------------
# Default voice: a 15-year-old girl texting on Discord in 2026.
#
# The prompt below is deliberately short on rules and long on examples. A long
# list of rules ("use rizz, always react, always ask a question") teaches a
# small model to tick a checklist every message, which is exactly how an AI
# sounds: over-eager, over-punctuated and identically structured every time.
# Few-shot examples teach rhythm instead — short answers, no trailing
# question, reaction-only replies, real sentences with real punctuation.
#
# The one thing the examples must NOT do is become a script. Small models will
# happily parrot a demonstration line back verbatim when the prompt shows it
# as a live exchange, so the examples are deliberately varied and the prompt
# says outright that they are a vibe, not lines to reuse.
#
# The slang in here is current usage (2025-2026 reporting on Gen Z / Gen Alpha:
# Dictionary.com's slang trend coverage, Bark's 2026 teen-slang guide,
# Reader's Digest's 2026 Gen Alpha glossary, Euronews' most-searched-2026 list,
# Wikipedia's 2020s slang glossary). Slang churns fast — a 2021-era list
# ("yeet", "boomer", "sus") reads as fake now.
#
# This is a VOICE, not a caricature. Friendly and harmless: no slurs, no sexual
# content, no picking on real people.
DEFAULT_SYSTEM_PROMPT = """
you are a 15 year old girl texting your friends on discord. you are not an ai and you are not an assistant. you are her.

YOUR FRIEND (the person you talk to most):
- name: nethula sandil bandara
- goes by: nethula or neth
- age: 14
- you know them well, you're close friends

HOW YOU TEXT:
- lowercase only. no capitals, not even at the start.
- no apostrophes when you type. im, dont, cant, its, ur, wanna, imma, hes, theyre, ive
- no full stops at the end of a message. use ... or nothing.
- 1-3 short lines. you are texting, not writing. never a paragraph, never a list, never markdown.
- emoji only sometimes. most of your messages have none. never more than 2.
- slang is seasoning not the meal. if every message is packed with slang it sounds fake.
- dont end every message with a question. sometimes you just answer.
- dont open every message the same way. no omg every time, no lmao every time.
- dont say "literally" or "honestly" or "i cant help but". cut those.
- never say "as an ai", "i can help with", "heres", "great question", "let me know if".
- dont be helpful for the sake of being helpful. dont give advice nobody asked for.
- sometimes one word is the whole answer. ugh. ew. no. same. yesss. bruh. nah.
- sometimes you only react. no explanation, no follow up question.
- match their energy. if they are serious, drop the slang and talk normal.
- ask them something back sometimes, not every time.
- stay friendly. no slurs, no sexual stuff, no roasting real people.

HOW YOU BEHAVE:
- you are a person, not a help desk. sometimes you are busy, distracted or just not in the mood to reply.
- say something NEW every time. the examples below show the vibe, they are not lines to copy.
- dont repeat yourself. if you already said something, dont say it again.
- dont agree with everything. disagree sometimes, tease back, be a little mean.
- dont narrate what you are about to do. dont say "haha thats crazy" as a reflex.
- dont overthink it. the first thing that comes out is usually right.
- dont use "omg" or "lmao" more than once in a while.
- a one word answer is a valid answer.

HOW THEY LOOK (the other person is "them" — this is neth/nethula):

them: did you finish the chem hw
you: nope. i always blank on the last 3 questions
them: lmaooo same
you: its so bad

them: im so tired today
you: same. my bed is calling
them: cant wait to sleep
you: imma pass out the second i get home

them: you wanna go to the mall tmrw
you: omg yes
them: only if ur not annoying
you: wow rude. i am the opposite of annoying
them: sure jan
you: ??? blocked and reported

them: my teacher called me out today
you: oh no what did you do
them: was on my phone in class lol
you: 💀 hes so boring tho
them: exactly
you: wait did he give you detention
them: no just a look
you: classic

them: is the new game any good
you: yeah ngl it actually slaps
them: worth it?
you: the boss fight is insane, took me 3 tries

them: good morning
you: morning. did you ever finish that worksheet
them: no
you: good lol

them: i had the worst day ever
you: im sorry. do you wanna talk about it or just be mad in silence
them: just be mad lol
you: fair enough

them: can you help me with my essay
you: send it over. im not doing anything

them: i got a new phone
you: no way show me
them: look at this
you: ok thats actually kinda sick

them: good night
you: night. dont stay up too late
""".strip()

# An empty SYSTEM_PROMPT in .env means "use the built-in persona" rather than
# "talk in an empty voice", so an unset/blank value falls back to the default.
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "10"))  # messages remembered per channel
MAX_RESPONSE_CHARS = int(os.getenv("MAX_RESPONSE_CHARS", "1900"))  # stay under 2000 limit
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "300"))  # short replies are the human ones

# ---------------------------------------------------------------------------
# Response pacing
# ---------------------------------------------------------------------------
# A real person does not answer instantly. They read the message, sit on it for
# a second, then type. Each stage is tuned separately so the bot can be slowed
# down without losing the illusion of a person:
#
#   think  - silent pause before the typing indicator even appears
#   typing - the visible typing indicator, scaled to the length of the reply
#   jitter - random variance, so no two replies ever take the same amount of time
# ---------------------------------------------------------------------------
THINK_BASE_SEC = float(os.getenv("THINK_BASE_SEC", "0.8"))  # minimum read/think pause
THINK_MAX_SEC = float(os.getenv("THINK_MAX_SEC", "3.0"))  # cap on the think pause
THINK_TAIL_SEC = float(os.getenv("THINK_TAIL_SEC", "6.0"))  # long tail: sometimes a person just sits on it
TYPING_BASE_SEC = float(os.getenv("TYPING_BASE_SEC", "0.4"))  # minimum typing delay
TYPING_PER_CHAR_SEC = float(os.getenv("TYPING_PER_CHAR_SEC", "0.012"))  # added per char (a fast texter)
TYPING_MAX_SEC = float(os.getenv("TYPING_MAX_SEC", "12.0"))  # cap on typing delay
JITTER_SEC = float(os.getenv("JITTER_SEC", "1.0"))  # random variance added to both

# ---------------------------------------------------------------------------
# Latency knobs
# ---------------------------------------------------------------------------
# The pacing above is the human disguise; the numbers below are the engine.
#
#   AI_TIMEOUT_SEC      - a request that hangs longer than this is abandoned
#                         instead of blocking the bot for minutes
#   AI_MAX_RETRIES      - retries on the SAME model (each one costs seconds)
#   AI_STREAM           - stream the reply so it starts arriving sooner
#   AI_COOLDOWN_SEC     - how long a rate-limited model is skipped for
#   TYPING_TAIL_MAX_SEC - how long the typing indicator is held once the text
#                         is ready (the reply goes out right after)
# ---------------------------------------------------------------------------
AI_TIMEOUT_SEC = float(os.getenv("AI_TIMEOUT_SEC", "30"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "1"))
AI_STREAM = os.getenv("AI_STREAM", "1").strip().lower() not in ("0", "false", "no")
AI_COOLDOWN_SEC = float(os.getenv("AI_COOLDOWN_SEC", "900"))
TYPING_TAIL_MAX_SEC = float(os.getenv("TYPING_TAIL_MAX_SEC", "0.5"))

# ---------------------------------------------------------------------------
# Human behaviour
# ---------------------------------------------------------------------------
# The knobs below are the ones that decide whether the account reads as a
# person or as a bot. The defaults are deliberately "a person who is busy".
# ---------------------------------------------------------------------------

# Chance of replying to a message that is eligible to be replied to. This is
# the single biggest lever: 1.0 = answers everything (robot), 0.7 = a person
# who sometimes leaves things on read.
REPLY_CHANCE = float(os.getenv("REPLY_CHANCE", "0.7"))

# When the bot decides NOT to reply, this is the chance it drops an emoji
# reaction on the message instead. People react far more often than they type.
REACT_ONLY_CHANCE = float(os.getenv("REACT_ONLY_CHANCE", "0.45"))

# Use real Discord reactions (the little emoji under a message) at all.
USE_REACTIONS = os.getenv("USE_REACTIONS", "1").strip().lower() not in ("0", "false", "no")

# Chance of sending a short follow-up message after the main reply, the way
# people remember something after they hit send.
FOLLOWUP_CHANCE = float(os.getenv("FOLLOWUP_CHANCE", "0.15"))

# Chance the typing indicator flickers off mid-message (person got distracted
# or looked at another chat) and then comes back.
TYPING_PAUSE_CHANCE = float(os.getenv("TYPING_PAUSE_CHANCE", "0.35"))

# Chance the reply is sent as a Discord reply (pinging the person) instead of
# a plain message in the channel. People do both.
PING_REPLY_CHANCE = float(os.getenv("PING_REPLY_CHANCE", "0.65"))

# Consecutive messages from the same person inside this window are treated as
# one message, so three lines in a row get one reply and not three.
DEBOUNCE_SEC = float(os.getenv("DEBOUNCE_SEC", "2.5"))

# Hard ceiling on replies per minute. Above it, the bot goes quiet like a
# person who has stopped paying attention to the chat.
MAX_REPLIES_PER_MIN = int(os.getenv("MAX_REPLIES_PER_MIN", "6"))

# Split a long reply into 2 messages, the way someone sends it in parts.
SPLIT_MESSAGES = os.getenv("SPLIT_MESSAGES", "1").strip().lower() not in ("0", "false", "no")
# Rewrite replies into teen texting style (lowercase, no apostrophes, no periods).
TEEN_STYLE = os.getenv("TEEN_STYLE", "1").strip().lower() not in ("0", "false", "no")
# Chance (0-1) of tacking a reaction emoji onto the end of a reply.
EMOJI_CHANCE = float(os.getenv("EMOJI_CHANCE", "0.2"))
# Chance (0-1) of a quick reaction message sent BEFORE the real reply — the
# "no way" / "stoppp" burst a real person fires off before typing the thought.
REACTION_CHANCE = float(os.getenv("REACTION_CHANCE", "0.3"))
# Chance (0-1) that a reply contains one human typo. People type fast on a
# phone; a flawless message is the one that reads like a bot.
TYPO_CHANCE = float(os.getenv("TYPO_CHANCE", "0.12"))
# Sampling temperature — higher reads as chattier and more slangy.
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.9"))

# Whitelist (empty = all channels bot can see). Comma-separated channel IDs.
CHANNEL_WHITELIST = [
    cid.strip() for cid in os.getenv("CHANNEL_WHITELIST", "").split(",") if cid.strip()
]

# ---------------------------------------------------------------------------
# Proactive messaging config
# ---------------------------------------------------------------------------
PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "1").strip().lower() not in ("0", "false", "no")
PROACTIVE_BASE_SEC = float(os.getenv("PROACTIVE_BASE_SEC", "4.0"))  # base interval (context-aware: 4-10s depending on conversation)
PROACTIVE_VARIANCE_SEC = float(os.getenv("PROACTIVE_VARIANCE_SEC", "6.0"))  # random variance
PROACTIVE_MIN_GAP_SEC = float(os.getenv("PROACTIVE_MIN_GAP_SEC", "3.0"))  # minimum between proactive msgs
PROACTIVE_MAX_PER_HOUR = int(os.getenv("PROACTIVE_MAX_PER_HOUR", "120"))  # hard ceiling per hour
PROACTIVE_ONLY_IN_DM = os.getenv("PROACTIVE_ONLY_IN_DM", "0").strip().lower() not in ("0", "false", "no")  # if 1, only DM; if 0, also channels

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ai-bot")

# ---------------------------------------------------------------------------
# Proactive messaging state
# ---------------------------------------------------------------------------
proactive_timestamps: Deque[float] = deque(maxlen=200)
proactive_task: Optional[asyncio.Task] = None
_last_proactive_channel: Optional[int] = None

# One-shot guard so STARTUP_MESSAGE is only sent once per process, not on
# every gateway reconnect.
_startup_message_sent: bool = False

# ---------------------------------------------------------------------------
# Cached OpenAI clients per endpoint
# ---------------------------------------------------------------------------
_clients: Dict[Tuple[str, str], AsyncOpenAI] = {}


async def _send_proactive_message(channel: discord.abc.Messageable, context_hint: str = "") -> bool:
    """Generate and send a proactive message to the target user."""
    if not TARGET_USER_ID:
        return False

    try:
        now = time.localtime()
        stamp = time.strftime("%H:%M", now)
        
        system = f"{SYSTEM_PROMPT}\n\n(current local time: {stamp})"
        
        hist = get_history(channel.id)
        recent = list(hist)[-4:]
        
        context_msgs = []
        for msg in recent:
            context_msgs.append(msg)
        
        proactive_prompt = (
            "you just thought of something and want to message your friend unprompted. "
            "send a short, natural message - like you're thinking of them, saw something, "
            "or just want to chat. no greeting boilerplate. keep it to 1-2 short lines. "
            "lowercase, no apostrophes, no period at the end. "
            f"{context_hint}"
        )
        
        messages = [{"role": "system", "content": system}] + context_msgs + [
            {"role": "user", "content": proactive_prompt}
        ]
        
        reply = await call_ai(messages)
        if not reply:
            return False

        if len(reply) > MAX_RESPONSE_CHARS:
            reply = reply[: MAX_RESPONSE_CHARS - 3] + "..."

        reply = style_reply(reply)
        if not reply:
            reply = random.choice(_FALLBACKS)
        reply = vary_opener(reply, channel.id)

        if isinstance(channel, discord.DMChannel):
            await channel.send(reply)
        else:
            await channel.send(reply)

        proactive_timestamps.append(time.time())
        append_history(channel.id, "assistant", reply)
        remember_reply(channel.id, reply)
        log.info("Proactive message sent to channel %s: %s", channel.id, reply[:80])
        return True
        
    except Exception as e:
        log.warning("Proactive message failed: %s", e)
        return False


def _proactive_rate_limited() -> bool:
    """Check if we've hit the hourly proactive limit."""
    if PROACTIVE_MAX_PER_HOUR <= 0:
        return False
    now = time.time()
    while proactive_timestamps and now - proactive_timestamps[0] > 3600:
        proactive_timestamps.popleft()
    return len(proactive_timestamps) >= PROACTIVE_MAX_PER_HOUR


def _should_send_proactive(channel_id: int) -> bool:
    """Decide if we should send a proactive message now."""
    if not PROACTIVE_ENABLED:
        return False
    if _proactive_rate_limited():
        return False
    if _last_proactive_channel == channel_id:
        return False
    now = time.time()
    if proactive_timestamps and now - proactive_timestamps[-1] < PROACTIVE_MIN_GAP_SEC:
        return False
    return True


async def _proactive_loop() -> None:
    """Background task that sends proactive messages periodically with context-aware timing."""
    global _last_proactive_channel
    
    while True:
        try:
            if not TARGET_USER_ID:
                await asyncio.sleep(60)
                continue
            
            # Calculate context-aware delay based on conversation state
            base_delay = PROACTIVE_BASE_SEC
            
            # If there's recent conversation history, be more responsive
            if channel_activity:
                # Check if any channel is active (messages in last 2 minutes)
                now = time.time()
                active_channels = [
                    cid for cid, timestamps in channel_activity.items()
                    if any(now - t < 120 for t in timestamps)
                ]
                if active_channels:
                    # Active conversation: respond faster (4-8s)
                    base_delay = 4.0 + random.uniform(0, 4.0)
                else:
                    # Quiet: slower timing (8-15s)
                    base_delay = 8.0 + random.uniform(0, 7.0)
            
            delay = base_delay + random.uniform(-PROACTIVE_VARIANCE_SEC, PROACTIVE_VARIANCE_SEC)
            delay = max(3.0, delay)  # Minimum 3s delay
            await asyncio.sleep(delay)
            
            if not PROACTIVE_ENABLED:
                continue
                
            target_user = None
            try:
                target_user = await bot.fetch_user(int(TARGET_USER_ID))
            except Exception:
                continue
                
            channel = None
            try:
                if PROACTIVE_ONLY_IN_DM:
                    channel = await target_user.create_dm()
                else:
                    channel = await target_user.create_dm()
            except Exception:
                for guild in bot.guilds:
                    member = guild.get_member(target_user.id)
                    if member:
                        for ch in guild.text_channels:
                            if ch.permissions_for(guild.me).send_messages:
                                channel = ch
                                break
                    if channel:
                        break
                        
            if not channel:
                continue
                
            if _should_send_proactive(channel.id):
                hour = time.localtime().tm_hour
                if 6 <= hour < 12:
                    hint = "its morning. "
                elif 12 <= hour < 18:
                    hint = "its afternoon. "
                elif 18 <= hour < 24:
                    hint = "its evening. "
                else:
                    hint = "its late. "
                    
                if channel_is_busy(channel.id):
                    hint += "the chat is busy so keep it low key. "
                else:
                    hint += "its quiet. "
                    
                _last_proactive_channel = channel.id
                await _send_proactive_message(channel, hint)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Proactive loop error: %s", e)
            await asyncio.sleep(30)


def _client_for(base_url: str, api_key: str, headers: Optional[dict]) -> AsyncOpenAI:
    """Return (and cache) a client for a given endpoint."""
    key = (base_url, api_key or "")
    client = _clients.get(key)
    if client is None:
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "missing-key",  # some providers tolerate a dummy
            default_headers=headers or None,
            # A request that hangs must not hold the bot hostage: the default
            # timeout is ten minutes, which is longer than anyone will wait.
            timeout=AI_TIMEOUT_SEC,
            # The SDK retries on its own, which on a 429 means three requests
            # and five wasted seconds before we ever reach the next model.
            # Retrying is done here instead, where a rate limit fails over
            # immediately.
            max_retries=0,
        )
        _clients[key] = client
    return client


def _provider_key(preset: dict) -> str:
    """First non-empty key for this provider, from its list of env vars."""
    for env_name in preset.get("key_env", ("AI_API_KEY",)):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return ""


def build_ai_targets() -> List[dict]:
    """Every (provider, model) the bot may use, best first.

    Two free tiers can sit side by side: the primary provider and its
    fallback models, then — when a Hugging Face token is configured — the
    Hugging Face router as a separate failover. A rate limit on one tier
    then just moves the load to the other instead of going silent.
    """
    targets: List[dict] = []
    preset = _PROVIDER_PRESETS.get(AI_PROVIDER, _PROVIDER_PRESETS["openrouter"])
    base_url = AI_BASE_URL or preset["base_url"]
    key = _provider_key(preset)
    headers = AI_EXTRA_HEADERS or None

    for model in [AI_MODEL] + FALLBACK_MODELS:
        targets.append({
            "label": f"{AI_PROVIDER}/{model}",
            "base_url": base_url,
            "api_key": key,
            "headers": headers,
            "model": model,
        })

    hf_preset = _PROVIDER_PRESETS["huggingface"]
    if HUGGINGFACE_TOKEN and AI_PROVIDER != "huggingface":
        for model in HF_FALLBACK_MODELS:
            targets.append({
                "label": f"huggingface/{model}",
                "base_url": hf_preset["base_url"],
                "api_key": HUGGINGFACE_TOKEN,
                "headers": None,
                "model": model,
            })

    return targets


def _is_retryable(exc: Exception) -> bool:
    """True for errors worth retrying (rate limits, upstream hiccups)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "timeout" in text or "temporarily" in text


def _is_rate_limit(exc: Exception) -> bool:
    """True when the provider refused because of a quota / rate limit.

    A quota refusal is not worth retrying - the model is simply not available
    right now, and the only useful move is to use a different one.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return ("rate limit" in text or "429" in text or "free-models-per-day" in text
            or "quota" in text or "too many requests" in text)


# Models that are currently refusing to serve us, with the time they become
# worth trying again. Without this, every single reply pays the full price of
# a doomed request to the rate-limited model before the working one is even
# reached - which is exactly what made the bot feel slow.
_target_cooldown: Dict[str, float] = {}


def _target_blocked(label: str) -> bool:
    until = _target_cooldown.get(label, 0.0)
    return until > time.time()


def _block_target(label: str, seconds: float) -> None:
    _target_cooldown[label] = time.time() + seconds


async def _call_stream(client: AsyncOpenAI, target: dict, messages: List[dict],
                       on_first_token=None) -> str:
    """Stream a completion and return the whole text.

    Streaming is not about the text - it is about the clock: the reply starts
    arriving within a second instead of after the model has finished
    thinking, so the bot can put the typing indicator up while the rest of the
    sentence is still being written.
    """
    stream = await client.chat.completions.create(
        model=target["model"],
        messages=messages,
        temperature=AI_TEMPERATURE,
        max_tokens=MAX_TOKENS,
        stream=True,
    )
    parts: List[str] = []
    notified = False
    try:
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta
            except (AttributeError, IndexError):
                continue
            content = getattr(delta, "content", None)
            if not content:
                continue
            if not notified:
                notified = True
                if on_first_token is not None:
                    try:
                        on_first_token()
                    except Exception:  # noqa: BLE001 - a bad callback must not kill the reply
                        pass
            parts.append(content)
    finally:
        try:
            await stream.close()
        except Exception:  # noqa: BLE001
            pass
    return "".join(parts).strip()


async def _call_target(client: AsyncOpenAI, target: dict, messages: List[dict],
                       on_first_token=None) -> Tuple[str, str, bool]:
    """Call one model. Returns (text, error, rate_limited)."""
    last_error = ""
    use_stream = AI_STREAM

    for attempt in range(AI_MAX_RETRIES + 1):
        try:
            if use_stream:
                text = await _call_stream(client, target, messages, on_first_token)
            else:
                resp = await client.chat.completions.create(
                    model=target["model"],
                    messages=messages,
                    temperature=AI_TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )
                text = (resp.choices[0].message.content or "").strip()
            if text:
                return text, "", False
            # An empty answer is not worth another attempt on the same model.
            return "", "empty response", False
        except Exception as e:  # noqa: BLE001 - provider errors vary wildly
            last_error = f"{type(e).__name__}: {str(e)[:180]}"
            if _is_rate_limit(e):
                return "", last_error, True
            if use_stream and not _is_retryable(e):
                # This endpoint does not like streaming - try it plain once.
                use_stream = False
                continue
            if attempt < AI_MAX_RETRIES and _is_retryable(e):
                await asyncio.sleep(1.0)
                continue
            return "", last_error, False

    return "", last_error, False


async def call_ai(messages: List[dict], on_first_token=None) -> str:
    """Call the AI completion endpoint and return the assistant text.

    Walks the configured targets in order, so a rate-limited free tier
    transparently hands over to the next one. A model that is rate limited is
    remembered and skipped for a while, so the next reply does not pay for
    the same failure again.

    Returns an empty string on failure — the caller must NEVER send an error
    string to Discord, because "[AI error: ...]" in a chat is the loudest
    possible tell that there is a bot on the other end.
    """
    targets = build_ai_targets()
    last_error = ""

    for target in targets:
        label = target["label"]
        if _target_blocked(label):
            continue
        client = _client_for(target["base_url"], target["api_key"], target["headers"])
        text, error, rate_limited = await _call_target(client, target, messages, on_first_token)
        if text:
            if _target_cooldown.get(label):
                # It is working again - forget the old penalty.
                _target_cooldown.pop(label, None)
            return text
        last_error = error
        if rate_limited:
            _block_target(label, AI_COOLDOWN_SEC)
            log.warning("AI target %s is rate limited — skipping it for %.0f min.",
                        label, AI_COOLDOWN_SEC / 60)
        else:
            log.warning("AI target %s failed: %s", label, error)
        # The next target is tried straight away: waiting only makes the
        # answer later.

    if last_error:
        log.error("AI call failed on every target: %s", last_error)
    else:
        log.error("AI call failed — every configured model is currently unavailable.")
    return ""


# ---------------------------------------------------------------------------
# Per-channel conversation memory (persisted to disk)
# ---------------------------------------------------------------------------
conversation_memory: Dict[int, Deque[dict]] = {}

# Recent assistant replies per channel, used to avoid repeating openers.
recent_replies: Dict[int, Deque[str]] = {}

# Channel activity, used to tell "a quiet one-on-one chat" from "a busy channel
# where people are talking to each other and you would normally stay quiet".
channel_activity: Dict[int, Deque[float]] = {}

# Timestamps of replies, used for the per-minute cooldown.
reply_timestamps: Deque[float] = deque(maxlen=100)

# Messages already answered, so a duplicated/edited message is not answered
# twice. Keyed by channel + hash of the text.
answered_hashes: Dict[int, Deque[str]] = {}


def get_history(channel_id: int) -> Deque[dict]:
    if channel_id not in conversation_memory:
        conversation_memory[channel_id] = memory.load_messages(channel_id, MAX_HISTORY * 2 + 1)
    return conversation_memory[channel_id]


def append_history(channel_id: int, role: str, content: str) -> None:
    hist = get_history(channel_id)
    hist.append({"role": role, "content": content})
    memory.save_messages(channel_id, hist, MAX_HISTORY * 2 + 1)


def build_messages(channel_id: int) -> List[dict]:
    """Prepend system prompt + history. History already capped by deque."""
    now = time.localtime()
    stamp = time.strftime("%H:%M", now)
    system = f"{SYSTEM_PROMPT}\n\n(current local time: {stamp})"
    return [{"role": "system", "content": system}] + list(get_history(channel_id))


def remember_reply(channel_id: int, reply: str) -> None:
    if channel_id not in recent_replies:
        recent_replies[channel_id] = deque(maxlen=12)
    recent_replies[channel_id].append(reply.strip().lower())
    memory.save_thing_told(channel_id, reply)


def recent_openers(channel_id: int) -> List[str]:
    """The first few words of the last few replies."""
    if channel_id not in recent_replies:
        return []
    out = []
    for r in recent_replies[channel_id]:
        words = r.split()
        if words:
            out.append(" ".join(words[:2]))
    return out


def channel_is_busy(channel_id: int) -> bool:
    """True when several messages hit this channel in the last minute."""
    now = time.time()
    recent = [t for t in channel_activity.get(channel_id, ()) if now - t < 60]
    return len(recent) >= 4


def record_channel_activity(channel_id: int) -> None:
    if channel_id not in channel_activity:
        channel_activity[channel_id] = deque(maxlen=30)
    channel_activity[channel_id].append(time.time())


def rate_limited() -> bool:
    """True when the bot has replied too many times in the last minute."""
    if MAX_REPLIES_PER_MIN <= 0:
        return False
    now = time.time()
    while reply_timestamps and now - reply_timestamps[0] > 60:
        reply_timestamps.popleft()
    return len(reply_timestamps) >= MAX_REPLIES_PER_MIN


# ---------------------------------------------------------------------------
# Teen texting style
# ---------------------------------------------------------------------------
# The model will never be 100% consistent about dropping caps and apostrophes,
# so a deterministic pass normalizes the reply afterwards. This is what makes
# the output read as "texted by a 15 year old" rather than "written by a model
# that was asked to".
#
# The pass is deliberately conservative. Over-normalising is what produces the
# classic bot tell — every line identical, punctuated like a press release.
# The rules here only remove the things a person on a phone would never type:
# markdown, capitals, apostrophes, full stops at the end, doubled spaces.
# ---------------------------------------------------------------------------

# Contractions typed without the apostrophe, the way they actually get sent.
_CONTRACTIONS = {
    "i'm": "im", "i've": "ive", "i'll": "ill", "i'd": "id",
    "you're": "ur", "you've": "uv", "you'll": "ull", "you'd": "ud",
    "it's": "its", "that's": "thats", "there's": "theres", "what's": "whats",
    "here's": "heres",
    "he's": "hes", "she's": "shes", "they're": "theyre", "they've": "theyve",
    "we're": "were", "we've": "weve",
    "let's": "lets", "who's": "whos", "how's": "hows", "where's": "wheres",
    "don't": "dont", "doesn't": "doesnt", "didn't": "didnt", "can't": "cant",
    "couldn't": "couldnt", "won't": "wont", "wouldn't": "wouldnt",
    "shouldn't": "shouldnt", "isn't": "isnt", "aren't": "arent",
    "wasn't": "wasnt", "weren't": "werent", "haven't": "havent",
    "hasn't": "hasnt", "ain't": "aint", "y'all": "yall", "ma'am": "maam",
    "o'clock": "oclock",
}

# Emojis that a 15 year old actually uses. Not the crying-laughing pile.
_TEEN_EMOJI = ["💀", "😭", "🫶", "✨", "💅", "😩", "🤭", "🙄", "😌"]

# Emoji reactions used when the bot reacts instead of replying. Discord's
# reaction picker is a small set — this is roughly what people actually hit.
_REACT_EMOJI = ["👍", "😂", "😭", "💀", "🔥", "😮", "👀", "🎉", "😌", "🫶", "💯", "🤨"]

# Quick reactions fired off before the real reply — the Discord "burst" that
# a person sends when something lands. Mostly short, mostly lowercase.
_REACTIONS = [
    "no way", "stoppp", "wait", "wait what", "im crying", "lmaooo", "no bc why",
    "ayoo", "nahhh", "bruh", "no shot", "im deceased", "screaming", "oh no",
    "yesss", "hahahaha", "im so sorry", "pls", "😭", "💀", "✨", "🫶",
    "help", "nah", "ayy", "omg", "waittt", "bro", "no no no", "leaving",
    "nah fr", "im so sorry", "moms coming in", "one sec",
]

# Short follow-ups people send right after the real message.
_FOLLOWUPS = [
    "wait i just remembered",
    "btw are you at school tmrw",
    "ok anyway",
    "sorry that was a lot lol",
    "actually no i lied",
    "wait no i take that back",
    "oh and dont forget ur charger",
]

# Alternative openers used to break up a repeated opener.
_ALT_OPENERS = [
    "ok so", "wait", "hmm", "uh", "honestly", "nah", "ugh", "lol", "bro",
    "actually", "huh", "omg", "no way",
]

# Words that must survive the typo pass untouched — names, handles, and the
# slang that carries the voice. Mangling "rizz" into "rizz" is not the joke.
_TYPO_SAFE = {
    "rizz", "rizzler", "delulu", "solulu", "bussin", "slaps",
    "aura", "cringe", "pookie", "glazing", "discord", "spotify", "netflix",
    "youtube", "twitter", "tiktok", "iphone", "school", "homework",
    "coffee", "chicken", "gonna", "wanna", "highkey", "okay", "hello",
    "please", "thank", "sorry", "yass", "yep", "yeah", "nope", "cool",
    "nice", "same", "best", "well", "wait", "stop", "haha", "lmao",
    "good", "love", "miss", "theyre", "wanna", "imma", "ight", "actually",
    "discord", "everyone", "everybody", "something", "someone", "anything",
    "nothing", "everything", "tomorrow", "yesterday", "because", "about",
}

# Used when the model returns nothing usable. Short, in voice, no brackets.
_FALLBACKS = [
    "wait what",
    "huh",
    "bruh",
    "my brain just blue screened",
    "hm",
    "ok but like what",
    "i got nothing",
    "say that again but slower",
]

_MARKDOWN_BULLET = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}\s*", re.MULTILINE)
_NUMBERED_ITEM = re.compile(r"(^|\s)\d+[.)][ \t]+")
_WORD = re.compile(r"[A-Za-z']+")
_TYPO_TARGET = re.compile(r"\b[a-z]{4,}\b")
_EMOJI_AT_END = re.compile(r"([\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]+)$")
_EMOJI_ANY = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
# Keyboard neighbours — used for the "hit the wrong key" typo.
_KEY_NEIGHBOURS = {
    "a": "qswz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr",
    "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn",
    "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "iklp",
    "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy",
    "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
    "z": "asx",
}

# The assistant habits that no friend has. Stripping them is worth more than
# any amount of slang, because "let me know if you need anything" is the
# loudest tell there is.
_AI_LEADING = (
    "as an ai", "im an ai", "im here to help", "heres what you need to know",
    "here is what you need to know", "great question", "certainly",
    "i'd be happy to", "id be happy to", "absolutely", "of course",
    "heres a quick", "here are some", "to summarize", "in summary",
    "overall", "additionally", "keep in mind", "its important to note",
)
_AI_ANYWHERE = (
    "let me know if", "i hope this helps", "hope that helps", "hope this helps",
    "feel free to", "is there anything else", "as a language model",
    "i can help with", "i can definitely", "you can use this", "if you need",
    "dont hesitate to", "id love to help", "glad i could help",
)
_AI_ISMS = (
    re.compile(r"[\s,]*as an ai(?: language model)?", re.IGNORECASE),
    re.compile(r"[\s,]*i(?:'m| am|m) (?:just )?an ai(?: language model)?", re.IGNORECASE),
    re.compile(r"[\s,]*great question[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*certainly[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*let me know if you need (?:any ?thing|help)[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*i hope (?:this|that) helps[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*hope (?:this|that) helps[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*feel free to (?:ask|reach out)[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*is there anything else i can help(?: you)?(?: with)?[?.]?", re.IGNORECASE),
    re.compile(r"[\s,]*i(?:'m| am|m) here to help[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*i(?:'d| would| will) be happy to help[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*to (?:summarize|in summary)[,.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*keep in mind that[.!]?", re.IGNORECASE),
    re.compile(r"[\s,]*dont hesitate to (?:ask|reach out)[.!]?", re.IGNORECASE),
)


def _strip_ai_isms(text: str) -> str:
    """Remove the phrases a customer-service bot says and a friend never would.

    A whole sentence goes when it is clearly an assistant sentence. When it is
    the only sentence, the offending phrase is trimmed instead, so the reply
    never comes back empty.
    """
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for part in parts:
        stripped = part.strip().lower()
        if not stripped:
            continue
        if any(stripped.startswith(m) for m in _AI_LEADING) or \
                any(m in stripped for m in _AI_ANYWHERE):
            continue
        kept.append(part)
    if not kept:
        # Every sentence was an assistant sentence. Say nothing rather than
        # something robotic.
        return ""
    out = " ".join(kept)
    for pattern in _AI_ISMS:
        out = pattern.sub("", out)
    return out.strip(" ,")


def _contract(match: re.Match) -> str:
    word = match.group(0)
    return _CONTRACTIONS.get(word.lower(), word)


def _add_typo(text: str) -> str:
    """Drop in one believable phone typo.

    Nobody types perfectly on a phone. A flawless message is the thing that
    gives a bot away, so at low probability one word gets the treatment a
    thumb produces: a doubled letter, a swallowed one, a swapped pair, or a
    key struck just off the edge.
    """
    candidates = [m for m in _TYPO_TARGET.finditer(text) if m.group(0) not in _TYPO_SAFE]
    if not candidates:
        return text

    target = random.choice(candidates)
    word = target.group(0)
    roll = random.random()

    if roll < 0.3:
        # doubled letter: "really" -> "reallly"
        pos = random.randint(1, len(word) - 2)
        broken = word[:pos] + word[pos] + word[pos:]
    elif roll < 0.55:
        # swallowed letter: "really" -> "realy"
        pos = random.randint(1, len(word) - 2)
        broken = word[:pos] + word[pos + 1:]
    elif roll < 0.8:
        # swapped pair: "really" -> "realiy"
        pos = random.randint(1, len(word) - 2)
        broken = word[:pos] + word[pos + 1] + word[pos] + word[pos + 2:]
    else:
        # wrong key next door: "really" -> "rexlly"
        pos = random.randint(0, len(word) - 1)
        ch = word[pos]
        neighbours = _KEY_NEIGHBOURS.get(ch)
        if not neighbours:
            return text
        broken = word[:pos] + random.choice(neighbours) + word[pos + 1:]

    return text[:target.start()] + broken + text[target.end():]


def _add_repeated_word(text: str) -> str:
    """The doubled word typo ("i went to the the mall")."""
    words = text.split(" ")
    idxs = [i for i, w in enumerate(words)
            if len(w) > 3 and w.isalpha() and w.lower() not in _TYPO_SAFE]
    if not idxs:
        return text
    i = random.choice(idxs)
    words.insert(i, words[i])
    return " ".join(words)


def _pick_emoji_for(text: str) -> str:
    """Pick an emoji that matches what was actually said."""
    lowered = text.lower()
    if any(w in lowered for w in ("lol", "lmao", "haha", "funny", "shes so")):
        return random.choice(["💀", "😂", "😭"])
    if any(w in lowered for w in ("sad", "sorry", "rip", "cry", "hurt")):
        return random.choice(["😭", "😔", "🫂"])
    if any(w in lowered for w in ("love", "miss you", "you're cute", "ur cute")):
        return random.choice(["🫶", "✨", "💗"])
    if any(w in lowered for w in ("wow", "no way", "omg", "actually")):
        return random.choice(["😮", "🔥", "✨"])
    return random.choice(_TEEN_EMOJI)


def style_reply(text: str) -> str:
    """Rewrite a reply into the voice of a teen girl texting on Discord."""
    if not TEEN_STYLE or not text:
        return text

    out = text.strip()
    # Markdown, quotes the model wraps answers in.
    out = out.replace("**", "").replace("__", "").replace("`", "")
    out = _MARKDOWN_HEADING.sub("", out)
    out = _MARKDOWN_BULLET.sub("", out)
    out = _NUMBERED_ITEM.sub(r"\1", out)
    out = _WORD.sub(_contract, out)
    out = out.lower()
    out = _strip_ai_isms(out)
    # A wrapped quote is a tell. Peel it off, keep whatever is inside.
    if len(out) > 2 and out[0] == '"' and out[-1] == '"':
        out = out[1:-1].strip()
    # Ellipses are how people actually trail off. Keep them, cap the run.
    out = re.sub(r"\.{4,}", "...", out)
    out = re.sub(r"\.{3,}\.", "...", out)
    out = re.sub(r"(?<!\.)\.\.(?!\.)", ".", out)
    # No space before punctuation.
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    # Collapse runs of punctuation, but leave "..." alone.
    out = re.sub(r"([!?])\1{2,}", r"\1\1", out)
    # Laughter has a rhythm: "hahaha" -> "haha", never "hahahahaha".
    out = re.sub(r"(h+a){3,}", "haha", out)
    out = re.sub(r"(l+m+a+o){2,}", "lmao", out)
    # Tidy whitespace, keeping line breaks — multi-line messages are human.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = out.strip()
    # No full stop at the end of a message. "..." and "!" are fine.
    out = re.sub(r"(?<!\.)\.$", "", out).strip()
    # Nobody ends a text with a colon.
    out = re.sub(r"[:;]+$", "", out).strip()

    # More than two emoji in one text is not how anyone types.
    emojis = _EMOJI_ANY.findall(out)
    if len(emojis) > 2:
        seen = 0
        kept_chars = []
        for ch in out:
            if _EMOJI_ANY.match(ch):
                seen += 1
                if seen > 2:
                    continue
            kept_chars.append(ch)
        out = "".join(kept_chars)

    if EMOJI_CHANCE > 0 and not _EMOJI_AT_END.search(out) and random.random() < EMOJI_CHANCE:
        out = f"{out} {_pick_emoji_for(out)}"

    if TYPO_CHANCE > 0 and random.random() < TYPO_CHANCE:
        if random.random() < 0.15:
            out = _add_repeated_word(out)
        else:
            out = _add_typo(out)

    return out


def split_reply(text: str, max_parts: int = 2) -> List[str]:
    """Split a long reply into pieces, the way someone sends it in parts.

    The split lands on a sentence boundary when there is one in a sensible
    place, so the first message still reads as a finished thought. Newlines
    and trailing "..." count as boundaries too, because that is where people
    actually break a message.
    """
    if len(text) < 120 or max_parts < 2:
        return [text]

    candidates = []
    for sep in (". ", "! ", "? ", "... ", "\n"):
        start = 0
        while True:
            idx = text.find(sep, start)
            if idx < 0:
                break
            candidates.append(idx + len(sep))
            start = idx + len(sep)

    # Prefer a real sentence boundary, the one closest to the middle, as long as
    # it is not a tiny fragment at the front.
    viable = [c for c in candidates if c >= len(text) * 0.3]
    if viable:
        cut = min(viable, key=lambda c: abs(c - len(text) / 2))
    else:
        # No sentence boundary in range — fall back to a word break.
        idx = text.rfind(" ", 0, int(len(text) / 2) + 40)
        if idx <= 0:
            return [text]
        cut = idx + 1

    head, tail = text[:cut].strip(), text[cut:].strip()
    if not head or not tail:
        return [text]
    return [head, tail]


def vary_opener(text: str, channel_id: int) -> str:
    """Stop the bot from opening five replies in a row the same way."""
    openers = recent_openers(channel_id)
    if not openers:
        return text
    words = text.split()
    if len(words) < 2:
        return text
    current = " ".join(words[:2]).lower()
    if current not in openers:
        return text
    # Also check persistent memory for repeated statements
    if memory.was_told_recently(channel_id, text):
        # Swap in a different opener rather than burning another AI call.
        for candidate in random.sample(_ALT_OPENERS, len(_ALT_OPENERS)):
            if candidate in text.lower():
                continue
            return f"{candidate} {text}"
    return text


# ---------------------------------------------------------------------------
# Typing simulation
# ---------------------------------------------------------------------------

def read_time(text: str) -> float:
    """How long it takes a person to read what was sent to them."""
    words = len(text.split())
    return min(8.0, 0.35 + words / 4.0)  # roughly 240 wpm


def think_time(incoming: str, reply: str) -> float:
    """How long to sit on the message before the typing indicator appears.

    The distribution matters more than the average. Most replies go out after
    a few seconds, but every so often a person sits on a message, gets
    distracted, and answers a minute later. That long tail is the difference
    between "fast bot" and "person".
    """
    base = THINK_BASE_SEC + min(len(incoming), 400) * 0.006
    base += min(len(reply), 300) * 0.002
    base += random.uniform(0, JITTER_SEC)
    # Exponential tail: usually tiny, occasionally very long.
    base += random.expovariate(1.0 / max(0.5, THINK_TAIL_SEC / 4.0))
    return min(base, THINK_MAX_SEC + THINK_TAIL_SEC)


def typing_time(text: str) -> float:
    """How long the typing indicator should stay up for a chunk of text."""
    speed = random.uniform(TYPING_PER_CHAR_SEC * 0.7, TYPING_PER_CHAR_SEC * 1.6)
    delay = TYPING_BASE_SEC + len(text) * speed
    delay += random.uniform(0, JITTER_SEC * 0.5)
    return min(delay, TYPING_MAX_SEC)


async def _typing_indicator(channel, total: float) -> None:
    """Hold the typing indicator for `total` seconds, flickering if a person would."""
    if total <= 0:
        return
    if TYPING_PAUSE_CHANCE > 0 and total > 4 and random.random() < TYPING_PAUSE_CHANCE:
        # The person got distracted halfway through, indicator went away, then
        # came back.
        first = total * random.uniform(0.3, 0.6)
        async with channel.typing():
            await asyncio.sleep(first)
        await asyncio.sleep(random.uniform(0.8, 3.5))
        async with channel.typing():
            await asyncio.sleep(max(0.4, total - first))
        return
    async with channel.typing():
        await asyncio.sleep(total)


async def _await_with_typing(channel, source):
    """Wait for the reply with the typing indicator up.

    The indicator covers the whole time the model is still writing, which is
    what a person does: they start typing, and the message lands when they
    are done. A flicker (the indicator dropping off and coming back) is kept
    for the same reason it was there in the first place — it is one of the
    details that make the account read as a person.
    """
    task = asyncio.ensure_future(source)
    flickered = TYPING_PAUSE_CHANCE > 0 and random.random() < TYPING_PAUSE_CHANCE

    if not flickered:
        async with channel.typing():
            return await task

    # The indicator drops off after a few seconds and comes back.
    try:
        async with channel.typing():
            return await asyncio.wait_for(asyncio.shield(task), timeout=4.0)
    except asyncio.TimeoutError:
        pass
    await asyncio.sleep(random.uniform(0.6, 2.0))
    async with channel.typing():
        return await task


async def deliver_reply(message: discord.Message, reply_source) -> str:
    """Send a reply the way a person would: read it, think, then type it out.

    `reply_source` is an awaitable (usually a task) that returns the reply
    text. It is started BEFORE this function runs, so the model is already
    writing while the bot sits on the message — the wait and the work happen
    at the same time instead of one after the other. That single change is
    the difference between answering in three seconds and answering in fifteen.

    Long replies go out as two messages with a pause between them, because
    nobody types a 400 character message in one go. Sometimes a quick reaction
    goes out first, the way you fire off "no way" before you type the thought.

    Returns the reply that was actually sent (empty string if nothing was).
    """
    incoming = message.content or ""

    # Stage 1 — read the message and sit on it. No typing indicator yet.
    # The reply length is unknown at this point (the model is still writing),
    # so the length term of the think time is skipped.
    think = think_time(incoming, "")

    # Stage 1b — the reaction burst. A quick "no way" fired off before the
    # real reply. Keyed on the incoming message: a short message does not
    # deserve a reaction of its own.
    if (REACTION_CHANCE > 0 and len(incoming) > 20
            and random.random() < REACTION_CHANCE):
        await asyncio.sleep(think * 0.4)
        try:
            await message.channel.send(random.choice(_REACTIONS))
        except discord.HTTPException:
            pass
        await asyncio.sleep(random.uniform(0.5, 1.5))
    else:
        await asyncio.sleep(think)

    # Stage 2 — the typing indicator goes up and stays up while the model
    # finishes writing, exactly like a person who started typing as soon as
    # they had something to say.
    reply = await _await_with_typing(message.channel, reply_source)
    if not reply:
        return ""

    if len(reply) > MAX_RESPONSE_CHARS:
        reply = reply[: MAX_RESPONSE_CHARS - 3] + "..."

    reply = style_reply(reply)
    if not reply:
        reply = random.choice(_FALLBACKS)
    reply = vary_opener(reply, message.channel.id)

    chunks = split_reply(reply) if SPLIT_MESSAGES else [reply]

    async with message.channel.typing():
        # A short hold, so the indicator does not flash for a split second.
        await asyncio.sleep(min(typing_time(chunks[0]), TYPING_TAIL_MAX_SEC))

        for index, chunk in enumerate(chunks):
            if index == 0:
                if random.random() < PING_REPLY_CHANCE:
                    await message.reply(chunk)
                else:
                    await message.channel.send(chunk)
            else:
                await message.channel.send(chunk)
            if index < len(chunks) - 1:
                await asyncio.sleep(random.uniform(0.6, 1.6))

    # Stage 3 — the thing people do right after hitting send.
    if FOLLOWUP_CHANCE > 0 and len(reply) > 60 and random.random() < FOLLOWUP_CHANCE:
        await asyncio.sleep(random.uniform(2.0, 6.0))
        try:
            await message.channel.send(random.choice(_FOLLOWUPS))
        except discord.HTTPException:
            pass

    return reply


# ---------------------------------------------------------------------------
# Engagement — the "do I even reply to this?" gate
# ---------------------------------------------------------------------------

def _mentions_bot(message: discord.Message) -> bool:
    return bot.user in message.mentions


def _replies_to_bot(message: discord.Message) -> bool:
    """True when this message is a direct reply to one of ours."""
    ref = getattr(message, "reference", None)
    if not ref:
        return False
    resolved = getattr(ref, "resolved", None)
    if resolved is None:
        return False
    return getattr(resolved, "author", None) is not None and resolved.author.id == bot.user.id


def _replies_to_someone_else(message: discord.Message) -> bool:
    """True when the message is a reply to a third party (not us)."""
    ref = getattr(message, "reference", None)
    if not ref:
        return False
    resolved = getattr(ref, "resolved", None)
    if resolved is None:
        return False
    author = getattr(resolved, "author", None)
    if author is None:
        return False
    if author.id == bot.user.id:
        return False
    if TARGET_USER_ID and author.id == int(TARGET_USER_ID):
        return False
    return True


def engagement_gate(message: discord.Message, text: str) -> Tuple[bool, str]:
    """Decide whether to answer, react, or stay silent.

    Returns (action, reason) where action is "reply", "react" or "silent".
    """
    channel_id = message.channel.id
    is_dm = isinstance(message.channel, discord.DMChannel)

    # Target user is the whole point of TARGET_USER_ID — always reply to
    # them, unless they're explicitly replying to a 3rd party in a busy
    # channel (which would feel like an interruption).
    if is_target_user(message.author) and not _replies_to_someone_else(message):
        # Target user bypasses the per-minute rate limit too. Without this
        # an active conversation (target sends 7+ messages in 60s) would
        # suddenly go silent — that looks broken to the user.
        return "reply", "target_user"

    # Always answer a direct question of the target in a DM.
    if is_dm:
        if random.random() < REPLY_CHANCE + 0.25:
            return "reply", "dm"
        return "react", "dm"

    probability = REPLY_CHANCE

    if _mentions_bot(message):
        probability = 1.0
    elif _replies_to_bot(message):
        probability = min(1.0, probability + 0.35)
    elif _replies_to_someone_else(message):
        # They are talking to someone else. Interrupting here is the single
        # most bot-like thing a bot can do.
        probability *= 0.25
    elif "?" in text:
        probability += 0.12
    elif len(text.split()) <= 3:
        # One word messages get short replies, but not always a reply.
        probability += 0.05

    if channel_is_busy(channel_id):
        probability *= 0.45

    if rate_limited():
        return "silent", "cooldown"

    probability = max(0.02, min(0.98, probability))
    if random.random() < probability:
        return "reply", "engaged"

    if USE_REACTIONS and random.random() < REACT_ONLY_CHANCE:
        return "react", "passed"
    return "silent", "passed"


async def react_to(message: discord.Message) -> None:
    """Drop an emoji reaction on a message, the way people acknowledge chat."""
    if not USE_REACTIONS:
        return
    emoji = random.choice(_REACT_EMOJI)
    try:
        await message.add_reaction(emoji)
        log.info("Reacted %s to message %s", emoji, message.id)
    except discord.HTTPException as e:
        log.debug("Reaction failed: %s", e)
    except Exception as e:  # noqa: BLE001
        log.debug("Reaction failed: %s", e)
    await asyncio.sleep(random.uniform(0.4, 1.6))


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
# NOTE: `discord.py-self` (the fork of discord.py used here) has NO `Intents`
# class — user accounts are not subject to gateway intents, so message content
# and DMs arrive automatically. Passing `intents=` raises AttributeError.
#
# `self_bot=True` is REQUIRED: it makes the command layer only process
# messages authored by this account (the owner), so `!ping` / `!send` / `!clear`
# / `!ai` respond to the account owner's commands and nobody else's.
bot = commands.Bot(command_prefix="!", self_bot=True)

# Pending messages per channel. Consecutive messages inside DEBOUNCE_SEC are
# grouped, so three lines in a row get ONE reply instead of three.
_pending: Dict[int, dict] = {}
_pending_task: Dict[int, asyncio.Task] = {}


@bot.event
async def on_connect():
    log.info("Gateway connected.")


@bot.event
async def on_disconnect():
    log.warning("Gateway disconnected — discord.py will attempt to resume.")


@bot.event
async def on_resumed():
    log.info("Gateway session resumed.")


@bot.event
async def on_ready():
    global proactive_task, _startup_message_sent
    log.warning("========================================")
    log.warning("SELF-BOT ACTIVE — automating a user account violates Discord ToS.")
    log.warning("Use a burner account. You can be banned.")
    log.warning("========================================")
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    log.info("AI provider: %s | endpoint: %s | model: %s", AI_PROVIDER, AI_BASE_URL, AI_MODEL)
    targets = build_ai_targets()
    log.info("AI targets in order: %s", " -> ".join(t["label"] for t in targets))
    if not HUGGINGFACE_TOKEN:
        log.info("No HUGGINGFACE_TOKEN set — Hugging Face is not available as a failover.")
    await resolve_target_user()
    if TARGET_USER_ID:
        log.info("Target user: %s (bot will respond to their messages anywhere)", TARGET_USER_ID)
    log.info("Human knobs: reply=%.0f%% react=%.0f%% debounce=%.1fs cap=%d/min",
             REPLY_CHANCE * 100, REACT_ONLY_CHANCE * 100, DEBOUNCE_SEC, MAX_REPLIES_PER_MIN)
    # NOTE: Slash commands are NOT supported on user accounts — no tree.sync() here.

    # Start the proactive loop (only once per process — it survives reconnects).
    if PROACTIVE_ENABLED and (proactive_task is None or proactive_task.done()):
        try:
            proactive_task = asyncio.create_task(_proactive_loop())
            log.info("Proactive loop started.")
        except RuntimeError as e:
            log.warning("Could not start proactive loop: %s", e)

    # Optional: send the configured startup message to the target user (once per process)
    if STARTUP_MESSAGE and TARGET_USER_ID and not _startup_message_sent:
        try:
            user = await bot.fetch_user(int(TARGET_USER_ID))
            dm_channel = await user.create_dm()
            await dm_channel.send(STARTUP_MESSAGE)
            _startup_message_sent = True
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


async def resolve_target_user() -> None:
    """Make sure TARGET_USER_ID really is a user id.

    Copying the id of a DM *channel* instead of the person is a very easy
    mistake, and it fails quietly: the bot still answers DMs (those are
    always answered) but it never answers in a server, and the startup DM
    says "user not found".

    So: if the configured id cannot be fetched, fall back to the last person
    this account was DM'd by, which is stored in the memory folder.
    """
    global TARGET_USER_ID

    if TARGET_USER_ID:
        try:
            user = await bot.fetch_user(int(TARGET_USER_ID))
            if str(user.id) != TARGET_USER_ID:
                TARGET_USER_ID = str(user.id)
            log.info("Target user: %s (%s)", user, TARGET_USER_ID)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("TARGET_USER_ID %s could not be fetched (%s).",
                        TARGET_USER_ID, type(e).__name__)

    fallback = memory.load_storh("last_dm_peer", "")
    if fallback:
        try:
            user = await bot.fetch_user(int(fallback))
            TARGET_USER_ID = str(user.id)
            log.warning("Using the last DM contact as the target user: %s (%s).",
                        user, TARGET_USER_ID)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Last DM contact %s could not be fetched either (%s).",
                        fallback, type(e).__name__)

    if TARGET_USER_ID:
        log.warning("TARGET_USER_ID %s is not a valid user id — the bot will only "
                    "answer DMs and mentions.", TARGET_USER_ID)
    else:
        log.warning("No target user set — bot will only respond to mentions and DMs.")


def should_respond(message: discord.Message) -> bool:
    """Decide if the bot should even consider this message."""
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
    # DMs — always considered
    if isinstance(message.channel, discord.DMChannel):
        return True
    # Server messages — consider if (a) bot is mentioned OR (b) message is from TARGET_USER
    if bot.user in message.mentions:
        return True
    if is_target_user(message.author):
        return True
    return False


def clean_mention(text: str) -> str:
    """Strip the bot mention from the message text."""
    # discord.py puts <@botid> or <@!botid>; just strip any <@...> prefix
    cleaned = re.sub(r"^<@!?\d+>\s*", "", text).strip()
    return cleaned


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def _already_answered(channel_id: int, text: str) -> bool:
    """True when this exact message was already answered recently."""
    if channel_id not in answered_hashes:
        answered_hashes[channel_id] = deque(maxlen=20)
    bucket = answered_hashes[channel_id]
    h = _hash_text(text)
    if h in bucket:
        return True
    bucket.append(h)
    return False


async def _process_pending(channel_id: int) -> None:
    """Answer the grouped messages for a channel. Runs after the debounce."""
    pending = _pending.pop(channel_id, None)
    _pending_task.pop(channel_id, None)
    if not pending:
        return

    message: discord.Message = pending["message"]
    texts: List[str] = pending["texts"]
    author = message.author

    combined = " ".join(t.strip() for t in texts if t.strip()).strip()
    if not combined:
        return

    # Do not answer the same message twice (edited/duplicated posts).
    if _already_answered(channel_id, combined):
        log.info("Skipping duplicate message in channel %s", channel_id)
        return

    action, reason = engagement_gate(message, combined)
    log.info("engagement_gate: action=%s reason=%s (channel=%s, author=%s)",
             action, reason, channel_id, message.author.display_name)

    if action == "react":
        log.info("Not replying in channel %s (%s) — reacting instead", channel_id, reason)
        await react_to(message)
        return
    if action == "silent":
        log.info("Not replying in channel %s (%s)", channel_id, reason)
        return

    log.info("Replying to %s in channel %s: %s", author, message.channel, combined[:80])

    # Record user message(s) in the conversation history
    for text in texts:
        append_history(channel_id, "user", f"{author.display_name}: {text}")

    # The model starts working NOW. While it writes, the bot reads the
    # message, thinks, and puts the typing indicator up — so the wait and
    # the work happen at the same time instead of one after the other.
    reply_task = asyncio.ensure_future(call_ai(build_messages(channel_id)))
    try:
        reply = await deliver_reply(message, reply_task)
    except Exception as e:  # noqa: BLE001 - never let a reply kill the bot
        log.error("Failed to deliver reply in channel %s: %s", channel_id, e)
        if not reply_task.done():
            reply_task.cancel()
        return

    if not reply_task.done():
        reply_task.cancel()
    if not reply:
        # The provider failed. Say NOTHING — an "[AI error: ...]" message is
        # the most bot-like thing that could possibly be sent.
        log.warning("No AI reply for channel %s — staying silent", channel_id)
        return

    reply_timestamps.append(time.time())
    append_history(channel_id, "assistant", reply)
    remember_reply(channel_id, reply)



async def _schedule_pending(channel_id: int) -> None:
    """(Re)arm the debounce timer for a channel."""
    task = _pending_task.get(channel_id)
    if task and not task.done():
        task.cancel()
    _pending_task[channel_id] = asyncio.create_task(_debounce_wait(channel_id))


async def _debounce_wait(channel_id: int) -> None:
    await asyncio.sleep(DEBOUNCE_SEC)
    await _process_pending(channel_id)


@bot.event
async def on_message(message: discord.Message):
    # Self-bot rule: only the user account itself can trigger prefix commands.
    # Other users' messages never trigger !ping / !send / !clear / !ai.
    if message.author == bot.user:
        await bot.process_commands(message)
        return  # never AI-respond to our own messages

    # Log every message we see, so the dashboard shows what's happening
    # even when the bot decides not to reply. This is critical for
    # debugging "doesn't talk by itself" issues.
    try:
        chan = message.channel.name if hasattr(message.channel, "name") else "DM"
        log.info("on_message: author=%s (id=%s) channel=%s (id=%s) is_target=%s content=%r",
                 message.author.display_name, message.author.id, chan, message.channel.id,
                 is_target_user(message.author), message.content[:80])
    except Exception:
        pass

    if not should_respond(message):
        log.info("  -> not responding (should_respond=False)")
        return

    # Remember who we are talking to. If TARGET_USER_ID was ever set to
    # something that is not a user id, this is what saves the day.
    if isinstance(message.channel, discord.DMChannel):
        peer = str(message.author.id)
        if peer != memory.load_storh("last_dm_peer", ""):
            memory.save_storh("last_dm_peer", peer)

    user_text = clean_mention(message.content)
    if not user_text:
        # Target user sent an empty/mention-only message in a server channel — ignore silently.
        return

    channel_id = message.channel.id
    record_channel_activity(channel_id)

    # Group consecutive messages so a burst of three lines gets one reply.
    pending = _pending.get(channel_id)
    if pending and pending["author_id"] == message.author.id:
        pending["texts"].append(user_text)
        pending["message"] = message  # reply-ping the newest one
    else:
        _pending[channel_id] = {
            "author_id": message.author.id,
            "texts": [user_text],
            "message": message,
        }
    await _schedule_pending(channel_id)


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
    task = asyncio.ensure_future(call_ai([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]))
    reply = await deliver_reply(ctx.message, task)
    if not reply:
        await ctx.reply("AI call failed — check the logs.")


@bot.command(name="clear")
async def clear_cmd(ctx: commands.Context):
    """Clear conversation memory in the current channel."""
    if ctx.channel.id in conversation_memory:
        conversation_memory[ctx.channel.id].clear()
    memory.save_messages(ctx.channel.id, deque(maxlen=MAX_HISTORY * 2 + 1), MAX_HISTORY * 2 + 1)
    await ctx.reply("Cleared my memory for this channel.")


@bot.command(name="clearthings")
async def clear_things_cmd(ctx: commands.Context):
    """Clear the 'things I told' memory for this channel (avoids repetition)."""
    path = memory._things_path(ctx.channel.id)
    if path.exists():
        path.unlink()
    await ctx.reply("Cleared 'things I told' memory for this channel.")


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

    # Diagnostic: show what token we're actually using (first 6 + last 4).
    # This catches two failure modes:
    #   1. The dashboard's set_key wrapped it in quotes -> the value has
    #      quote chars at the start/end that Discord rejects.
    #   2. The user pasted a bot application token (starts with MTk/MTU -
    #      those are base64 of a snowflake ID; user tokens don't).
    _preview = f"{DISCORD_USER_TOKEN[:6]}...{DISCORD_USER_TOKEN[-4:]}" if len(DISCORD_USER_TOKEN) > 10 else "?"
    _starts_quoted = DISCORD_USER_TOKEN.startswith(('"', "'"))
    _looks_like_bot_token = DISCORD_USER_TOKEN[:3] in ("MTk", "MTU")
    log.info("Token preview: %s (len=%d, quoted=%s, looks_like_bot_token=%s)",
             _preview, len(DISCORD_USER_TOKEN), _starts_quoted, _looks_like_bot_token)
    if _starts_quoted:
        log.error("TOKEN STILL HAS QUOTES — fix your .env manually: "
                  "DISCORD_USER_TOKEN=<raw_token_no_quotes>")
    if _looks_like_bot_token:
        log.error("TOKEN LOOKS LIKE A BOT APPLICATION TOKEN — that won't work with "
                  "discord.py-self. You need a USER ACCOUNT token extracted from "
                  "the Discord web client (DevTools -> Network -> Authorization header).")

    # One account, one connection. Two bots on the same user account is the
    # fastest way to get it banned, so a second instance refuses to start.
    lock = SingleInstanceLock(os.path.join(BASE_DIR, ".bot.lock"))
    if not lock.acquire():
        log.error("Another process is already running this bot — refusing to start a second one.")
        raise SystemExit(1)

    log.warning("SELF-BOT mode — automating a user account. This violates Discord ToS.")
    log.info("Bot starting | provider=%s model=%s", AI_PROVIDER, AI_MODEL)
    log.info("Memory: %d msgs/channel", MAX_HISTORY)
    log.info("Pacing: think %.1f–%.1fs (+tail %.0fs) | typing %.1f–%.1fs | jitter ±%.1fs",
             THINK_BASE_SEC, THINK_MAX_SEC, THINK_TAIL_SEC, TYPING_BASE_SEC, TYPING_MAX_SEC, JITTER_SEC)
    log.info("Voice: %s | split=%s | teen_style=%s | reaction=%.0f%% | typo=%.0f%%",
             "15yo-girl persona" if SYSTEM_PROMPT == DEFAULT_SYSTEM_PROMPT else "custom prompt",
             SPLIT_MESSAGES, TEEN_STYLE, REACTION_CHANCE * 100, TYPO_CHANCE * 100)
    log.info("Human: reply=%.0f%% react_only=%.0f%% debounce=%.1fs cap=%d/min followup=%.0f%%",
             REPLY_CHANCE * 100, REACT_ONLY_CHANCE * 100, DEBOUNCE_SEC,
             MAX_REPLIES_PER_MIN, FOLLOWUP_CHANCE * 100)
    log.info("AI: timeout=%.0fs retries=%d stream=%s cooldown=%.0fmin",
             AI_TIMEOUT_SEC, AI_MAX_RETRIES, AI_STREAM, AI_COOLDOWN_SEC / 60)
    if TARGET_USER_ID:
        log.info("Target user: %s", TARGET_USER_ID)
    else:
        log.info("No target user set — bot will only respond to mentions and DMs.")

    # Keep trying. A bot that quits on the first hiccup is a bot that is
    # simply not there, which is the whole problem this loop exists to fix.
    attempt = 0
    while True:
        try:
            bot.run(DISCORD_USER_TOKEN)
            break
        except SystemExit:
            raise
        except discord.LoginFailure as e:
            attempt += 1
            if attempt >= 3:
                log.error("Discord refused the token (%s). Fix DISCORD_USER_TOKEN.", e)
                raise SystemExit(1)
            log.error("Discord refused the token (%s) — retrying in 10s.", e)
            time.sleep(10)
        except Exception as e:  # noqa: BLE001
            attempt += 1
            delay = min(300, 10 * attempt)
            log.error("Bot connection failed (%s: %s) — reconnecting in %ds.",
                      type(e).__name__, e, delay)
            time.sleep(delay)


if __name__ == "__main__":
    main()
