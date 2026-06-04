"""
user_profiles.py
----------------
Per-user system profile: the few facts the bot CANNOT get from satellite/weather
data but that strongly change the advice (mount type, tilt, shading, size,
inverter, cleaning history, age). Collected by the question flow and passed to
the advisor.

Design choice: we DON'T pre-compute conclusions here. The block below simply
states the user's answers as plain facts; the LLM reasons about what they imply
(e.g. that a ground mount makes "add standoffs" pointless, or that an oversized
array vs a small inverter means clipping). Nothing is hardcoded as a directive.

Storage: a plain in-memory dict. Pure code — nothing written to disk.
"""

from __future__ import annotations

from typing import Any

# user_id -> profile dict. Lives in RAM for the life of the process.
_STORE: dict[int, dict[str, Any]] = {}


def get_profile(user_id: int) -> dict[str, Any]:
    """Return a COPY of the stored profile for a user (empty dict if none)."""
    return dict(_STORE.get(user_id, {}))


def save_profile(user_id: int, profile: dict[str, Any]) -> None:
    """Overwrite the stored profile for a user (drops None/empty values)."""
    _STORE[user_id] = {k: v for k, v in profile.items() if v not in (None, "")}


def clear_profile(user_id: int) -> None:
    _STORE.pop(user_id, None)


def has_profile(user_id: int) -> bool:
    return bool(_STORE.get(user_id))


# ── Derived value the pipeline needs (a number, not a recommendation) ──────────

DEFAULT_KWP = 5.0


def effective_baseline_kwh(per_kwp_yield: float, profile: dict | None) -> float:
    """Real annual baseline using the user's actual system size (else 5 kWp).
    This is just the kWh math for the report — not advice."""
    kwp = (profile or {}).get("system_kwp") or DEFAULT_KWP
    try:
        return float(per_kwp_yield or 0) * float(kwp)
    except (TypeError, ValueError):
        return float(per_kwp_yield or 0) * DEFAULT_KWP


# ── Plain-fact rendering for the advisor prompt (no conclusions baked in) ──────

_LABELS = {
    "system_kwp": "System size",
    "inverter_kw": "Inverter size",
    "mount_type": "Mount",
    "tilt_band": "Tilt",
    "shading": "Shading",
    "shading_detail": "Shading detail",
    "last_cleaned": "Last cleaned",
    "contaminant": "What lands on the panels",
    "age_years": "System age",
}
# Neutral wording that just echoes what the user picked — no editorialising.
_VALUE_LABELS = {
    "mount_type": {"flush": "flush on roof", "tilted": "tilted frame",
                   "ground": "ground-mounted", "elevated": "raised/elevated"},
    "tilt_band": {"flat": "flat (<10 deg)", "low": "slight (10-15 deg)",
                  "optimal": "angled (15-35 deg)", "steep": "steep (>35 deg)"},
    "shading": {"none": "none (full sun)", "partial": "partial (some hours)",
                "significant": "significant"},
    "last_cleaned": {"week": "this week", "month": "this month",
                     "months": "2+ months ago", "never": "never"},
    "contaminant": {"dust": "desert/road dust", "salt": "sea salt", "birds": "bird droppings",
                    "industrial": "industrial/cement", "pollen": "pollen/leaves"},
    "age_years": {"new": "<1 yr", "1-5": "1-5 yrs", "5-10": "5-10 yrs", "10+": "10+ yrs"},
}


def profile_prompt_block(profile: dict | None) -> str:
    """Render the user's answers as plain facts. No directives, no conclusions —
    the LLM decides what they imply."""
    p = profile or {}
    if not p:
        return ("=== USER SYSTEM PROFILE ===\n"
                "(The user skipped the questions — no system details. Assume a typical 5 kWp "
                "rooftop and advise from the site data alone.)")

    lines = ["=== USER SYSTEM PROFILE ===",
             "The user told you these facts about their own system. Treat them as ground truth, "
             "reason about what they imply, and make sure every recommendation is consistent with "
             "them (don't suggest something these facts make pointless or impossible):"]
    for key, label in _LABELS.items():
        if key not in p:
            continue
        val = p[key]
        if key in ("system_kwp", "inverter_kw"):
            val = f"{val} {'kWp' if key == 'system_kwp' else 'kW'}"
        else:
            val = _VALUE_LABELS.get(key, {}).get(val, val)
        lines.append(f"  - {label}: {val}")
    return "\n".join(lines)