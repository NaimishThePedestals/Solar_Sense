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

# HARD RULES:
# 1. Each action's estimated_gain_percent must be <= the loss of the factor it addresses.
#    You cannot recover more than a factor is losing. (The system will cap this anyway.)
# 2. NEVER recommend spraying/misting/hosing water onto the panel FACE during hot/peak
#    hours — thermal shock cracks cells. Cooling actions must use airflow: raise mounts,
#    add standoffs, improve back-ventilation, or shade — or be done only at dawn/after sunset.
# 3. At most ONE cleaning action per group. Other actions must target heat/airflow, timing,
#    or system losses — not more cleaning.
# 4. addresses_factor must be unique within the whole output (no duplicate actions).
# 5. At least one action references this site's geography; at least one cites a SPECIFIC
#    time window from the forecast data (e.g. "before 8 AM", "ahead of Wednesday's rain").
# 6. do_now items: doable in <3 hours, no contractor, no delivery.
# 7. RAIN-AWARE: if rain probability > 50% in the next 48h, do not recommend wet cleaning now.

# Item counts: do_now 2-3, do_this_week 2-3, long_term 1-2. Be concise — no repetition.
# kWh fields are recomputed by the system from the real baseline, so just estimate them.

# Return ONLY valid JSON, no prose, no fences:
# {
#   "reasoning": "<2 sentences: biggest recoverable factor and why the top action attacks it>",
#   "system_assumption": "5 kWp rooftop",
#   "annual_baseline_kwh": <int>,
#   "todays_recoverable_percent": <int>,
#   "do_now": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
#               "estimated_extra_kwh_today":<num>,"effort":"low|medium|high",
#               "estimated_cost_inr":"<range or free>","why_now":"<cites a number>"}],
#   "do_this_week": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
#               "estimated_extra_kwh_per_week":<num>,"effort":"...","estimated_cost_inr":"...","why_now":"..."}],
#   "long_term": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
#               "estimated_extra_kwh_per_year":<num>,"effort":"...","estimated_cost_inr":"...","payback_notes":"..."}]
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

from physics import compute_physics, physics_prompt_block
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

ADVISOR_SYSTEM = """You are a hands-on solar installer (500+ Indian rooftop jobs) turning a
diagnosis into an action plan real people will follow.

You are given the validated factors and the physics numbers. RANK actions by the size of
the factor they attack: the biggest controllable loss gets the top "do_now" action.

QUALITY OVER QUANTITY — this is the most important rule:
- Recommend ONLY actions that are genuinely worthwhile for THIS site today. Do NOT pad.
- If only one action is justified in a group, return just one. Empty groups are fine.
- Two solid actions beat three with filler. A thin day SHOULD produce a short plan.

EVIDENCE HONESTY:
- Attach a numeric estimated_gain_percent ONLY to actions with a well-established,
  quantifiable mechanism: washing soiled panels, raising mount height / adding standoffs
  for airflow, fixing actual shading shown in the data.
- For actions that are reasonable but marginal or hard to quantify (clearing minor debris,
  trimming nearby vegetation, backside dusting), set estimated_gain_percent = 0 and
  confidence = "marginal". DO NOT invent a specific percentage like "+0.5%".
- confidence is one of: "high", "medium", "marginal".

BANNED ACTIONS — never recommend these (they are wrong or counterproductive):
- Tilting, repositioning, or re-angling a FIXED rooftop array "to catch wind" — fixed
  arrays don't move, and changing tilt loses more than convection gains.
- Consumer hydrophobic coatings (e.g. Rain-X) as anti-soiling — they can cement dust in
  arid air. Only a proper PV anti-soiling coating by a technician, and only long-term.
- Spraying/misting/hosing water onto the panel FACE to cool it — thermal shock cracks cells.
  Cooling = airflow (raise mounts, standoffs, back-ventilation, remove real obstructions),
  or wet-CLEANING only at dawn / after sunset.

OTHER RULES:
- At most ONE cleaning action per group. addresses_factor unique across the whole output.
- At least one action references this site's geography; at least one cites a SPECIFIC time
  window from the forecast (e.g. "before 8 AM", "ahead of Wednesday's rain").
- do_now items: doable in <3 hours, no contractor, no delivery.
- RAIN-AWARE: if rain probability > 50% in the next 48h, do not recommend wet cleaning now.
- Be concise — no repetition. kWh fields are recomputed by the system; just estimate them.

Return ONLY valid JSON, no prose, no fences:
{
  "reasoning": "<2 sentences: biggest recoverable factor and why the top action attacks it>",
  "system_assumption": "5 kWp rooftop",
  "annual_baseline_kwh": <int>,
  "todays_recoverable_percent": <int>,
  "do_now": [{"action":"...","addresses_factor":"...","estimated_gain_percent":<int>,
              "confidence":"high|medium|marginal","estimated_extra_kwh_today":<num>,
              "effort":"low|medium|high","estimated_cost_inr":"<range or free>","why_now":"<cites a number>"}],
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

    user_msg = (
        f"Location: {state['raw_data'].get('location_name')}\n"
        f"PVGIS yield per kWp: {per_kwp} kWh/yr -> 5 kWp baseline ~{baseline:.0f} kWh/yr\n\n"
        f"{physics_prompt_block(physics)}\n\n"
        f"=== VALIDATED FACTORS ===\n{json.dumps(analysis.get('factors'), indent=2)}\n\n"
        f"Max recoverable today (engine): {physics['recoverable_today_pct']}%\n"
        f"Rain prob next 48h (max %): "
        f"{(state['raw_data'].get('weather') or {}).get('daily', {}).get('rain_probability_max_percent', [])[:2]}\n\n"
        f"Produce the recommendations JSON."
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