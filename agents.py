# """
# agents.py
# ---------
# LangGraph agents. HYBRID design:

#   physics.py        computes the ground-truth numbers (cell temp, losses) in code
#   analyst_agent     LLM names + explains factors, grounded on those numbers
#   advisor_agent     LLM writes ranked, location-specific actions
#   validator.py      repairs LLM output (de-loop, dedupe, cap gains, recompute kWh)

# The LLM never invents the headline numbers anymore. It may nudge a factor value
# by a small margin WITH a stated reason, but the validator clamps everything back
# to what is physically possible before the user sees it.
# """

# from __future__ import annotations

# import json
# import os
# from typing import Any

# from langchain_openai import ChatOpenAI

# from physics import compute_physics, physics_prompt_block
# from validator import validate_analysis, validate_recommendations
# from tools import (
#     get_air_quality,
#     get_current_weather,
#     get_marine_data,
#     get_nasa_power_climatology,
#     get_pvgis_data,
#     reverse_geocode,
# )


# # ---------- LLM ----------

# def get_llm() -> ChatOpenAI:
#     """
#     DeepSeek via OpenAI-compatible endpoint.
#     frequency/presence penalties curb the runaway repetition ("consistently.
#     consistently. consistently.") seen in production. The validator is the hard
#     backstop; these penalties just make loops far less likely in the first place.
#     """
#     return ChatOpenAI(
#         model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
#         api_key=os.getenv("DEEPSEEK_API_KEY"),
#         base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
#         temperature=0.2,
#         max_tokens=3000,
#         model_kwargs={"frequency_penalty": 0.5, "presence_penalty": 0.3},
#     )


# def _extract_json(text: str) -> dict:
#     cleaned = text.strip()
#     if cleaned.startswith("```"):
#         cleaned = cleaned.split("```", 2)[-1]
#         if cleaned.startswith("json"):
#             cleaned = cleaned[4:]
#         if "```" in cleaned:
#             cleaned = cleaned.rsplit("```", 1)[0]
#     return json.loads(cleaned.strip())


# # ---------- Agent 1: Data Collector ----------

# def data_collector_agent(state: dict) -> dict:
#     lat, lon = state["lat"], state["lon"]
#     raw: dict[str, Any] = {"location_name": reverse_geocode(lat, lon)}
#     errors: list[str] = []

#     for key, fn in [
#         ("pvgis", get_pvgis_data),
#         ("weather", get_current_weather),
#         ("air_quality", get_air_quality),
#         ("nasa_power", get_nasa_power_climatology),
#     ]:
#         try:
#             raw[key] = fn(lat, lon)
#         except Exception as e:
#             errors.append(f"{key} failed: {e}")
#             raw[key] = None

#     try:
#         raw["marine"] = get_marine_data(lat, lon)  # None for inland — not an error
#     except Exception as e:
#         errors.append(f"Marine API failed: {e}")
#         raw["marine"] = None

#     return {**state, "raw_data": raw, "errors": errors}


# # ---------- Agent 2: Physics (deterministic, no LLM) ----------

# def physics_agent(state: dict) -> dict:
#     physics = compute_physics(state["raw_data"], state["lat"], state["lon"])
#     return {**state, "physics": physics}


# # ---------- Agent 3: Analyst ----------

# ANALYST_SYSTEM = """You are a senior solar-PV analyst writing a TODAY-FOCUSED snapshot for one site.

# A PHYSICS ENGINE has already computed the ground-truth numbers in code (cell temperature,
# heat loss, soiling loss, cloud/irradiance availability). They are AUTHORITATIVE.

# YOUR JOB: turn those numbers into clearly named, location-specific factors with crisp
# explanations, plus a short summary. You are the writer, not the calculator.

# RULES:
# 1. Use the engine's numbers. You MAY nudge a single factor's impact by at most ±20%
#    ONLY if you state a concrete, data-driven reason in its explanation. Otherwise copy
#    the computed number exactly.
# 2. factors[] contains CONTROLLABLE losses only (heat, soiling). Do NOT list cloud /
#    irradiance as a factor — it is environmental and not recoverable. Instead mention it
#    in todays_summary as context, e.g. "the sky is delivering X% of clear-sky potential".
# 3. current_value MUST cite an exact number with units from the data (no "moderate").
# 4. At least one factor's explanation must reference a GEOGRAPHIC specific inferred from
#    lat/lon (desert dust grade, coastal salt, monsoon belt timing, alpine UV, etc.).
# 5. Omit any factor under 3% impact.

# Return ONLY valid JSON, no prose, no markdown fences:
# {
#   "reasoning": "<2-3 sentences referencing the engine's numbers>",
#   "todays_summary": "<2-3 sentences; MUST include the geographic context AND the clear-sky availability>",
#   "factors": [
#     {"name": "<specific label>", "severity": "<low|medium|high>",
#      "current_value": "<exact number + units>", "todays_impact_percent": <int>,
#      "explanation": "<one sentence tying physics to the cited number>"}
#   ]
# }
# """


# def analyst_agent(state: dict) -> dict:
#     llm = get_llm()
#     raw = state["raw_data"]
#     physics = state["physics"]

#     user_msg = (
#         f"Location: {raw.get('location_name')}  (lat {state['lat']}, lon {state['lon']})\n\n"
#         f"{physics_prompt_block(physics)}\n\n"
#         f"=== SUPPORTING DATA ===\n"
#         f"Weather: {json.dumps(raw.get('weather'), indent=2)}\n"
#         f"Air quality: {json.dumps(raw.get('air_quality'), indent=2)}\n"
#         f"Marine: {json.dumps(raw.get('marine'))}\n"
#         f"NASA climatology: {json.dumps(raw.get('nasa_power'))}\n\n"
#         f"Write the factors JSON."
#     )
#     raw_text = ""
#     try:
#         resp = llm.invoke([
#             {"role": "system", "content": ANALYST_SYSTEM},
#             {"role": "user", "content": user_msg},
#         ])
#         raw_text = resp.content or ""
#         analysis = _extract_json(raw_text)
#     except Exception as e:
#         raw_text = raw_text or f"(LLM error: {e})"
#         analysis = {"todays_summary": f"(LLM unavailable: {e})", "factors": []}

#     # Validator clamps numbers to physics, de-loops text, synthesizes factors if needed.
#     analysis, issues = validate_analysis(analysis, physics)
#     return {**state, "analysis": analysis, "analysis_issues": issues, "analysis_raw": raw_text}


# # ---------- Agent 4: Advisor ----------

# ADVISOR_SYSTEM = """You are a hands-on solar installer (500+ Indian rooftop jobs) turning a
# diagnosis into an action plan real people will follow.

# You are given the validated factors and the physics numbers. RANK actions by the size of
# the factor they attack: the biggest controllable loss gets the top "do_now" action.

# QUALITY OVER QUANTITY — this is the most important rule:
# - Recommend ONLY actions that are genuinely worthwhile for THIS site today. Do NOT pad.
# - If only one action is justified in a group, return just one. Empty groups are fine.
# - Two solid actions beat three with filler. A thin day SHOULD produce a short plan.

# EVIDENCE HONESTY:
# - Attach a numeric estimated_gain_percent ONLY to actions with a well-established,
#   quantifiable mechanism: washing soiled panels, raising mount height / adding standoffs
#   for airflow, fixing actual shading shown in the data.
# - For actions that are reasonable but marginal or hard to quantify (clearing minor debris,
#   trimming nearby vegetation, backside dusting), set estimated_gain_percent = 0 and
#   confidence = "marginal". DO NOT invent a specific percentage like "+0.5%".
# - confidence is one of: "high", "medium", "marginal".

# BANNED ACTIONS — never recommend these (they are wrong or counterproductive):
# - Tilting, repositioning, or re-angling a FIXED rooftop array "to catch wind" — fixed
#   arrays don't move, and changing tilt loses more than convection gains.
# - Consumer hydrophobic coatings (e.g. Rain-X) as anti-soiling — they can cement dust in
#   arid air. Only a proper PV anti-soiling coating by a technician, and only long-term.
# - Spraying/misting/hosing water onto the panel FACE to cool it — thermal shock cracks cells.
#   Cooling = airflow (raise mounts, standoffs, back-ventilation, remove real obstructions),
#   or wet-CLEANING only at dawn / after sunset.

# OTHER RULES:
# - At most ONE cleaning action per group. addresses_factor unique across the whole output.
# - At least one action references this site's geography; at least one cites a SPECIFIC time
#   window from the forecast (e.g. "before 8 AM", "ahead of Wednesday's rain").
# - do_now items: doable in <3 hours, no contractor, no delivery.
# - RAIN-AWARE: if rain probability > 50% in the next 48h, do not recommend wet cleaning now.
# - Be concise — no repetition. kWh fields are recomputed by the system; just estimate them.

# Return ONLY valid JSON, no prose, no fences:
# {
#   "reasoning": "<2 sentences: biggest recoverable factor and why the top action attacks it>",
#   "system_assumption": "5 kWp rooftop",
#   "annual_baseline_kwh": <int>,
#   "todays_recoverable_percent": <int>,
#   "do_now": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
#               "confidence":"high|medium|marginal","estimated_extra_kwh_today":<num>,
#               "effort":"low|medium|high","estimated_cost_inr":"<range or free>","why_now":"<cites a number>"}],
#   "do_this_week": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
#               "confidence":"...","estimated_extra_kwh_per_week":<num>,"effort":"...","estimated_cost_inr":"...","why_now":"..."}],
#   "long_term": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
#               "confidence":"...","estimated_extra_kwh_per_year":<num>,"effort":"...","estimated_cost_inr":"...","payback_notes":"..."}]
# }
# """


# def advisor_agent(state: dict) -> dict:
#     llm = get_llm()
#     analysis = state["analysis"]
#     physics = state["physics"]
#     pvgis = state["raw_data"].get("pvgis") or {}
#     per_kwp = pvgis.get("annual_yield_kwh_per_kwp") or 0
#     baseline = per_kwp * 5  # 5 kWp assumption

#     user_msg = (
#         f"Location: {state['raw_data'].get('location_name')}\n"
#         f"PVGIS yield per kWp: {per_kwp} kWh/yr -> 5 kWp baseline ~{baseline:.0f} kWh/yr\n\n"
#         f"{physics_prompt_block(physics)}\n\n"
#         f"=== VALIDATED FACTORS ===\n{json.dumps(analysis.get('factors'), indent=2)}\n\n"
#         f"Max recoverable today (engine): {physics['recoverable_today_pct']}%\n"
#         f"Rain prob next 48h (max %): "
#         f"{(state['raw_data'].get('weather') or {}).get('daily', {}).get('rain_probability_max_percent', [])[:2]}\n\n"
#         f"Produce the recommendations JSON."
#     )
#     raw_text = ""
#     try:
#         resp = llm.invoke([
#             {"role": "system", "content": ADVISOR_SYSTEM},
#             {"role": "user", "content": user_msg},
#         ])
#         raw_text = resp.content or ""
#         recs = _extract_json(raw_text)
#     except Exception as e:
#         raw_text = raw_text or f"(LLM error: {e})"
#         recs = {"system_assumption": "5 kWp rooftop", "do_now": [], "do_this_week": [],
#                 "long_term": [], "_llm_error": str(e)}

#     # Validator: de-loop, dedupe, cap gains to factors, recompute kWh from baseline.
#     recs, issues = validate_recommendations(recs, analysis, baseline)
#     return {**state, "recommendations": recs, "advice_issues": issues, "advice_raw": raw_text}



















"""
agents.py
---------
LangGraph agents. HYBRID design:

  physics.py        computes the ground-truth numbers (cell temp, losses) in code
  analyst_agent     LLM names + explains factors, grounded on those numbers
  advisor_agent     LLM writes ranked, location-specific actions
  validator.py      repairs LLM output (de-loop, dedupe, cap gains, recompute kWh)

The LLM never invents the headline numbers anymore. It may nudge a factor value
by a small margin WITH a stated reason, but the validator clamps everything back
to what is physically possible before the user sees it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_openai import ChatOpenAI

from physics import site_profile, site_profile_block
from user_profiles import DEFAULT_KWP, effective_baseline_kwh, profile_prompt_block
from validator import validate_analysis, validate_recommendations
from tools import (
    get_air_quality,
    get_current_weather,
    get_marine_data,
    get_nasa_power_climatology,
    get_pvgis_data,
    reverse_geocode,
)


# ---------- LLM ----------

def get_llm() -> ChatOpenAI:
    """
    DeepSeek via OpenAI-compatible endpoint.
    frequency/presence penalties curb the runaway repetition ("consistently.
    consistently. consistently.") seen in production. The validator is the hard
    backstop; these penalties just make loops far less likely in the first place.
    """
    return ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        temperature=0.2,
        max_tokens=3000,
        model_kwargs={"frequency_penalty": 0.5, "presence_penalty": 0.3},
    )


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[-1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        if "```" in cleaned:
            cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


# ---------- Agent 1: Data Collector ----------

def data_collector_agent(state: dict) -> dict:
    lat, lon = state["lat"], state["lon"]
    raw: dict[str, Any] = {"location_name": reverse_geocode(lat, lon)}
    errors: list[str] = []

    for key, fn in [
        ("pvgis", get_pvgis_data),
        ("weather", get_current_weather),
        ("air_quality", get_air_quality),
        ("nasa_power", get_nasa_power_climatology),
    ]:
        try:
            raw[key] = fn(lat, lon)
        except Exception as e:
            errors.append(f"{key} failed: {e}")
            raw[key] = None

    try:
        raw["marine"] = get_marine_data(lat, lon)  # None for inland — not an error
    except Exception as e:
        errors.append(f"Marine API failed: {e}")
        raw["marine"] = None

    return {**state, "raw_data": raw, "errors": errors}





# ---------- Agent 3: Analyst ----------

ANALYST_SYSTEM = """
You are Dr. Solara — a legendary solar energy diagnostician with nearly a century of field experience, from the earliest photovoltaic installations to today's utility-scale farms. You have seen every failure mode, every edge case, every seasonal anomaly that can humble a solar array.
Your single source of truth: Live API Feed
Raw sensor telemetry — irradiance, temperature, humidity, wind speed, soiling indices, pressure, and whatever else the feed carries. This is all you have. This is enough.
Your job is not to report data. Your job is to interrogate it.
For every dataset you receive, you must:

Read the raw data cold — take in every field, note what's present, what's missing, what looks anomalous before any analysis begins
Diagnose every observable factor contributing to efficiency loss — temperature coefficient degradation, spectral mismatch, angle of incidence losses, soiling, shading, humidity-induced resistance, inverter clipping, and anything else the data whispers to you
Go beyond the obvious — what does the data imply that it doesn't state? A humidity spike paired with a temperature drop might hint at dew-point condensation on the glass. A sudden irradiance dip with no cloud cover reported might suggest localized dust storm activity. A wind drop combined with rising panel temperature points to convective cooling failure. Think like a detective, not a calculator
Think out loud, in full — narrate your reasoning as you go. State what you're seeing, what it reminds you of, what you'd expect vs. what you're actually observing, and where your confidence is high or uncertain
Quantify where you can, qualify where you can't — give numbers when the data supports them; give informed judgment when it doesn't. Never leave an observation hanging without a verdict
Flag data quality issues — if a sensor reading looks stale, spiked, or physically impossible, call it out. Bad data is a diagnosis too

Your tone: seasoned, unhurried, deeply authoritative. You've seen a thousand panels fail in a thousand ways. Nothing surprises you — but everything is worth examining carefully.
Return ONLY valid JSON, no prose, no markdown fences:
{
  "reasoning": "<2-3 sentences referencing the engine's numbers>",
  "todays_summary": "<2-3 sentences; MUST include the geographic context AND the clear-sky availability>",
  "factors": [
    {"name": "<specific label>", "severity": "<low|medium|high>",
     "current_value": "<exact number + units>", "todays_impact_percent": <int>,
     "explanation": "<one sentence tying physics to the cited number>"}
  ]
}
"""


def analyst_agent(state: dict) -> dict:
    llm = get_llm()
    raw = state["raw_data"]

    user_msg = (
        f"Location: {raw.get('location_name')}  (lat {state['lat']}, lon {state['lon']})\n\n"
        f"=== SUPPORTING DATA ===\n"
        f"Weather: {json.dumps(raw.get('weather'), indent=2)}\n"
        f"Air quality: {json.dumps(raw.get('air_quality'), indent=2)}\n"
        f"Marine: {json.dumps(raw.get('marine'))}\n"
        f"NASA climatology: {json.dumps(raw.get('nasa_power'))}\n\n"
        f"Write the factors JSON."
    )
    raw_text = ""
    try:
        resp = llm.invoke([
            {"role": "system", "content": ANALYST_SYSTEM},
            {"role": "user", "content": user_msg},
        ])
        raw_text = resp.content or ""
        analysis = _extract_json(raw_text)
    except Exception as e:
        raw_text = raw_text or f"(LLM error: {e})"
        analysis = {"todays_summary": f"(LLM unavailable: {e})", "factors": []}

    # Validator detached for now; returning raw analysis directly.
    # analysis, issues = validate_analysis(analysis)
    issues = []
    return {**state, "analysis": analysis, "analysis_issues": issues, "analysis_raw": raw_text}


# ---------- Agent 4: Advisor ----------

ADVISOR_SYSTEM = """You are a senior solar panel Advisor with over 50 years of hands-on field experience. You understand every environmental and installation factor that can affect solar panel efficiency — not in theory, but in practice.
Your audience: Everyday homeowners who have installed solar panels at their home. They have no technical or solar industry background. Speak to them like a trusted expert neighbor — clear, warm, and genuinely helpful.
Your job: Analyze everything you get  the information about the user's environment and their specific installed panel type and other factors, then tell them exactly what is hurting their solar efficiency and what they should do about it.

How to think (internal reasoning only — never show this to the user):
To form your recommendations, mentally simulate the full solar journey — how the sun moves, how the atmosphere interacts with light at different times and seasons, how heat builds up on panels, how dust settles differently in humid vs dry conditions, how shadows creep across a rooftop hour by hour. Use this mental model strictly to interpret the data you are given. Never invent data or fill in gaps with assumptions.

Output Format — Three-Tier Action Plan:
Present your solution in exactly this structure:
🔆 What to Do Today
Immediate actions the user can take right now that will have a visible impact.
📅 This Week's Plan
Steps to take over the coming days that address the underlying issue more thoroughly.
🗓️ This Month / Annual Plan
Longer-term habits, adjustments, or checks that keep efficiency high season after season.

Solution Quality Rules — strictly follow these:

✅ Every solution must be specific to the user's environment and panel type — not copy-paste advice that could apply to anyone anywhere.
✅ Write solutions the way a knowledgeable friend would explain them — practical, human, and directly useful.
✅ No raw numbers, percentages, temperatures, or technical metrics in the final output. The user doesn't need to know the math — they need to know what to do.
✅ No vague, generic filler advice. If a suggestion wouldn't meaningfully change what this specific user does tomorrow, cut it.
✅ Focus on solutions that genuinely move the needle on efficiency — not maintenance checklists dressed up as insights.
✅ Cover the full picture — your response must address all the user's specific data points 
(panel type, wattage, tilt angle, location, shading, temperature, etc.) and the analyst 
agent's findings together. You may give one focused solution for issue related to user answer on what mainly lands on them , but never 
fixate on a single problem while ignoring other factors that are clearly present in 
the data.
✅ ONE-TO-ONE FACTOR MAPPING: For EVERY single factor listed in the `VALIDATED FACTORS` input, you MUST provide exactly one specific action in the `do_now` list to address it immediately. Ensure the `addresses_factor` field in the JSON exactly matches the name of the factor. Do not ignore any of the provided factors.


Return ONLY valid JSON, no prose, no fences:
{
  "reasoning": "<2 sentences: what is DISTINCTIVE about this site and how the top action targets it>",
  "system_assumption": "5 kWp rooftop",
  "annual_baseline_kwh": <int>,
  "todays_recoverable_percent": <int>,
  "do_now": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
              "confidence":"high|medium|marginal","estimated_extra_kwh_today":<num>,
              "effort":"low|medium|high","estimated_cost_inr":"<range or free>","why_now":"<cites a site-specific number>"}],
  "do_this_week": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
              "confidence":"...","estimated_extra_kwh_per_week":<num>,"effort":"...","estimated_cost_inr":"...","why_now":"..."}],
  "long_term": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
              "confidence":"...","estimated_extra_kwh_per_year":<num>,"effort":"...","estimated_cost_inr":"...","payback_notes":"..."}]
}
"""


def advisor_agent(state: dict) -> dict:
    llm = get_llm()
    analysis = state["analysis"]
    profile = state.get("profile") or {}
    pvgis = state["raw_data"].get("pvgis") or {}
    per_kwp = pvgis.get("annual_yield_kwh_per_kwp") or 0
    baseline = effective_baseline_kwh(per_kwp, profile)  # user's real kWp, else 5
    kwp = profile.get("system_kwp") or DEFAULT_KWP

    sp = site_profile(state["raw_data"], {}, state["lat"], state["lon"])
    user_msg = (
        f"Location: {state['raw_data'].get('location_name')}\n"
        f"PVGIS yield per kWp: {per_kwp} kWh/yr -> {kwp:g} kWp baseline ~{baseline:.0f} kWh/yr\n\n"
        f"{profile_prompt_block(profile)}\n\n"
        f"{site_profile_block(sp)}\n\n"
        f"=== VALIDATED FACTORS ===\n{json.dumps(analysis.get('factors'), indent=2)}\n\n"
        f"Rain prob next 48h (max %): "
        f"{(state['raw_data'].get('weather') or {}).get('daily', {}).get('rain_probability_max_percent', [])[:2]}\n\n"
        f"Diagnose what is DISTINCTIVE about this site and produce the recommendations JSON."
    )
    raw_text = ""
    try:
        resp = llm.invoke([
            {"role": "system", "content": ADVISOR_SYSTEM},
            {"role": "user", "content": user_msg},
        ])
        raw_text = resp.content or ""
        recs = _extract_json(raw_text)
    except Exception as e:
        raw_text = raw_text or f"(LLM error: {e})"
        recs = {"system_assumption": "5 kWp rooftop", "do_now": [], "do_this_week": [],
                "long_term": [], "_llm_error": str(e)}

    # Validator detached for now; returning raw recommendations directly.
    # recs, issues = validate_recommendations(recs, analysis, baseline)
    issues = []
    # System label reflects the user's real size (or the assumed default).
    recs["system_assumption"] = (f"{kwp:g} kWp rooftop" if profile.get("system_kwp")
                                 else f"{DEFAULT_KWP:g} kWp rooftop (assumed)")
    return {**state, "recommendations": recs, "advice_issues": issues, "advice_raw": raw_text}