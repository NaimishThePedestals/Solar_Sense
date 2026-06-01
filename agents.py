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

from physics import compute_physics, physics_prompt_block, site_profile, site_profile_block
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


# ---------- Agent 2: Physics (deterministic, no LLM) ----------

def physics_agent(state: dict) -> dict:
    physics = compute_physics(state["raw_data"], state["lat"], state["lon"])
    return {**state, "physics": physics}


# ---------- Agent 3: Analyst ----------

ANALYST_SYSTEM = """You are a senior solar-PV analyst writing a TODAY-FOCUSED snapshot for one site.

A PHYSICS ENGINE has already computed the ground-truth numbers in code (cell temperature,
heat loss, soiling loss, cloud/irradiance availability). They are AUTHORITATIVE.

YOUR JOB: turn those numbers into clearly named, location-specific factors with crisp
explanations, plus a short summary. You are the writer, not the calculator.

RULES:
1. Use the engine's numbers. You MAY nudge a single factor's impact by at most ±20%
   ONLY if you state a concrete, data-driven reason in its explanation. Otherwise copy
   the computed number exactly.
2. factors[] contains CONTROLLABLE losses only (heat, soiling). Do NOT list cloud /
   irradiance as a factor — it is environmental and not recoverable. Instead mention it
   in todays_summary as context, e.g. "the sky is delivering X% of clear-sky potential".
3. current_value MUST cite an exact number with units from the data (no "moderate").
4. At least one factor's explanation must reference a GEOGRAPHIC specific inferred from
   lat/lon (desert dust grade, coastal salt, monsoon belt timing, alpine UV, etc.).
5. Omit any factor under 3% impact.

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
    physics = state["physics"]

    user_msg = (
        f"Location: {raw.get('location_name')}  (lat {state['lat']}, lon {state['lon']})\n\n"
        f"{physics_prompt_block(physics)}\n\n"
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

    # Validator clamps numbers to physics, de-loops text, synthesizes factors if needed.
    analysis, issues = validate_analysis(analysis, physics)
    return {**state, "analysis": analysis, "analysis_issues": issues, "analysis_raw": raw_text}


# ---------- Agent 4: Advisor ----------

ADVISOR_SYSTEM = """You are a master solar technician who has serviced rooftops across deserts,
coasts, cities, and mountains. You are writing an action plan for ONE specific site.

HOW TO THINK (this is what makes two locations get DIFFERENT plans):
Start from the SITE PROFILE. Your job is to diagnose what is UNUSUAL or DOMINANT about THIS
site and prescribe for it — not to emit a standard checklist. A coastal site, a desert site,
and a cool mountain site must produce visibly different plans because their profiles differ.
The #1 "do_now" action must attack this site's single most distinctive or largest issue.

SELF-CHECK before you write each action: "Would I give this same advice at a totally different
site?" If yes, it's probably generic filler — replace it with something this site's data
actually calls for, or drop it. Cite the site's real numbers (PM10 value, tilt angle, wind
direction, rain time) in why_now so the advice is unmistakably about THIS place.

THE DO-NOW REALITY (read this — it's why your plans look identical):
Heat has almost NO good same-day fix — you cannot cool the weather, and raising mounts is a
this-week job, not a do-now one. So do NOT pad "do_now" with "clear debris/clutter under the
panels for airflow" — that is a reflex filler that fits everywhere and helps almost nowhere.
Only put it in do_now if you have a SPECIFIC reason it applies here, with a real gain.
Legitimate do_now actions are essentially: CLEAN the glass (only if soiled), fresh-water RINSE
(coastal salt), or remove a REAL visible shading/obstruction. If none of those apply, do_now
should be SHORT or EMPTY — say plainly that there's little to recover today and the real wins
are this-week (standoffs/ventilation) and long-term. An empty do_now is a correct answer.
RANK by gain: the highest-percentage real action leads; never let a marginal action be #1.

MATCH THE LEVER TO THE SITE (examples, not a checklist — choose what fits, ignore the rest):
- Extreme/high dust (PM10): cleaning CADENCE is the lever (e.g. weekly/twice-weekly), not a
  one-off wipe; a water-fed pole or scheduled routine beats a single clean.
- Coastal/salt: fresh-water RINSE (not dry brushing) to clear salt film; mention it explicitly.
- Hot + low wind + high heat loss: airflow — raise mounts / standoffs / clear rear obstructions.
- Cool climate / low heat loss: do NOT prescribe cooling at all; focus elsewhere or keep it short.
- Tilt far from PVGIS optimal AND mount is adjustable: nudge tilt toward the optimal angle (state it).
- High altitude / high UV: periodic visual inspection for encapsulant yellowing; not a daily lever.
- Humid: overnight dew cements dust — early-morning wipe timing matters.
- Rain coming soon: let the rain pre-clean, then squeegee; don't waste a wash today.

EVIDENCE HONESTY:
- Numeric estimated_gain_percent ONLY for well-established, quantifiable mechanisms (cleaning
  soiled glass, raising mounts for airflow, correcting a real tilt/shading problem).
- Reasonable-but-marginal/unquantifiable actions: set estimated_gain_percent = 0 and
  confidence = "marginal". Never invent a number like "+0.5%". confidence ∈ high|medium|marginal.

BANNED (wrong/unsafe — never recommend):
- Tilting/repositioning a FIXED array "to catch wind".
- Consumer hydrophobic coatings (e.g. Rain-X) as anti-soiling.
- Spraying/misting water on the panel FACE to cool it (thermal shock). Cooling = airflow only.

CONSTRAINTS:
- Rank by factor size; gain per action <= the loss of the factor it addresses.
- At most ONE cleaning action per group; addresses_factor unique across the whole output.
- do_now = doable in <3h, no contractor. If rain prob > 50% in next 48h, no wet cleaning now.
- QUALITY OVER QUANTITY: a thin day should produce a short plan. Empty groups are fine. No padding.
- Be concise, no repetition. kWh fields are recomputed by the system; just estimate them.

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
    physics = state["physics"]
    pvgis = state["raw_data"].get("pvgis") or {}
    per_kwp = pvgis.get("annual_yield_kwh_per_kwp") or 0
    baseline = per_kwp * 5  # 5 kWp assumption

    sp = site_profile(state["raw_data"], physics, state["lat"], state["lon"])
    user_msg = (
        f"Location: {state['raw_data'].get('location_name')}\n"
        f"PVGIS yield per kWp: {per_kwp} kWh/yr -> 5 kWp baseline ~{baseline:.0f} kWh/yr\n\n"
        f"{site_profile_block(sp)}\n\n"
        f"{physics_prompt_block(physics)}\n\n"
        f"=== VALIDATED FACTORS ===\n{json.dumps(analysis.get('factors'), indent=2)}\n\n"
        f"Max recoverable today (engine): {physics['recoverable_today_pct']}%\n"
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

    # Validator: de-loop, dedupe, cap gains to factors, recompute kWh from baseline.
    recs, issues = validate_recommendations(recs, analysis, baseline)
    return {**state, "recommendations": recs, "advice_issues": issues, "advice_raw": raw_text}