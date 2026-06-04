"""
setup_flow.py
-------------
The QUESTION BANK for the per-report questionnaire — just the 7 questions and a
few pure helpers. No Telegram handlers and no storage live here; telegram_bot.py
owns the conversation wiring and calls into these.

Flow (implemented in telegram_bot.py): the user sends a location, the questions
below are asked right after, and the answers are bundled with the location and
sent through the pipeline. Questions are asked fresh every time, so nothing is
stored between reports.

Each step: key, text, and either `options` (tappable buttons) and/or typed input.
`skip_if(profile)` → True skips that step (conditional follow-ups).
Special button values:  __skip__ (don't store, move on),
                        __type__ (switch to typed input for this key),
                        __same__ (inverter = system size).
"""

from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Callback prefix so question buttons never collide with the geo-picker ("geo|...").
CB = "su|"

STEPS: list[dict] = [
    {
        "key": "system_kwp",
        "text": "1/7 — What's your system size? (total panel capacity)",
        "options": [("3 kWp", "3"), ("5 kWp", "5"), ("10 kWp", "10"),
                    ("Other (type it)", "__type__"), ("Not sure", "__skip__")],
        "numeric": True,
        "type_prompt": "Type your system size in kWp (e.g. 5.5):",
    },
    {
        "key": "inverter_kw",
        "text": "2/7 — Inverter size? (catches power 'clipping')",
        "options": [("3 kW", "3"), ("5 kW", "5"), ("Same as panels", "__same__"),
                    ("Other (type it)", "__type__"), ("Not sure", "__skip__")],
        "numeric": True,
        "type_prompt": "Type your inverter size in kW (e.g. 4):",
    },
    {
        "key": "mount_type",
        "text": "3/7 — How are the panels mounted?",
        "options": [("Flush on roof", "flush"), ("Tilted frame", "tilted"),
                    ("Ground-mounted", "ground"), ("Raised / elevated", "elevated"),
                    ("Not sure", "__skip__")],
    },
    {
        "key": "tilt_band",
        "text": "4/7 — Roughly how steep is the tilt? (affects rain self-cleaning)",
        "options": [("Flat (<10 deg)", "flat"), ("Slight (10-15)", "low"),
                    ("Angled (15-35)", "optimal"), ("Steep (>35)", "steep"),
                    ("Not sure", "__skip__")],
    },
    {
        "key": "shading",
        "text": "5/7 — Does anything shade the panels during the day?",
        "options": [("No, full sun", "none"), ("Partly (some hours)", "partial"),
                    ("Yes, significant", "significant"), ("Not sure", "__skip__")],
    },
    {
        # Conditional: only if they reported shading.
        "key": "shading_detail",
        "text": "5b - What casts the shade, and roughly when? (e.g. 'water tank, afternoons')",
        "typed_only": True,
        "type_prompt": "Type what shades them and when (or send 'skip'):",
        "skip_if": lambda p: p.get("shading") not in ("partial", "significant"),
    },
    {
        "key": "last_cleaned",
        "text": "6/7 — When were the panels last cleaned?",
        "options": [("This week", "week"), ("This month", "month"),
                    ("2+ months ago", "months"), ("Never", "never"),
                    ("Not sure", "__skip__")],
    },
    {
        "key": "contaminant",
        "text": "6b - What mainly lands on them?",
        "options": [("Desert/road dust", "dust"), ("Sea salt", "salt"),
                    ("Bird droppings", "birds"), ("Industrial/cement", "industrial"),
                    ("Pollen/leaves", "pollen"), ("Not sure", "__skip__")],
    },
    {
        "key": "age_years",
        "text": "7/7 — How old is the system?",
        "options": [("New (<1 yr)", "new"), ("1-5 years", "1-5"),
                    ("5-10 years", "5-10"), ("10+ years", "10+"),
                    ("Not sure", "__skip__")],
    },
]


def keyboard(step: dict) -> InlineKeyboardMarkup:
    """Two buttons per row from a step's options."""
    rows, row = [], []
    for label, value in step.get("options", []):
        row.append(InlineKeyboardButton(label, callback_data=f"{CB}{value}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def parse_number(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", (text or "").replace(",", "."))
    if not m:
        return None
    try:
        v = float(m.group())
        return v if 0 < v < 1000 else None
    except ValueError:
        return None


def next_index(profile: dict, idx: int) -> int:
    """Advance past any steps whose skip_if applies. Returns len(STEPS) when done."""
    i = idx + 1
    while i < len(STEPS) and STEPS[i].get("skip_if", lambda p: False)(profile):
        i += 1
    return i