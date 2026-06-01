
# """
# validator.py
# ------------
# Repairs and bounds LLM output BEFORE it reaches the user. The LLM (DeepSeek) is
# good at narrative and bad at (a) not looping, (b) not exceeding physical limits,
# (c) keeping kWh consistent with %. This module fixes all three deterministically,
# so a model hiccup degrades gracefully instead of shipping the "nanocoating (e.g.,
# nanocoating (e.g.," / "consistently. consistently." garbage seen in production.

# Two entry points:
#   - validate_analysis(analysis, physics)  -> (clean_analysis, issues)
#   - validate_recommendations(recs, analysis, baseline_kwh) -> (clean_recs, issues)
# """

# from __future__ import annotations

# import re
# from typing import Any

# # Active BAD advice: misting/spraying water on panels TO COOL them during heat.
# # The discriminator is COOLING INTENT — a dawn dust-clean never says "cool the panels",
# # only thermal-shock-risky midday misting does. We also exclude dawn/evening phrasing.
# _WET = re.compile(r"\b(spray|mist|splash|pour|hose|sprinkle)\b", re.I)
# _PANEL = re.compile(r"\b(panel|module|glass|cell|surface)s?\b", re.I)
# _COOL_INTENT = re.compile(r"\bcool(?:ing|s|ed)?\b|\blower\b.{0,30}\btemperature\b", re.I)
# _SAFE_TIME = re.compile(r"\b(before|ahead of|dawn|sunrise|early morning|after sunset|evening|night)\b", re.I)


# def _is_unsafe_action(text: str) -> bool:
#     """True for 'wet the panels to cool them during heat' — thermal-shock risk."""
#     if not (_WET.search(text) and _PANEL.search(text) and _COOL_INTENT.search(text)):
#         return False
#     # Dawn / evening misting is fine — only flag if NOT explicitly timed to a safe window.
#     return not _SAFE_TIME.search(text)


# # Wrong / counterproductive advice the model sometimes pads with on thin days.
# _TILT_FIXED = re.compile(r"\b(tilt|re-?angle|reposition|re-?orient|adjust the angle)\b.{0,40}"
#                          r"\b(array|panel|module)s?\b.{0,40}\b(wind|convection|cool)", re.I)
# _BAD_COATING = re.compile(r"\brain-?x\b|\b(hydrophobic|consumer)\b.{0,30}\b(coating|spray)\b", re.I)


# def _is_low_quality_action(text: str) -> str | None:
#     """Return a reason string if the action is wrong/counterproductive, else None."""
#     if _TILT_FIXED.search(text):
#         return "tilting a fixed array to 'catch wind' (fixed arrays don't move; loses more than it gains)"
#     if _BAD_COATING.search(text):
#         return "consumer hydrophobic coating as anti-soiling (can cement dust in arid air)"
#     return None


# # ── Text de-looping ───────────────────────────────────────────────────────────

# def deloop_text(text: str) -> str:
#     """
#     Collapse the runaway repetition DeepSeek sometimes emits:
#       - repeated sentences ("consistently. consistently. consistently.")
#       - repeated word-blocks ("strong local winds. strong local winds.")
#       - interleaved clause fragments ("nanocoating (e.g., nanocoating (e.g.,")
#     Keeps the FIRST occurrence, preserves order, never invents text. As a last
#     resort, truncates output that is still pathologically repetitive so the user
#     never sees a wall of loop.
#     """
#     if not text or not isinstance(text, str):
#         return text or ""

#     # 1) Sentence-level dedup (keep first occurrence of each normalized sentence).
#     sentences = re.split(r"(?<=[.!?])\s+", text.strip())
#     seen: set[str] = set()
#     kept: list[str] = []
#     for s in sentences:
#         norm = re.sub(r"\s+", " ", s.strip().lower())
#         if norm and norm not in seen:
#             seen.add(norm)
#             kept.append(s.strip())
#     out = " ".join(kept)

#     # 2) Collapse consecutive repeated word-blocks (size 8 → 1), longest first.
#     #    Iterate to a fixpoint so boundary-adjacent repeats also collapse.
#     for _ in range(6):
#         collapsed = _collapse_word_repeats(out)
#         if collapsed == out:
#             break
#         out = collapsed

#     # 3) Collapse repeated single words ("dust dust dust").
#     out = re.sub(r"\b(\w+)(\s+\1\b){1,}", r"\1", out, flags=re.I)
#     out = re.sub(r"\s{2,}", " ", out).strip()

#     # 4) Safety net: if it is STILL highly repetitive (low unique-word ratio and
#     #    long), keep only the first two sentences — never ship a loop wall.
#     words = out.split()
#     if len(words) > 40 and len(set(w.lower() for w in words)) / len(words) < 0.5:
#         first = re.split(r"(?<=[.!?])\s+", out)[:2]
#         out = " ".join(first).strip()

#     return out


# def _collapse_word_repeats(text: str) -> str:
#     """Emit one copy of any immediately-repeated word-block, skipping all copies."""
#     words = text.split()
#     out: list[str] = []
#     i, n = 0, len(words)
#     while i < n:
#         matched = False
#         for size in range(min(8, (n - i) // 2), 0, -1):
#             block = words[i:i + size]
#             if block == words[i + size:i + 2 * size]:
#                 out.extend(block)
#                 j = i + size
#                 while words[j:j + size] == block:   # skip every consecutive copy
#                     j += size
#                 i = j
#                 matched = True
#                 break
#         if not matched:
#             out.append(words[i])
#             i += 1
#     return " ".join(out)


# def _norm_action(text: str) -> str:
#     """Normalized key for detecting duplicate recommendation rows."""
#     t = deloop_text(text).lower()
#     t = re.sub(r"[^a-z0-9 ]", "", t)
#     return " ".join(t.split()[:12])  # first 12 words is enough to catch dupes


# # ── Analysis validation ───────────────────────────────────────────────────────

# def validate_analysis(analysis: dict[str, Any], physics: dict[str, Any]) -> tuple[dict, list[str]]:
#     """
#     Clamp the LLM's factor numbers to the physics engine and recompute the
#     headline so the displayed score is always internally consistent.
#     """
#     issues: list[str] = []
#     a = dict(analysis or {})

#     factors = a.get("factors") or []
#     if not isinstance(factors, list):
#         factors = []

#     # De-loop every text field and clamp impacts to a sane range.
#     clean_factors = []
#     for f in factors:
#         if not isinstance(f, dict):
#             continue
#         f = dict(f)
#         f["name"] = deloop_text(f.get("name", ""))
#         f["explanation"] = deloop_text(f.get("explanation", ""))
#         f["current_value"] = deloop_text(str(f.get("current_value", "")))
#         try:
#             f["todays_impact_percent"] = max(0, round(float(f.get("todays_impact_percent", 0))))
#         except (TypeError, ValueError):
#             f["todays_impact_percent"] = 0
#         if f["todays_impact_percent"] >= 3:   # keep the "no trivial factors" rule
#             clean_factors.append(f)

#     # Headline numbers come from PHYSICS, not the LLM — single source of truth.
#     a["factors"] = clean_factors
#     a["todays_efficiency_score"] = physics["efficiency_score"]
#     a["todays_estimated_loss_percent"] = round(physics["controllable_loss_pct"])
#     a["recoverable_today_pct"] = physics["recoverable_today_pct"]
#     a["irradiance_availability_pct"] = physics["irradiance_availability_pct"]
#     a["cloud_loss_pct"] = physics["cloud_loss_pct"]

#     # Re-derive severity from impact so labels never contradict numbers.
#     for f in clean_factors:
#         imp = f["todays_impact_percent"]
#         f["severity"] = "high" if imp > 12 else "medium" if imp >= 6 else "low"

#     if not clean_factors:
#         issues.append("Analyst returned no usable factors; synthesized them from the physics engine.")
#         clean_factors = _factors_from_physics(physics)
#         a["factors"] = clean_factors

#     return a, issues


# def _factors_from_physics(physics: dict[str, Any]) -> list[dict]:
#     """Build minimal controllable factors directly from the physics engine."""
#     out = []
#     heat = physics.get("heat_loss_pct", 0)
#     soil = physics.get("soiling_loss_pct", 0)
#     if heat >= 3:
#         out.append({
#             "name": "High-temperature cell loss",
#             "severity": "high" if heat > 12 else "medium" if heat >= 6 else "low",
#             "current_value": f"cell {physics.get('cell_temp_c')}°C",
#             "todays_impact_percent": round(heat),
#             "explanation": "Cell temperature above 25°C STC reduces output at ~0.4%/°C.",
#         })
#     if soil >= 3:
#         out.append({
#             "name": "Dust soiling",
#             "severity": "high" if soil > 12 else "medium" if soil >= 6 else "low",
#             "current_value": f"PM10 {physics['inputs_used'].get('pm10_ug_m3')} µg/m³",
#             "todays_impact_percent": round(soil),
#             "explanation": "Airborne dust deposits on the glass and blocks light (low confidence).",
#         })
#     return out


# # ── Recommendation validation ──────────────────────────────────────────────────

# _GROUPS = [
#     ("do_now",       "estimated_extra_kwh_today", 365),
#     ("do_this_week", "estimated_extra_kwh_per_week", 52),
#     ("long_term",    "estimated_extra_kwh_per_year", 1),
# ]


# def validate_recommendations(recs: dict[str, Any], analysis: dict[str, Any],
#                              baseline_kwh: float) -> tuple[dict, list[str]]:
#     """
#     - De-loop and DEDUPE every action (kills the duplicate 'standoffs' row).
#     - Cap each item's gain at the loss of the factor it addresses.
#     - Cap short-term group gains at recoverable_today (cannot recover what isn't lost).
#     - Recompute kWh from the REAL baseline + gain (kills '2 kWh for 7% gain' nonsense).
#     - Strip actively harmful actions (water on hot panels at peak).
#     """
#     issues: list[str] = []
#     r = dict(recs or {})
#     baseline = float(baseline_kwh or 0)

#     # Map factor name -> its loss, to cap gains per factor.
#     factor_loss = {}
#     for f in (analysis.get("factors") or []):
#         factor_loss[_norm_action(f.get("name", ""))] = f.get("todays_impact_percent", 0)
#     # Cap at what is REALISTICALLY recoverable (physics), not total controllable loss.
#     recoverable_today = float(analysis.get("recoverable_today_pct",
#                                            analysis.get("todays_estimated_loss_percent", 0)))

#     seen_actions: set[str] = set()

#     for group, kwh_field, periods_per_year in _GROUPS:
#         items = r.get(group) or []
#         if not isinstance(items, list):
#             items = []
#         clean: list[dict] = []
#         for it in items:
#             if not isinstance(it, dict):
#                 continue
#             it = dict(it)
#             action = deloop_text(it.get("action", ""))
#             if not action:
#                 continue

#             # Drop duplicate rows (normalized first-12-words key).
#             key = _norm_action(action)
#             if key in seen_actions:
#                 issues.append(f"Removed duplicate action in {group}: '{action[:40]}...'")
#                 continue
#             seen_actions.add(key)

#             # Strip actively harmful advice.
#             if _is_unsafe_action(action):
#                 issues.append(f"Removed unsafe action (water on hot panels) in {group}: '{action[:40]}...'")
#                 continue

#             # Strip wrong / counterproductive advice (tilting fixed arrays, Rain-X, etc).
#             bad = _is_low_quality_action(action)
#             if bad:
#                 issues.append(f"Removed low-quality action in {group} — {bad}: '{action[:40]}...'")
#                 continue

#             it["action"] = action
#             it["why_now"] = deloop_text(it.get("why_now", ""))
#             it["payback_notes"] = deloop_text(it.get("payback_notes", ""))

#             # Normalize confidence; marginal actions carry NO fabricated number.
#             conf = str(it.get("confidence", "medium")).lower()
#             if conf not in ("high", "medium", "marginal"):
#                 conf = "medium"
#             it["confidence"] = conf

#             # Cap gain to the addressed factor's loss (can't recover more than exists).
#             try:
#                 gain = max(0.0, float(it.get("estimated_gain_percent", 0)))
#             except (TypeError, ValueError):
#                 gain = 0.0
#             cap = factor_loss.get(_norm_action(it.get("addresses_factor", "")))
#             if group != "long_term" and cap is not None and gain > cap:
#                 issues.append(f"Capped gain {gain}%→{cap}% (factor only loses {cap}%) in {group}.")
#                 gain = float(cap)

#             if conf == "marginal" or gain == 0:
#                 # Honest: no invented percentage, no kWh — shown as "marginal".
#                 it["confidence"] = "marginal"
#                 it["estimated_gain_percent"] = 0
#                 it[kwh_field] = None
#             else:
#                 it["estimated_gain_percent"] = round(gain, 1)
#                 # Recompute kWh from REAL baseline so % and kWh always agree.
#                 annual_kwh = gain / 100.0 * baseline
#                 it[kwh_field] = round(annual_kwh / periods_per_year, 1)

#             clean.append(it)

#         # Cap short-term cumulative gains at what's physically recoverable today.
#         if group in ("do_now", "do_this_week"):
#             _scale_group_to_cap(clean, recoverable_today, issues, group)

#         r[group] = clean

#     # Recompute the headline recoverable number from the (capped) actions.
#     total_now = sum(i.get("estimated_gain_percent", 0) for i in r.get("do_now", []))
#     total_week = sum(i.get("estimated_gain_percent", 0) for i in r.get("do_this_week", []))
#     r["todays_recoverable_percent"] = round(min(total_now + total_week, recoverable_today), 1)
#     r["annual_baseline_kwh"] = round(baseline)

#     return r, issues


# def _scale_group_to_cap(items: list[dict], cap: float, issues: list[str], group: str) -> None:
#     """If summed gains exceed the recoverable cap, proportionally scale them down."""
#     total = sum(i.get("estimated_gain_percent", 0) for i in items)
#     if cap > 0 and total > cap and total > 0:
#         factor = cap / total
#         for i in items:
#             i["estimated_gain_percent"] = round(i.get("estimated_gain_percent", 0) * factor, 1)
#         issues.append(f"Scaled {group} gains down to fit {cap}% recoverable cap.")





























"""
validator.py
------------
Repairs and bounds LLM output BEFORE it reaches the user. The LLM (DeepSeek) is
good at narrative and bad at (a) not looping, (b) not exceeding physical limits,
(c) keeping kWh consistent with %. This module fixes all three deterministically,
so a model hiccup degrades gracefully instead of shipping the "nanocoating (e.g.,
nanocoating (e.g.," / "consistently. consistently." garbage seen in production.

Two entry points:
  - validate_analysis(analysis, physics)  -> (clean_analysis, issues)
  - validate_recommendations(recs, analysis, baseline_kwh) -> (clean_recs, issues)
"""

from __future__ import annotations

import re
from typing import Any

# Active BAD advice: misting/spraying water on panels TO COOL them during heat.
# The discriminator is COOLING INTENT — a dawn dust-clean never says "cool the panels",
# only thermal-shock-risky midday misting does. We also exclude dawn/evening phrasing.
_WET = re.compile(r"\b(spray|mist|splash|pour|hose|sprinkle)\b", re.I)
_PANEL = re.compile(r"\b(panel|module|glass|cell|surface)s?\b", re.I)
_COOL_INTENT = re.compile(r"\bcool(?:ing|s|ed)?\b|\blower\b.{0,30}\btemperature\b", re.I)
_SAFE_TIME = re.compile(r"\b(before|ahead of|dawn|sunrise|early morning|after sunset|evening|night)\b", re.I)


def _is_unsafe_action(text: str) -> bool:
    """True for 'wet the panels to cool them during heat' — thermal-shock risk."""
    if not (_WET.search(text) and _PANEL.search(text) and _COOL_INTENT.search(text)):
        return False
    # Dawn / evening misting is fine — only flag if NOT explicitly timed to a safe window.
    return not _SAFE_TIME.search(text)


# Wrong / counterproductive advice the model sometimes pads with on thin days.
_TILT_FIXED = re.compile(r"\b(tilt|re-?angle|reposition|re-?orient|adjust the angle)\b.{0,40}"
                         r"\b(array|panel|module)s?\b.{0,40}\b(wind|convection|cool)", re.I)
_BAD_COATING = re.compile(r"\brain-?x\b|\b(hydrophobic|consumer)\b.{0,30}\b(coating|spray)\b", re.I)


def _is_low_quality_action(text: str) -> str | None:
    """Return a reason string if the action is wrong/counterproductive, else None."""
    if _TILT_FIXED.search(text):
        return "tilting a fixed array to 'catch wind' (fixed arrays don't move; loses more than it gains)"
    if _BAD_COATING.search(text):
        return "consumer hydrophobic coating as anti-soiling (can cement dust in arid air)"
    return None


# The reflex filler the model defaults to on heat-dominated sites: "clear debris/
# clutter from under the panels for airflow." Detected as two independent signals so
# word distance doesn't matter. Fine if it carries a real gain; useless as marginal
# filler that appears at every location.
_FILLER_CLEAR = re.compile(
    r"\b(clear|remove|clean up|free up|clearing|removing)\b.{0,70}"
    r"\b(debris|clutter|leaves|objects?|items?|materials?|build-?up|nests?|obstructions?)\b", re.I)
_FILLER_AIRFLOW = re.compile(
    r"\b(airflow|air ?gap|air ?flow|ventilation|convection|cooling|"
    r"(under|underneath|beneath|behind|around)\b.{0,25}\b(panel|module|array))\b", re.I)


def _is_airflow_filler(text: str) -> bool:
    return bool(_FILLER_CLEAR.search(text) and _FILLER_AIRFLOW.search(text))


# ── Text de-looping ───────────────────────────────────────────────────────────

def deloop_text(text: str) -> str:
    """
    Collapse the runaway repetition DeepSeek sometimes emits:
      - repeated sentences ("consistently. consistently. consistently.")
      - repeated word-blocks ("strong local winds. strong local winds.")
      - interleaved clause fragments ("nanocoating (e.g., nanocoating (e.g.,")
    Keeps the FIRST occurrence, preserves order, never invents text. As a last
    resort, truncates output that is still pathologically repetitive so the user
    never sees a wall of loop.
    """
    if not text or not isinstance(text, str):
        return text or ""

    # 1) Sentence-level dedup (keep first occurrence of each normalized sentence).
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    seen: set[str] = set()
    kept: list[str] = []
    for s in sentences:
        norm = re.sub(r"\s+", " ", s.strip().lower())
        if norm and norm not in seen:
            seen.add(norm)
            kept.append(s.strip())
    out = " ".join(kept)

    # 2) Collapse consecutive repeated word-blocks (size 8 → 1), longest first.
    #    Iterate to a fixpoint so boundary-adjacent repeats also collapse.
    for _ in range(6):
        collapsed = _collapse_word_repeats(out)
        if collapsed == out:
            break
        out = collapsed

    # 3) Collapse repeated single words ("dust dust dust").
    out = re.sub(r"\b(\w+)(\s+\1\b){1,}", r"\1", out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out).strip()

    # 4) Safety net: if it is STILL highly repetitive (low unique-word ratio and
    #    long), keep only the first two sentences — never ship a loop wall.
    words = out.split()
    if len(words) > 40 and len(set(w.lower() for w in words)) / len(words) < 0.5:
        first = re.split(r"(?<=[.!?])\s+", out)[:2]
        out = " ".join(first).strip()

    return out


def _collapse_word_repeats(text: str) -> str:
    """Emit one copy of any immediately-repeated word-block, skipping all copies."""
    words = text.split()
    out: list[str] = []
    i, n = 0, len(words)
    while i < n:
        matched = False
        for size in range(min(8, (n - i) // 2), 0, -1):
            block = words[i:i + size]
            if block == words[i + size:i + 2 * size]:
                out.extend(block)
                j = i + size
                while words[j:j + size] == block:   # skip every consecutive copy
                    j += size
                i = j
                matched = True
                break
        if not matched:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _norm_action(text: str) -> str:
    """Normalized key for detecting duplicate recommendation rows."""
    t = deloop_text(text).lower()
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return " ".join(t.split()[:12])  # first 12 words is enough to catch dupes


# ── Analysis validation ───────────────────────────────────────────────────────

def validate_analysis(analysis: dict[str, Any], physics: dict[str, Any]) -> tuple[dict, list[str]]:
    """
    Clamp the LLM's factor numbers to the physics engine and recompute the
    headline so the displayed score is always internally consistent.
    """
    issues: list[str] = []
    a = dict(analysis or {})

    factors = a.get("factors") or []
    if not isinstance(factors, list):
        factors = []

    # De-loop every text field and clamp impacts to a sane range.
    clean_factors = []
    for f in factors:
        if not isinstance(f, dict):
            continue
        f = dict(f)
        f["name"] = deloop_text(f.get("name", ""))
        f["explanation"] = deloop_text(f.get("explanation", ""))
        f["current_value"] = deloop_text(str(f.get("current_value", "")))
        try:
            f["todays_impact_percent"] = max(0, round(float(f.get("todays_impact_percent", 0))))
        except (TypeError, ValueError):
            f["todays_impact_percent"] = 0
        if f["todays_impact_percent"] >= 3:   # keep the "no trivial factors" rule
            clean_factors.append(f)

    # Headline numbers come from PHYSICS, not the LLM — single source of truth.
    a["factors"] = clean_factors
    a["todays_efficiency_score"] = physics["efficiency_score"]
    a["todays_estimated_loss_percent"] = round(physics["controllable_loss_pct"])
    a["recoverable_today_pct"] = physics["recoverable_today_pct"]
    a["irradiance_availability_pct"] = physics["irradiance_availability_pct"]
    a["cloud_loss_pct"] = physics["cloud_loss_pct"]

    # Re-derive severity from impact so labels never contradict numbers.
    for f in clean_factors:
        imp = f["todays_impact_percent"]
        f["severity"] = "high" if imp > 12 else "medium" if imp >= 6 else "low"

    if not clean_factors:
        issues.append("Analyst returned no usable factors; synthesized them from the physics engine.")
        clean_factors = _factors_from_physics(physics)
        a["factors"] = clean_factors

    return a, issues


def _factors_from_physics(physics: dict[str, Any]) -> list[dict]:
    """Build minimal controllable factors directly from the physics engine."""
    out = []
    heat = physics.get("heat_loss_pct", 0)
    soil = physics.get("soiling_loss_pct", 0)
    if heat >= 3:
        out.append({
            "name": "High-temperature cell loss",
            "severity": "high" if heat > 12 else "medium" if heat >= 6 else "low",
            "current_value": f"cell {physics.get('cell_temp_c')}°C",
            "todays_impact_percent": round(heat),
            "explanation": "Cell temperature above 25°C STC reduces output at ~0.4%/°C.",
        })
    if soil >= 3:
        out.append({
            "name": "Dust soiling",
            "severity": "high" if soil > 12 else "medium" if soil >= 6 else "low",
            "current_value": f"PM10 {physics['inputs_used'].get('pm10_ug_m3')} µg/m³",
            "todays_impact_percent": round(soil),
            "explanation": "Airborne dust deposits on the glass and blocks light (low confidence).",
        })
    return out


# ── Recommendation validation ──────────────────────────────────────────────────

_GROUPS = [
    ("do_now",       "estimated_extra_kwh_today", 365),
    ("do_this_week", "estimated_extra_kwh_per_week", 52),
    ("long_term",    "estimated_extra_kwh_per_year", 1),
]


def validate_recommendations(recs: dict[str, Any], analysis: dict[str, Any],
                             baseline_kwh: float) -> tuple[dict, list[str]]:
    """
    - De-loop and DEDUPE every action (kills the duplicate 'standoffs' row).
    - Cap each item's gain at the loss of the factor it addresses.
    - Cap short-term group gains at recoverable_today (cannot recover what isn't lost).
    - Recompute kWh from the REAL baseline + gain (kills '2 kWh for 7% gain' nonsense).
    - Strip actively harmful actions (water on hot panels at peak).
    """
    issues: list[str] = []
    r = dict(recs or {})
    baseline = float(baseline_kwh or 0)

    # Map factor name -> its loss, to cap gains per factor.
    factor_loss = {}
    for f in (analysis.get("factors") or []):
        factor_loss[_norm_action(f.get("name", ""))] = f.get("todays_impact_percent", 0)
    # Cap at what is REALISTICALLY recoverable (physics), not total controllable loss.
    recoverable_today = float(analysis.get("recoverable_today_pct",
                                           analysis.get("todays_estimated_loss_percent", 0)))

    seen_actions: set[str] = set()

    for group, kwh_field, periods_per_year in _GROUPS:
        items = r.get(group) or []
        if not isinstance(items, list):
            items = []
        clean: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            it = dict(it)
            action = deloop_text(it.get("action", ""))
            if not action:
                continue

            # Drop duplicate rows (normalized first-12-words key).
            key = _norm_action(action)
            if key in seen_actions:
                issues.append(f"Removed duplicate action in {group}: '{action[:40]}...'")
                continue
            seen_actions.add(key)

            # Strip actively harmful advice.
            if _is_unsafe_action(action):
                issues.append(f"Removed unsafe action (water on hot panels) in {group}: '{action[:40]}...'")
                continue

            # Strip wrong / counterproductive advice (tilting fixed arrays, Rain-X, etc).
            bad = _is_low_quality_action(action)
            if bad:
                issues.append(f"Removed low-quality action in {group} — {bad}: '{action[:40]}...'")
                continue

            it["action"] = action
            it["why_now"] = deloop_text(it.get("why_now", ""))
            it["payback_notes"] = deloop_text(it.get("payback_notes", ""))

            # Normalize confidence; marginal actions carry NO fabricated number.
            conf = str(it.get("confidence", "medium")).lower()
            if conf not in ("high", "medium", "marginal"):
                conf = "medium"
            it["confidence"] = conf

            # Cap gain to the addressed factor's loss (can't recover more than exists).
            try:
                gain = max(0.0, float(it.get("estimated_gain_percent", 0)))
            except (TypeError, ValueError):
                gain = 0.0
            cap = factor_loss.get(_norm_action(it.get("addresses_factor", "")))
            if group != "long_term" and cap is not None and gain > cap:
                issues.append(f"Capped gain {gain}%→{cap}% (factor only loses {cap}%) in {group}.")
                gain = float(cap)

            if conf == "marginal" or gain == 0:
                # Honest: no invented percentage, no kWh — shown as "marginal".
                # Also drop the generic "clear debris under panels for airflow" filler
                # from the action-today groups — it's the reflex that appears everywhere.
                if group in ("do_now", "do_this_week") and _is_airflow_filler(action):
                    issues.append(f"Dropped generic airflow filler in {group}: '{action[:40]}...'")
                    continue
                it["confidence"] = "marginal"
                it["estimated_gain_percent"] = 0
                it[kwh_field] = None
            else:
                it["estimated_gain_percent"] = round(gain, 1)
                # Recompute kWh from REAL baseline so % and kWh always agree.
                annual_kwh = gain / 100.0 * baseline
                it[kwh_field] = round(annual_kwh / periods_per_year, 1)

            clean.append(it)

        # Cap short-term cumulative gains at what's physically recoverable today.
        if group in ("do_now", "do_this_week"):
            _scale_group_to_cap(clean, recoverable_today, issues, group)

        # Lead with the biggest real win; marginal (gain 0) actions sink to the bottom.
        clean.sort(key=lambda it: it.get("estimated_gain_percent", 0) or 0, reverse=True)

        r[group] = clean

    # Recompute the headline recoverable number from the (capped) actions.
    total_now = sum(i.get("estimated_gain_percent", 0) for i in r.get("do_now", []))
    total_week = sum(i.get("estimated_gain_percent", 0) for i in r.get("do_this_week", []))
    r["todays_recoverable_percent"] = round(min(total_now + total_week, recoverable_today), 1)
    r["annual_baseline_kwh"] = round(baseline)

    return r, issues


def _scale_group_to_cap(items: list[dict], cap: float, issues: list[str], group: str) -> None:
    """If summed gains exceed the recoverable cap, proportionally scale them down."""
    total = sum(i.get("estimated_gain_percent", 0) for i in items)
    if cap > 0 and total > cap and total > 0:
        factor = cap / total
        for i in items:
            i["estimated_gain_percent"] = round(i.get("estimated_gain_percent", 0) * factor, 1)
        issues.append(f"Scaled {group} gains down to fit {cap}% recoverable cap.")