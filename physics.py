# """
# physics.py
# ----------
# Deterministic solar-PV physics. NO LLM here — these are the ground-truth numbers
# the analyst/advisor agents are grounded against.

# Why this exists:
#   The old system asked the LLM to BE the calculator (cell_temp = ambient + 25,
#   cloud_loss = cloud% * 0.5, soiling = (PM10-25)/25*5, then a hand-wavy wind
#   "discount"). That double-counted wind, ignored the measured irradiance, and let
#   losses add past what is physically possible. Here we compute each loss from a
#   named model, compound them correctly, and separate ENVIRONMENTAL loss (clouds —
#   not the user's fault, not recoverable) from CONTROLLABLE loss (heat, soiling —
#   what recommendations can actually attack).

# Models used (all standard, citable):
#   - Faiman (IEC 61853) module-temperature model — naturally folds in wind cooling,
#     so there is no separate, double-counted "wind discount".
#   - Crystalline-Si temperature coefficient ~ -0.40 %/°C.
#   - Clearness-index cloud loss from measured GHI vs Haurwitz clear-sky GHI,
#     using a NOAA solar-position calc — uses the data you already fetch instead of
#     a guess from cloud-cover %.
#   - Soiling: a deliberately CONSERVATIVE proxy from PM10 (instantaneous PM10 is a
#     weak proxy for accumulated soiling, so we keep it small and flag low confidence).
# """

# from __future__ import annotations

# import datetime as _dt
# import math
# from typing import Any

# # ── Tunable constants (documented so they are not magic numbers) ──────────────

# FAIMAN_U0 = 25.0          # W/m²/K  — constant heat-loss coefficient (Faiman)
# FAIMAN_U1 = 6.84          # W·s/m³/K — wind-dependent coefficient (Faiman)
# TEMP_COEFF_PER_C = 0.0040 # crystalline-Si power loss per °C above 25°C (0.40%/°C)
# STC_TEMP_C = 25.0

# CLEARSKY_KT = 0.75        # clear-sky clearness index ceiling for "100% available"
# SOILING_PM10_THRESHOLD = 20.0   # µg/m³ below which soiling is treated as ~0
# SOILING_RATE = 0.15             # %% soiling per (µg/m³ over threshold)/10
# SOILING_CAP = 8.0               # hard cap — instantaneous PM10 cannot prove more
# RAIN_CLEAN_MM = 2.0             # >this much rain in last 24h substantially cleans

# # How much of each controllable loss can realistically be recovered SOON.
# # Soiling is almost fully recoverable (clean it). Heat is only partly recoverable
# # short-term (airflow/cooling) — you cannot make a 40°C day cool.
# RECOVERABLE_FRACTION = {"soiling": 0.90, "heat": 0.30}


# # ── Solar geometry (NOAA simplified) ──────────────────────────────────────────

# def solar_zenith(lat: float, lon: float, when_utc: _dt.datetime) -> tuple[float, float]:
#     """Return (zenith_degrees, cos_zenith) for a UTC datetime. cos<=0 means night."""
#     n = when_utc.timetuple().tm_yday
#     hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
#     gamma = 2 * math.pi / 365 * (n - 1 + (hour - 12) / 24)

#     eqtime = 229.18 * (
#         0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
#         - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
#     )
#     decl = (
#         0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
#         - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
#         - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
#     )
#     time_offset = eqtime + 4 * lon            # timezone = 0 (UTC)
#     tst = hour * 60 + time_offset             # true solar time (minutes)
#     ha = math.radians(tst / 4 - 180)          # hour angle
#     lat_r = math.radians(lat)
#     cos_z = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(ha)
#     cos_z = max(-1.0, min(1.0, cos_z))
#     return math.degrees(math.acos(cos_z)), cos_z


# def clear_sky_ghi(cos_zenith: float) -> float:
#     """Haurwitz clear-sky GHI (W/m²). 0 at/after sunset."""
#     if cos_zenith <= 0:
#         return 0.0
#     return 1098.0 * cos_zenith * math.exp(-0.059 / cos_zenith)


# # ── Helpers ───────────────────────────────────────────────────────────────────

# def _clamp(x: float, lo: float, hi: float) -> float:
#     return max(lo, min(hi, x))


# def _parse_when(weather: dict | None) -> _dt.datetime:
#     """Best-effort current UTC time. Uses Open-Meteo 'current.time' if present."""
#     # Open-Meteo 'current' time is local to the requested tz; we only need a rough
#     # UTC for solar geometry, so fall back to utcnow() which is accurate enough.
#     return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


# # ── Core computation ──────────────────────────────────────────────────────────

# def compute_physics(raw_data: dict[str, Any], lat: float, lon: float,
#                     when_utc: _dt.datetime | None = None) -> dict[str, Any]:
#     """
#     Turn raw API data into ground-truth physics. Returns a dict that is safe to
#     embed directly into an LLM prompt and to validate LLM output against.
#     """
#     weather = raw_data.get("weather") or {}
#     aq = raw_data.get("air_quality") or {}
#     notes: list[str] = []
#     confidence: dict[str, str] = {}

#     when = when_utc or _parse_when(weather)

#     temp_c = weather.get("current_temp_c")
#     wind_ms = weather.get("current_wind_speed_m_s")
#     ghi = weather.get("current_shortwave_radiation_w_m2")
#     cloud_pct = weather.get("current_cloud_cover_percent")
#     pm10 = aq.get("pm10_ug_m3")

#     # --- 1. Cell temperature & heat loss (Faiman; wind folded in, not discounted) ---
#     heat_loss_pct = 0.0
#     cell_temp_c = None
#     if temp_c is not None and ghi is not None:
#         ws = wind_ms if (wind_ms is not None) else 1.0
#         cell_temp_c = temp_c + ghi / (FAIMAN_U0 + FAIMAN_U1 * ws)
#         heat_loss_pct = max(0.0, (cell_temp_c - STC_TEMP_C) * TEMP_COEFF_PER_C * 100)
#         confidence["heat"] = "high"
#     else:
#         confidence["heat"] = "unavailable"
#         notes.append("Heat loss could not be computed (missing temp or irradiance).")

#     # --- 2. Cloud / irradiance availability (ENVIRONMENTAL, not recoverable) ---
#     _, cos_z = solar_zenith(lat, lon, when)
#     csky = clear_sky_ghi(cos_z)
#     irradiance_availability_pct = 100.0
#     cloud_loss_pct = 0.0
#     if csky >= 50 and ghi is not None:
#         kt = ghi / csky
#         availability = _clamp(kt / CLEARSKY_KT, 0.0, 1.0)
#         irradiance_availability_pct = round(availability * 100, 1)
#         cloud_loss_pct = round((1 - availability) * 100, 1)
#         confidence["cloud"] = "high"
#     elif cloud_pct is not None:
#         # Night, or no clear-sky reference: fall back to cloud-cover, low confidence.
#         cloud_loss_pct = round(cloud_pct * 0.5, 1)
#         irradiance_availability_pct = round(100 - cloud_loss_pct, 1)
#         confidence["cloud"] = "low"
#         notes.append("Sun low/down — cloud loss estimated from cloud-cover %, not measured irradiance.")
#     else:
#         confidence["cloud"] = "unavailable"

#     # --- 3. Soiling (CONTROLLABLE, conservative, low confidence) ---
#     soiling_loss_pct = 0.0
#     if pm10 is not None:
#         raw_soil = max(0.0, (pm10 - SOILING_PM10_THRESHOLD) / 10.0 * SOILING_RATE * 10)
#         soiling_loss_pct = _clamp(raw_soil, 0.0, SOILING_CAP)
#         # If it rained meaningfully in the last day, soiling is largely washed off.
#         past_rain = _recent_rain_mm(weather)
#         if past_rain >= RAIN_CLEAN_MM:
#             soiling_loss_pct *= 0.4
#             notes.append(f"Recent rain ({past_rain:.1f} mm/24h) likely rinsed panels — soiling reduced.")
#         soiling_loss_pct = round(soiling_loss_pct, 1)
#         confidence["soiling"] = "low"  # instantaneous PM10 is a weak proxy
#     else:
#         confidence["soiling"] = "unavailable"

#     # --- 4. Compound the losses correctly (they multiply, they do not add) ---
#     controllable = _compound([heat_loss_pct, soiling_loss_pct])
#     total_vs_ideal = _compound([heat_loss_pct, soiling_loss_pct, cloud_loss_pct])

#     # Score reflects SYSTEM health given the sun it is getting (controllable only),
#     # so the user is not told they are "failing" because of weather they cannot fix.
#     score = int(round(100 - controllable))

#     # --- 5. Per-factor recoverable estimate (caps the advisor) ---
#     recoverable_heat = heat_loss_pct * RECOVERABLE_FRACTION["heat"]
#     recoverable_soiling = soiling_loss_pct * RECOVERABLE_FRACTION["soiling"]
#     recoverable_today = round(_compound([recoverable_heat, recoverable_soiling]), 1)

#     return {
#         "inputs_used": {
#             "ambient_temp_c": temp_c,
#             "wind_speed_m_s": wind_ms,
#             "measured_ghi_w_m2": ghi,
#             "cloud_cover_pct": cloud_pct,
#             "pm10_ug_m3": pm10,
#             "clear_sky_ghi_w_m2": round(csky, 1),
#         },
#         "cell_temp_c": round(cell_temp_c, 1) if cell_temp_c is not None else None,
#         # Controllable
#         "heat_loss_pct": round(heat_loss_pct, 1),
#         "heat_loss_recoverable_pct": round(recoverable_heat, 1),
#         "soiling_loss_pct": soiling_loss_pct,
#         "soiling_loss_recoverable_pct": round(recoverable_soiling, 1),
#         "controllable_loss_pct": round(controllable, 1),
#         # Environmental
#         "cloud_loss_pct": cloud_loss_pct,
#         "irradiance_availability_pct": irradiance_availability_pct,
#         # Headline
#         "efficiency_score": score,
#         "total_loss_vs_ideal_pct": round(total_vs_ideal, 1),
#         "recoverable_today_pct": recoverable_today,
#         "confidence": confidence,
#         "notes": notes,
#     }


# def _recent_rain_mm(weather: dict) -> float:
#     """Sum precipitation over the available past window (Open-Meteo past_days=1)."""
#     daily = (weather or {}).get("daily") or {}
#     sums = daily.get("precipitation_sum_mm") or []
#     # daily[0] is yesterday when past_days=1 is set
#     try:
#         return float(sums[0]) if sums else 0.0
#     except (TypeError, ValueError, IndexError):
#         return 0.0


# def _compound(losses_pct: list[float]) -> float:
#     """Combine independent fractional losses multiplicatively. Returns a %."""
#     remaining = 1.0
#     for l in losses_pct:
#         remaining *= (1 - max(0.0, l) / 100.0)
#     return (1 - remaining) * 100.0


# def physics_prompt_block(p: dict[str, Any]) -> str:
#     """Render the physics result as a compact, explicit block for the LLM prompt."""
#     inp = p["inputs_used"]
#     lines = [
#         "=== PHYSICS ENGINE (ground truth — computed in code, NOT by you) ===",
#         f"Inputs: ambient={inp['ambient_temp_c']}°C, wind={inp['wind_speed_m_s']} m/s, "
#         f"measured_GHI={inp['measured_ghi_w_m2']} W/m², clear_sky_GHI={inp['clear_sky_ghi_w_m2']} W/m², "
#         f"PM10={inp['pm10_ug_m3']} µg/m³",
#         f"Cell temperature (Faiman, wind already included): {p['cell_temp_c']}°C",
#         "",
#         "CONTROLLABLE losses (recommendations may target these):",
#         f"  • Heat loss: {p['heat_loss_pct']}%  (recoverable soon ≈ {p['heat_loss_recoverable_pct']}%)",
#         f"  • Soiling loss: {p['soiling_loss_pct']}%  (recoverable soon ≈ {p['soiling_loss_recoverable_pct']}%, LOW confidence)",
#         f"  • Combined controllable: {p['controllable_loss_pct']}%",
#         "",
#         "ENVIRONMENTAL (NOT recoverable — do NOT write recommendations to 'fix' this):",
#         f"  • Cloud/irradiance: panels receiving {p['irradiance_availability_pct']}% of clear-sky potential "
#         f"(={p['cloud_loss_pct']}% irradiance shortfall)",
#         "",
#         f"HEADLINE: efficiency score {p['efficiency_score']}/100 (controllable health). "
#         f"Max realistically recoverable today ≈ {p['recoverable_today_pct']}%.",
#         f"Confidence: {p['confidence']}",
#     ]
#     if p["notes"]:
#         lines.append("Notes: " + " ".join(p["notes"]))
#     return "\n".join(lines)
























"""
physics.py
----------
Deterministic solar-PV physics. NO LLM here — these are the ground-truth numbers
the analyst/advisor agents are grounded against.

Why this exists:
  The old system asked the LLM to BE the calculator (cell_temp = ambient + 25,
  cloud_loss = cloud% * 0.5, soiling = (PM10-25)/25*5, then a hand-wavy wind
  "discount"). That double-counted wind, ignored the measured irradiance, and let
  losses add past what is physically possible. Here we compute each loss from a
  named model, compound them correctly, and separate ENVIRONMENTAL loss (clouds —
  not the user's fault, not recoverable) from CONTROLLABLE loss (heat, soiling —
  what recommendations can actually attack).

Models used (all standard, citable):
  - Faiman (IEC 61853) module-temperature model — naturally folds in wind cooling,
    so there is no separate, double-counted "wind discount".
  - Crystalline-Si temperature coefficient ~ -0.40 %/°C.
  - Clearness-index cloud loss from measured GHI vs Haurwitz clear-sky GHI,
    using a NOAA solar-position calc — uses the data you already fetch instead of
    a guess from cloud-cover %.
  - Soiling: a deliberately CONSERVATIVE proxy from PM10 (instantaneous PM10 is a
    weak proxy for accumulated soiling, so we keep it small and flag low confidence).
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

# ── Tunable constants (documented so they are not magic numbers) ──────────────

FAIMAN_U0 = 25.0          # W/m²/K  — constant heat-loss coefficient (Faiman)
FAIMAN_U1 = 6.84          # W·s/m³/K — wind-dependent coefficient (Faiman)
TEMP_COEFF_PER_C = 0.0040 # crystalline-Si power loss per °C above 25°C (0.40%/°C)
STC_TEMP_C = 25.0

CLEARSKY_KT = 0.75        # clear-sky clearness index ceiling for "100% available"
SOILING_PM10_THRESHOLD = 20.0   # µg/m³ below which soiling is treated as ~0
SOILING_RATE = 0.15             # %% soiling per (µg/m³ over threshold)/10
SOILING_CAP = 8.0               # hard cap — instantaneous PM10 cannot prove more
RAIN_CLEAN_MM = 2.0             # >this much rain in last 24h substantially cleans

# How much of each controllable loss can realistically be recovered SOON.
# Soiling is almost fully recoverable (clean it). Heat is only partly recoverable
# short-term (airflow/cooling) — you cannot make a 40°C day cool.
RECOVERABLE_FRACTION = {"soiling": 0.90, "heat": 0.30}


# ── Solar geometry (NOAA simplified) ──────────────────────────────────────────

def solar_zenith(lat: float, lon: float, when_utc: _dt.datetime) -> tuple[float, float]:
    """Return (zenith_degrees, cos_zenith) for a UTC datetime. cos<=0 means night."""
    n = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    gamma = 2 * math.pi / 365 * (n - 1 + (hour - 12) / 24)

    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    time_offset = eqtime + 4 * lon            # timezone = 0 (UTC)
    tst = hour * 60 + time_offset             # true solar time (minutes)
    ha = math.radians(tst / 4 - 180)          # hour angle
    lat_r = math.radians(lat)
    cos_z = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(ha)
    cos_z = max(-1.0, min(1.0, cos_z))
    return math.degrees(math.acos(cos_z)), cos_z


def clear_sky_ghi(cos_zenith: float) -> float:
    """Haurwitz clear-sky GHI (W/m²). 0 at/after sunset."""
    if cos_zenith <= 0:
        return 0.0
    return 1098.0 * cos_zenith * math.exp(-0.059 / cos_zenith)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _parse_when(weather: dict | None) -> _dt.datetime:
    """Best-effort current UTC time. Uses Open-Meteo 'current.time' if present."""
    # Open-Meteo 'current' time is local to the requested tz; we only need a rough
    # UTC for solar geometry, so fall back to utcnow() which is accurate enough.
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


# ── Core computation ──────────────────────────────────────────────────────────

def compute_physics(raw_data: dict[str, Any], lat: float, lon: float,
                    when_utc: _dt.datetime | None = None) -> dict[str, Any]:
    """
    Turn raw API data into ground-truth physics. Returns a dict that is safe to
    embed directly into an LLM prompt and to validate LLM output against.
    """
    weather = raw_data.get("weather") or {}
    aq = raw_data.get("air_quality") or {}
    notes: list[str] = []
    confidence: dict[str, str] = {}

    when = when_utc or _parse_when(weather)

    temp_c = weather.get("current_temp_c")
    wind_ms = weather.get("current_wind_speed_m_s")
    ghi = weather.get("current_shortwave_radiation_w_m2")
    cloud_pct = weather.get("current_cloud_cover_percent")
    pm10 = aq.get("pm10_ug_m3")

    # --- 1. Cell temperature & heat loss (Faiman; wind folded in, not discounted) ---
    heat_loss_pct = 0.0
    cell_temp_c = None
    if temp_c is not None and ghi is not None:
        ws = wind_ms if (wind_ms is not None) else 1.0
        cell_temp_c = temp_c + ghi / (FAIMAN_U0 + FAIMAN_U1 * ws)
        heat_loss_pct = max(0.0, (cell_temp_c - STC_TEMP_C) * TEMP_COEFF_PER_C * 100)
        confidence["heat"] = "high"
    else:
        confidence["heat"] = "unavailable"
        notes.append("Heat loss could not be computed (missing temp or irradiance).")

    # --- 2. Cloud / irradiance availability (ENVIRONMENTAL, not recoverable) ---
    _, cos_z = solar_zenith(lat, lon, when)
    csky = clear_sky_ghi(cos_z)
    irradiance_availability_pct = 100.0
    cloud_loss_pct = 0.0
    if csky >= 50 and ghi is not None:
        kt = ghi / csky
        availability = _clamp(kt / CLEARSKY_KT, 0.0, 1.0)
        irradiance_availability_pct = round(availability * 100, 1)
        cloud_loss_pct = round((1 - availability) * 100, 1)
        confidence["cloud"] = "high"
    elif cloud_pct is not None:
        # Night, or no clear-sky reference: fall back to cloud-cover, low confidence.
        cloud_loss_pct = round(cloud_pct * 0.5, 1)
        irradiance_availability_pct = round(100 - cloud_loss_pct, 1)
        confidence["cloud"] = "low"
        notes.append("Sun low/down — cloud loss estimated from cloud-cover %, not measured irradiance.")
    else:
        confidence["cloud"] = "unavailable"

    # --- 3. Soiling (CONTROLLABLE, conservative, low confidence) ---
    soiling_loss_pct = 0.0
    if pm10 is not None:
        raw_soil = max(0.0, (pm10 - SOILING_PM10_THRESHOLD) / 10.0 * SOILING_RATE * 10)
        soiling_loss_pct = _clamp(raw_soil, 0.0, SOILING_CAP)
        # If it rained meaningfully in the last day, soiling is largely washed off.
        past_rain = _recent_rain_mm(weather)
        if past_rain >= RAIN_CLEAN_MM:
            soiling_loss_pct *= 0.4
            notes.append(f"Recent rain ({past_rain:.1f} mm/24h) likely rinsed panels — soiling reduced.")
        soiling_loss_pct = round(soiling_loss_pct, 1)
        confidence["soiling"] = "low"  # instantaneous PM10 is a weak proxy
    else:
        confidence["soiling"] = "unavailable"

    # --- 4. Compound the losses correctly (they multiply, they do not add) ---
    controllable = _compound([heat_loss_pct, soiling_loss_pct])
    total_vs_ideal = _compound([heat_loss_pct, soiling_loss_pct, cloud_loss_pct])

    # Score reflects SYSTEM health given the sun it is getting (controllable only),
    # so the user is not told they are "failing" because of weather they cannot fix.
    score = int(round(100 - controllable))

    # --- 5. Per-factor recoverable estimate (caps the advisor) ---
    recoverable_heat = heat_loss_pct * RECOVERABLE_FRACTION["heat"]
    recoverable_soiling = soiling_loss_pct * RECOVERABLE_FRACTION["soiling"]
    recoverable_today = round(_compound([recoverable_heat, recoverable_soiling]), 1)

    return {
        "inputs_used": {
            "ambient_temp_c": temp_c,
            "wind_speed_m_s": wind_ms,
            "measured_ghi_w_m2": ghi,
            "cloud_cover_pct": cloud_pct,
            "pm10_ug_m3": pm10,
            "clear_sky_ghi_w_m2": round(csky, 1),
        },
        "cell_temp_c": round(cell_temp_c, 1) if cell_temp_c is not None else None,
        # Controllable
        "heat_loss_pct": round(heat_loss_pct, 1),
        "heat_loss_recoverable_pct": round(recoverable_heat, 1),
        "soiling_loss_pct": soiling_loss_pct,
        "soiling_loss_recoverable_pct": round(recoverable_soiling, 1),
        "controllable_loss_pct": round(controllable, 1),
        # Environmental
        "cloud_loss_pct": cloud_loss_pct,
        "irradiance_availability_pct": irradiance_availability_pct,
        # Headline
        "efficiency_score": score,
        "total_loss_vs_ideal_pct": round(total_vs_ideal, 1),
        "recoverable_today_pct": recoverable_today,
        "confidence": confidence,
        "notes": notes,
    }


def _recent_rain_mm(weather: dict) -> float:
    """Sum precipitation over the available past window (Open-Meteo past_days=1)."""
    daily = (weather or {}).get("daily") or {}
    sums = daily.get("precipitation_sum_mm") or []
    # daily[0] is yesterday when past_days=1 is set
    try:
        return float(sums[0]) if sums else 0.0
    except (TypeError, ValueError, IndexError):
        return 0.0


def _compound(losses_pct: list[float]) -> float:
    """Combine independent fractional losses multiplicatively. Returns a %."""
    remaining = 1.0
    for l in losses_pct:
        remaining *= (1 - max(0.0, l) / 100.0)
    return (1 - remaining) * 100.0


def physics_prompt_block(p: dict[str, Any]) -> str:
    """Render the physics result as a compact, explicit block for the LLM prompt."""
    inp = p["inputs_used"]
    lines = [
        "=== PHYSICS ENGINE (ground truth — computed in code, NOT by you) ===",
        f"Inputs: ambient={inp['ambient_temp_c']}°C, wind={inp['wind_speed_m_s']} m/s, "
        f"measured_GHI={inp['measured_ghi_w_m2']} W/m², clear_sky_GHI={inp['clear_sky_ghi_w_m2']} W/m², "
        f"PM10={inp['pm10_ug_m3']} µg/m³",
        f"Cell temperature (Faiman, wind already included): {p['cell_temp_c']}°C",
        "",
        "CONTROLLABLE losses (recommendations may target these):",
        f"  • Heat loss: {p['heat_loss_pct']}%  (recoverable soon ≈ {p['heat_loss_recoverable_pct']}%)",
        f"  • Soiling loss: {p['soiling_loss_pct']}%  (recoverable soon ≈ {p['soiling_loss_recoverable_pct']}%, LOW confidence)",
        f"  • Combined controllable: {p['controllable_loss_pct']}%",
        "",
        "ENVIRONMENTAL (NOT recoverable — do NOT write recommendations to 'fix' this):",
        f"  • Cloud/irradiance: panels receiving {p['irradiance_availability_pct']}% of clear-sky potential "
        f"(={p['cloud_loss_pct']}% irradiance shortfall)",
        "",
        f"HEADLINE: efficiency score {p['efficiency_score']}/100 (controllable health). "
        f"Max realistically recoverable today ≈ {p['recoverable_today_pct']}%.",
        f"Confidence: {p['confidence']}",
    ]
    if p["notes"]:
        lines.append("Notes: " + " ".join(p["notes"]))
    return "\n".join(lines)


# ── Site profile: deterministic "what's distinctive here" tags ─────────────────
# Used to force LOCATION-SPECIFIC advice instead of the generic heat/airflow default.

_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _cardinal(deg):
    if deg is None:
        return None
    return _COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def _rain_window(weather: dict) -> str:
    """Find the next rain in the 24h hourly forecast, as a short human string."""
    h = (weather or {}).get("next_24h_hourly") or {}
    times = h.get("times") or []
    probs = h.get("rain_probability_percent") or []
    mm = h.get("precipitation_mm") or []
    for i, t in enumerate(times):
        p = probs[i] if i < len(probs) else 0
        m = mm[i] if i < len(mm) else 0
        if (p or 0) >= 50 or (m or 0) >= 0.3:
            hhmm = str(t)[-5:] if t else "?"
            return f"rain likely around {hhmm} (~{p}%)"
    return "no significant rain in next 24h"


def site_profile(raw_data: dict, physics: dict, lat: float, lon: float) -> dict:
    """Classify the site so the advisor can target what's UNUSUAL here, not the default."""
    weather = raw_data.get("weather") or {}
    aq = raw_data.get("air_quality") or {}
    marine = raw_data.get("marine")
    pvgis = raw_data.get("pvgis") or {}

    temp = weather.get("current_temp_c")
    humidity = weather.get("current_humidity_percent")
    wind = weather.get("current_wind_speed_m_s")
    uv = weather.get("current_uv_index")
    elev = pvgis.get("elevation_m")
    pm10 = aq.get("pm10_ug_m3")

    coastal = bool(marine and marine.get("is_coastal"))

    # Dust severity tier → drives cleaning cadence (daily vs monthly).
    if pm10 is None:
        dust_tier = "unknown"
    elif pm10 >= 250:
        dust_tier = "extreme"
    elif pm10 >= 100:
        dust_tier = "high"
    elif pm10 >= 50:
        dust_tier = "moderate"
    else:
        dust_tier = "low"

    # Climate band from temp + humidity.
    if temp is not None and humidity is not None:
        if temp >= 32 and humidity < 40:
            climate = "hot-arid"
        elif temp >= 30 and humidity >= 60:
            climate = "hot-humid"
        elif temp <= 25:
            climate = "cool"
        else:
            climate = "warm"
    else:
        climate = "unknown"

    high_altitude = elev is not None and elev >= 1500

    # Build the distinctive-feature list the advisor must lead from.
    distinctive = []
    if coastal:
        distinctive.append("coastal: salt aerosols need fresh-water rinse (inland sites don't)")
    if dust_tier in ("high", "extreme"):
        distinctive.append(f"{dust_tier} dust (PM10 {pm10}) → frequent/scheduled cleaning is the main lever")
    if high_altitude:
        distinctive.append(f"high altitude ({elev} m) → stronger UV, faster encapsulant aging")
    if climate == "hot-humid":
        distinctive.append("hot & humid → dew + dust cement onto glass overnight")
    if climate == "hot-arid":
        distinctive.append("hot & dry → dust-dominated soiling, near-zero rain self-cleaning")
    if wind is not None and wind < 1.5 and physics.get("heat_loss_pct", 0) >= 6:
        distinctive.append("very low wind → little convective cooling; airflow/mounting matters more")
    if climate == "cool" and physics.get("heat_loss_pct", 0) < 6:
        distinctive.append("cool climate → heat is NOT a major factor; do not over-prescribe cooling")

    return {
        "coastal": coastal,
        "dust_tier": dust_tier,
        "climate": climate,
        "high_altitude": high_altitude,
        "elevation_m": elev,
        "uv_index": uv,
        "humidity_pct": humidity,
        "wind_ms": wind,
        "wind_dir": _cardinal(weather.get("current_wind_direction_deg")),
        "optimal_tilt_deg": pvgis.get("optimal_tilt_deg"),
        "optimal_azimuth_deg": pvgis.get("optimal_azimuth_deg"),
        "rain_window": _rain_window(weather),
        "distinctive": distinctive,
    }


def site_profile_block(p: dict) -> str:
    """Render the site profile for the advisor prompt."""
    lines = [
        "=== SITE PROFILE (build the plan around THESE distinctive features) ===",
        f"Climate: {p['climate']} | coastal: {p['coastal']} | dust tier: {p['dust_tier']} | "
        f"altitude: {p['elevation_m']} m | UV index: {p['uv_index']}",
        f"Wind: {p['wind_ms']} m/s from {p['wind_dir']} | humidity: {p['humidity_pct']}%",
        f"PVGIS optimal tilt: {p['optimal_tilt_deg']}°, azimuth: {p['optimal_azimuth_deg']}° (0=south)",
        f"Rain outlook: {p['rain_window']}",
        "MOST DISTINCTIVE here (lead your #1 action from these):",
    ]
    if p["distinctive"]:
        lines += [f"  • {d}" for d in p["distinctive"]]
    else:
        lines.append("  • (unremarkable — rank actions strictly by the factor sizes)")
    return "\n".join(lines)