"""
telegram_bot.py
---------------
Telegram bot for SolarSense AI.
Uses python-telegram-bot with polling — no webhook, no ngrok, no hosting needed for local use.

SETUP:
    1. Talk to @BotFather on Telegram → /newbot → copy token
    2. Add TELEGRAM_BOT_TOKEN=your_token to .env
    3. pip install python-telegram-bot python-dotenv
    4. python telegram_bot.py

USAGE:
    User sends:  20.9077, 70.3626      (coordinates)
    User sends:  Veraval, Gujarat      (place name / address — geocoded automatically)
    User taps :  📎 → Location          (location pin)
    Bot replies: full solar report
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, constants
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from formatter import format_for_telegram
from graph import build_graph
from tools import geocode_place

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

# ── Thread pool for blocking work (pipeline + geocoding) ─────────────────────
executor = ThreadPoolExecutor(max_workers=4)

# ── Compiled graph (built ONCE, reused for every request) ─────────────────────
_GRAPH = None


def get_graph():
    """Lazily build and cache the compiled graph."""
    global _GRAPH
    if _GRAPH is None:
        log.info("Building LangGraph workflow (one-time)...")
        _GRAPH = build_graph()
    return _GRAPH


# ── Telegram message size limit ───────────────────────────────────────────────
TG_LIMIT = 4096
SAFE_LIMIT = 4000

# ── Messages ──────────────────────────────────────────────────────────────────

HELP_TEXT = r"""☀️ *SolarSense AI — Solar Panel Advisor*

Send me a *place name* or *coordinates* and I'll give you a real\-time solar efficiency report\.

*How to tell me where you are \(any of these\):*
• Type a place: `Veraval, Gujarat`
• Type coordinates: `20.9077, 70.3626`
• Tap 📎 → *Location* to share a pin

*I will analyze:*
• Current weather & cloud cover
• Air quality & dust soiling
• Solar potential \(PVGIS \+ NASA POWER\)
• Coastal salt aerosols \(if applicable\)

*You will get:*
✅ Today's efficiency score \(0\-100\)
🔬 What's hurting your output right now
🚨 Actions to recover power TODAY
📅 This week's quick wins
🏗 Long\-term upgrades with INR cost estimates

_Tip: a full city \+ state \+ country \(like Veraval, Gujarat, India\) resolves most reliably\._

Send a place or coordinates to get started\!"""


WAIT_TEXT = (
    "⏳ *Analyzing your location\\.\\.\\.*\n\n"
    "Fetching live data from 5 sources:\n"
    "🛰 PVGIS solar potential\n"
    "🌤 Open\\-Meteo weather \\(real\\-time\\)\n"
    "🌫 Air quality \\(PM10 / dust\\)\n"
    "🌊 Marine data \\(if coastal\\)\n"
    "🚀 NASA POWER climatology\n\n"
    "This takes about *20–40 seconds*\\.\\.\\."
)

ERROR_TEXT = (
    "❌ *Something went wrong while analyzing your location\\.*\n\n"
    "Please try again in a moment\\."
)

# Plain-text (no MarkdownV2) messages — place names contain punctuation that would
# otherwise need escaping, so we skip parse_mode for these.
NOT_FOUND_TEXT = (
    "❓ I couldn't find that place.\n\n"
    "Try adding more detail (e.g. 'Veraval, Gujarat, India'), or send coordinates "
    "like 20.9077, 70.3626."
)

LOOKUP_FAIL_TEXT = (
    "⚠️ The location lookup service is unreachable right now.\n\n"
    "Please try again shortly, or send coordinates like 20.9077, 70.3626."
)


# ── Coordinate parser ─────────────────────────────────────────────────────────

def parse_coordinates(text: str) -> tuple[float, float] | None:
    """
    Accepts many coordinate formats:
      20.9077, 70.3626  /  20.9077 70.3626  /  lat: 20.9077 lon: 70.3626  /  (20.9077, 70.3626)
    Returns None if the text isn't a coordinate pair (so it can be geocoded as a place).
    """
    cleaned = text.lower()
    cleaned = re.sub(r"(lat(itude)?|lon(gitude)?|:|\(|\))", " ", cleaned)
    numbers = re.findall(r"-?\d+\.\d+", cleaned)  # require a decimal point — names rarely have two decimals
    if len(numbers) >= 2:
        try:
            lat, lon = float(numbers[0]), float(numbers[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except ValueError:
            pass
    return None


def _query_variants(text: str) -> list[str]:
    """Progressively broader queries so a too-specific address still resolves.
    'shivji nagar veraval' -> ['shivji nagar veraval', 'nagar veraval', 'veraval']."""
    text = text.strip()
    seen: list[str] = []

    def add(q: str) -> None:
        q = q.strip(" ,")
        if q and q.lower() not in (s.lower() for s in seen):
            seen.append(q)

    add(text)
    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        for i in range(1, len(parts)):
            add(", ".join(parts[i:]))
    words = text.replace(",", " ").split()
    for i in range(1, len(words)):
        add(" ".join(words[i:]))
    return seen


def _resolve_place(text: str) -> list[dict]:
    """Blocking: run the variant cascade until a query returns candidates."""
    for q in _query_variants(text):
        candidates = geocode_place(q)
        if candidates:
            return candidates
    return []


# ── MarkdownV2 → plain text fallback ──────────────────────────────────────────

def _strip_markdown(text: str) -> str:
    out = re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", text)
    out = out.replace("*", "").replace("`", "")
    return out


def _enforce_length(chunks: list[str]) -> list[str]:
    """Split any chunk that exceeds Telegram's limit, breaking on newlines."""
    safe: list[str] = []
    for chunk in chunks:
        if len(chunk) <= TG_LIMIT:
            safe.append(chunk)
            continue
        buf = ""
        for line in chunk.split("\n"):
            if len(buf) + len(line) + 1 > SAFE_LIMIT:
                if buf:
                    safe.append(buf)
                buf = line
            else:
                buf = f"{buf}\n{line}" if buf else line
        if buf:
            safe.append(buf)
    return safe


# ── Pipeline runner ───────────────────────────────────────────────────────────

def run_pipeline(lat: float, lon: float) -> list[str]:
    final_state = get_graph().invoke({"lat": lat, "lon": lon})
    return _enforce_length(format_for_telegram(final_state))


# ── Telegram message sender ───────────────────────────────────────────────────

async def send_chunks(message, chunks: list[str]) -> None:
    """Send chunks with MarkdownV2; fall back to plain text per-chunk on rejection."""
    for chunk in chunks:
        try:
            await message.reply_text(chunk, parse_mode=constants.ParseMode.MARKDOWN_V2)
        except BadRequest as e:
            log.warning("MarkdownV2 rejected (%s); resending chunk as plain text", e)
            await message.reply_text(_strip_markdown(chunk))
        await asyncio.sleep(0.3)


async def _process_and_reply(update: Update, lat: float, lon: float) -> None:
    """Shared flow for typed coordinates, place names, and shared location pins.
    Uses effective_message so it works for both normal messages and button taps."""
    message = update.effective_message
    user = update.effective_user

    await message.chat.send_action(constants.ChatAction.TYPING)
    await message.reply_text(WAIT_TEXT, parse_mode=constants.ParseMode.MARKDOWN_V2)

    loop = asyncio.get_event_loop()
    try:
        chunks = await loop.run_in_executor(executor, run_pipeline, lat, lon)
        await send_chunks(message, chunks)
        log.info("Report sent to %s for (%.4f, %.4f)", user.full_name, lat, lon)
    except Exception as e:
        log.exception("Pipeline failed: %s", e)
        await message.reply_text(ERROR_TEXT, parse_mode=constants.ParseMode.MARKDOWN_V2)


# ── Command handlers ──────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=constants.ParseMode.MARKDOWN_V2)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=constants.ParseMode.MARKDOWN_V2)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles non-command text: coordinates OR a place name/address."""
    text = update.message.text.strip()
    user = update.effective_user
    log.info("Message from %s (@%s): %s", user.full_name, user.username, text[:80])

    # 1) Coordinates? Run immediately (no accuracy change vs before).
    coords = parse_coordinates(text)
    if coords:
        lat, lon = coords
        log.info("Parsed coordinates lat=%.4f lon=%.4f", lat, lon)
        await _process_and_reply(update, lat, lon)
        return

    # 2) Otherwise treat as a place name and geocode (in the thread pool).
    log.info("Geocoding place: %s", text)
    loop = asyncio.get_event_loop()
    try:
        candidates = await loop.run_in_executor(executor, _resolve_place, text)
    except Exception as e:
        log.warning("Geocoding service failed: %s", e)
        await update.message.reply_text(LOOKUP_FAIL_TEXT)
        return

    if not candidates:
        await update.message.reply_text(NOT_FOUND_TEXT)
        return

    if len(candidates) == 1:
        c = candidates[0]
        await update.message.reply_text(f"📍 Using: {c['display_name']}")
        await _process_and_reply(update, c["lat"], c["lon"])
        return

    # 3) Multiple matches → tappable buttons so the user picks the right one.
    buttons = [
        [InlineKeyboardButton(c["display_name"][:60],
                              callback_data=f"geo|{c['lat']:.5f}|{c['lon']:.5f}")]
        for c in candidates[:5]
    ]
    await update.message.reply_text(
        "I found a few matches — tap the right one:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_geo_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped one of the disambiguation buttons."""
    query = update.callback_query
    await query.answer()
    try:
        _, lat_s, lon_s = query.data.split("|")
        lat, lon = float(lat_s), float(lon_s)
    except (ValueError, AttributeError):
        await query.edit_message_text("Sorry, that selection was invalid — please resend your location.")
        return
    await query.edit_message_text(f"📍 Using selected location ({lat:.4f}, {lon:.4f})")
    await _process_and_reply(update, lat, lon)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles a shared location pin (📎 → Location)."""
    location = update.message.location
    lat, lon = location.latitude, location.longitude
    user = update.effective_user
    log.info("Location pin from %s (@%s): lat=%.4f lon=%.4f", user.full_name, user.username, lat, lon)
    await _process_and_reply(update, lat, lon)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")

    log.info("Starting SolarSense Telegram bot...")
    get_graph()  # eager build so the first user doesn't eat the compile cost

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(handle_geo_choice, pattern=r"^geo\|"))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()