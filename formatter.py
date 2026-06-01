"""
formatter.py
------------
Converts LangGraph pipeline output into messaging-platform friendly text.

- format_for_whatsapp()  → WhatsApp plain text with *bold* formatting
- format_for_telegram()  → Telegram MarkdownV2 (special chars must be escaped)

Telegram MarkdownV2 rules:
  Special chars that MUST be escaped: _ * [ ] ( ) ~ ` > # + - = | { } . !
  Bold = *text*   Italic = _text_   Code = `text`

IMPORTANT ESCAPING CONTRACT
---------------------------
_bold(), _italic() and _code() escape their content INTERNALLY.
=> Always pass RAW (unescaped) text to them.
=> Never call _esc() on something you then wrap in _bold/_italic/_code,
   and never hand-insert "\\." — that double-escapes and Telegram rejects
   the message with "can't parse entities".
For plain interpolated text (not wrapped in a helper) use _esc() exactly once.
"""

from __future__ import annotations

# ── Telegram MarkdownV2 escaping ──────────────────────────────────────────────

_TG_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def _esc(text) -> str:
    """Escape all Telegram MarkdownV2 special characters in plain text."""
    if text is None:
        return ""
    text = str(text)
    for ch in _TG_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


def _bold(text) -> str:
    """Bold. Escapes content internally — pass RAW text."""
    return f"*{_esc(text)}*"


def _italic(text) -> str:
    """Italic. Escapes content internally — pass RAW text."""
    return f"_{_esc(text)}_"


def _code(text) -> str:
    """Inline code. Inside a code span only backtick/backslash matter."""
    return f"`{str(text).replace('`', '')}`"


# ── Shared helpers ────────────────────────────────────────────────────────────

def _score_emoji(score: int) -> str:
    if score >= 80: return "🟢"
    if score >= 60: return "🟡"
    if score >= 40: return "🟠"
    return "🔴"


def _severity_emoji(severity: str) -> str:
    return {"low": "🟡", "medium": "🟠", "high": "🔴"}.get(severity, "⚪")


def _effort_emoji(effort: str) -> str:
    return {"low": "✅", "medium": "⚠️", "high": "🔧"}.get(effort, "⚪")


def _short_location(location: str) -> str:
    return ", ".join(location.split(",")[:3]).strip()


# ── Telegram formatter ────────────────────────────────────────────────────────

def format_for_telegram(final_state: dict) -> list[str]:
    """
    Returns a LIST of message chunks (each safe for Telegram's 4096 char limit).
    Uses MarkdownV2 formatting.
    """
    raw      = final_state.get("raw_data", {})
    analysis = final_state.get("analysis", {})
    recs     = final_state.get("recommendations", {})
    errors   = final_state.get("errors", [])

    location      = _short_location(raw.get("location_name", "Unknown"))
    lat           = final_state.get("lat", "")
    lon           = final_state.get("lon", "")
    score         = analysis.get("todays_efficiency_score", 0)
    loss          = analysis.get("todays_estimated_loss_percent", 0)
    summary       = analysis.get("todays_summary", "")
    factors       = analysis.get("factors", [])
    pvgis         = raw.get("pvgis") or {}
    weather       = raw.get("weather") or {}
    aq            = raw.get("air_quality") or {}
    marine        = raw.get("marine")
    baseline      = recs.get("annual_baseline_kwh", 0)
    recoverable   = recs.get("todays_recoverable_percent", 0)
    system_assume = recs.get("system_assumption", "5 kWp rooftop")

    chunks: list[str] = []

    # ── CHUNK 1: Header + Score + Conditions ─────────────────────────────
    c1: list[str] = [
        f"☀️ {_bold('SolarSense AI Report')}",
        f"📍 {_esc(location)}",
        f"🌐 {_code(f'{lat}, {lon}')}",
        "",
    ]

    if errors:
        c1.append(f"⚠️ {_bold('Data warnings:')}")
        for e in errors:
            c1.append(f"  • {_esc(e)}")
        c1.append("")

    bar_filled = "█" * (score // 10)
    bar_empty  = "░" * (10 - score // 10)
    c1 += [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"{_score_emoji(score)} {_bold(f'Today Efficiency: {score}/100')}",
        f"{_code(bar_filled + bar_empty)} {_esc(f'(-{loss}% loss)')}",
        "",
        _esc(summary),
        "",
    ]

    if weather or aq:
        c1.append(f"🌤 {_bold('Current Conditions')}")
        if weather:
            c1.append(f"  🌡 Temp: {_esc(weather.get('current_temp_c'))}°C  💨 Wind: {_esc(weather.get('current_wind_speed_m_s'))} m/s")
            c1.append(f"  ☁️ Cloud: {_esc(weather.get('current_cloud_cover_percent'))}%  💧 Humidity: {_esc(weather.get('current_humidity_percent'))}%")
            c1.append(f"  ⚡ Radiation: {_esc(weather.get('current_shortwave_radiation_w_m2'))} W/m²  🔆 UV: {_esc(weather.get('current_uv_index'))}")
        if aq:
            c1.append(f"  🌫 {_esc('PM2.5')}: {_esc(aq.get('pm2_5_ug_m3'))} µg/m³  PM10: {_esc(aq.get('pm10_ug_m3'))} µg/m³  Dust: {_esc(aq.get('dust_ug_m3'))} µg/m³")
        if marine:
            c1.append(f"  🌊 Waves: {_esc(marine.get('current_wave_height_m'))}m  SST: {_esc(marine.get('current_sea_surface_temp_c'))}°C")
        c1.append("")

    if pvgis:
        yield_txt = f"{pvgis.get('annual_yield_kwh_per_kwp')} kWh/year"
        c1 += [
            f"📊 {_bold('PVGIS Baseline (1 kWp)')}",
            f"  Annual yield: {_bold(yield_txt)}",
            f"  Optimal tilt: {_esc(pvgis.get('optimal_tilt_deg'))}°  Azimuth: {_esc(pvgis.get('optimal_azimuth_deg'))}°",
            f"  Elevation: {_esc(pvgis.get('elevation_m'))} m",
            "",
        ]

    chunks.append("\n".join(c1))

    # ── CHUNK 2: Efficiency Factors ───────────────────────────────────────
    if factors:
        c2: list[str] = [f"🔬 {_bold('What is Hurting Output RIGHT NOW')}", ""]
        for f in factors:
            sev    = f.get("severity", "low")
            name   = f.get("name", "")
            impact = f.get("todays_impact_percent", 0)
            c2 += [
                f"  {_severity_emoji(sev)} {_bold(name)} {_esc(f'(-{impact}%)')}",
                f"  📌 {_italic(f.get('current_value', ''))}",
                f"  {_esc(f.get('explanation', ''))}",
                "",
            ]
        chunks.append("\n".join(c2))

    # ── CHUNK 3: Action Plan + Do Now ─────────────────────────────────────
    c3: list[str] = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"💡 {_bold('Action Plan')}",
        f"  System: {_esc(system_assume)}",
        f"  Baseline: {_bold(f'{int(baseline)} kWh/year')}",
        f"  Recoverable today: {_bold(f'~{recoverable}%')}",
        "",
    ]
    do_now = recs.get("do_now", [])
    if do_now:
        c3.append(f"🚨 {_bold('DO NOW')}")
        c3.append("")
        for i, r in enumerate(do_now, 1):
            c3 += _reco_lines(i, r, "estimated_extra_kwh_today", "kWh today", "why_now", as_float=True)
    chunks.append("\n".join(c3))

    # ── CHUNK 4: This Week ────────────────────────────────────────────────
    do_week = recs.get("do_this_week", [])
    if do_week:
        c4: list[str] = [f"📅 {_bold('THIS WEEK — Quick wins')}", ""]
        for i, r in enumerate(do_week, 1):
            c4 += _reco_lines(i, r, "estimated_extra_kwh_per_week", "kWh/week", "why_now", as_float=True)
        chunks.append("\n".join(c4))

    # ── CHUNK 5: Long Term + Footer ───────────────────────────────────────
    long_term = recs.get("long_term", [])
    if long_term:
        c5: list[str] = [f"🏗 {_bold('LONG TERM — Bigger upgrades')}", ""]
        for i, r in enumerate(long_term, 1):
            c5 += _reco_lines(i, r, "estimated_extra_kwh_per_year", "kWh/year", "payback_notes", as_float=False)
        c5 += [
            "━━━━━━━━━━━━━━━━━━━━━━━",
            _italic("Powered by SolarSense AI · PVGIS + NASA POWER + Open-Meteo + DeepSeek"),
        ]
        chunks.append("\n".join(c5))

    return chunks


def _reco_lines(i: int, r: dict, kwh_field: str, kwh_unit: str,
                why_field: str, as_float: bool) -> list[str]:
    """Render one recommendation item as MarkdownV2 lines. All text RAW (helpers escape)."""
    eff  = r.get("effort", "medium")
    kwh  = r.get(kwh_field, 0) or 0
    gain = r.get("estimated_gain_percent", 0)
    cost = r.get("estimated_cost_inr", "")
    kwh_txt = f"{kwh:.1f}" if as_float else str(int(kwh))
    return [
        f"{_bold(f'{i}.')} {_esc(r.get('action', ''))}",
        f"   📈 {_bold(f'+{gain}%')}  ⚡ {_bold(f'+{kwh_txt} {kwh_unit}')}",
        f"   {_effort_emoji(eff)} Effort: {_esc(eff)}  💰 ₹{_esc(cost)}",
        f"   {_italic(r.get(why_field, ''))}",
        "",
    ]


# ── WhatsApp formatter ────────────────────────────────────────────────────────

def format_for_whatsapp(final_state: dict) -> str:
    """Plain text with *bold* for WhatsApp (no escaping needed)."""
    raw      = final_state.get("raw_data", {})
    analysis = final_state.get("analysis", {})
    recs     = final_state.get("recommendations", {})
    errors   = final_state.get("errors", [])

    location      = _short_location(raw.get("location_name", "Unknown"))
    lat           = final_state.get("lat", "")
    lon           = final_state.get("lon", "")
    score         = analysis.get("todays_efficiency_score", 0)
    loss          = analysis.get("todays_estimated_loss_percent", 0)
    summary       = analysis.get("todays_summary", "")
    factors       = analysis.get("factors", [])
    pvgis         = raw.get("pvgis") or {}
    weather       = raw.get("weather") or {}
    aq            = raw.get("air_quality") or {}
    marine        = raw.get("marine")
    baseline      = recs.get("annual_baseline_kwh", 0)
    recoverable   = recs.get("todays_recoverable_percent", 0)
    system_assume = recs.get("system_assumption", "5 kWp rooftop")

    lines: list[str] = ["☀️ *SolarSense AI Report*", f"📍 {location}", f"🌐 {lat}, {lon}", ""]

    if errors:
        lines.append("⚠️ *Data warnings:*")
        for e in errors:
            lines.append(f"  • {e}")
        lines.append("")

    bar_filled = "█" * (score // 10)
    bar_empty  = "░" * (10 - score // 10)
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"{_score_emoji(score)} *Today's Efficiency: {score}/100*",
        f"`{bar_filled}{bar_empty}` (-{loss}% loss)",
        "", summary, "",
    ]

    if weather or aq:
        lines.append("🌤 *Current Conditions*")
        if weather:
            lines.append(f"  🌡 Temp: {weather.get('current_temp_c')}°C  💨 Wind: {weather.get('current_wind_speed_m_s')} m/s")
            lines.append(f"  ☁️ Cloud: {weather.get('current_cloud_cover_percent')}%  💧 Humidity: {weather.get('current_humidity_percent')}%")
            lines.append(f"  ⚡ Radiation: {weather.get('current_shortwave_radiation_w_m2')} W/m²  🔆 UV: {weather.get('current_uv_index')}")
        if aq:
            lines.append(f"  🌫 PM2.5: {aq.get('pm2_5_ug_m3')} µg/m³  PM10: {aq.get('pm10_ug_m3')} µg/m³")
        if marine:
            lines.append(f"  🌊 Waves: {marine.get('current_wave_height_m')}m  SST: {marine.get('current_sea_surface_temp_c')}°C")
        lines.append("")

    if pvgis:
        lines += [
            "📊 *PVGIS Baseline (1 kWp)*",
            f"  Annual yield: {pvgis.get('annual_yield_kwh_per_kwp')} kWh/year",
            f"  Optimal tilt: {pvgis.get('optimal_tilt_deg')}°  Azimuth: {pvgis.get('optimal_azimuth_deg')}°", "",
        ]

    if factors:
        lines.append("🔬 *What's Hurting Output RIGHT NOW*")
        for f in factors:
            sev = f.get("severity", "low")
            lines.append(f"  {_severity_emoji(sev)} *{f.get('name')}* (-{f.get('todays_impact_percent')}%)")
            lines.append(f"     {f.get('current_value')} — {f.get('explanation')}")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━", "💡 *Action Plan*",
        f"  System: {system_assume}",
        f"  Baseline: {baseline:.0f} kWh/year",
        f"  Recoverable today: ~{recoverable}%", "",
    ]

    for label, items, kwh_field in [
        ("🚨 *DO NOW*", recs.get("do_now", []), "estimated_extra_kwh_today"),
        ("📅 *THIS WEEK*", recs.get("do_this_week", []), "estimated_extra_kwh_per_week"),
        ("🏗 *LONG TERM*", recs.get("long_term", []), "estimated_extra_kwh_per_year"),
    ]:
        if items:
            lines.append(label)
            for i, r in enumerate(items, 1):
                kwh = r.get(kwh_field, 0) or 0
                why = r.get("payback_notes" if kwh_field == "estimated_extra_kwh_per_year" else "why_now", "")
                lines += [
                    f"  *{i}. {r.get('action')}*",
                    f"     📈 +{r.get('estimated_gain_percent')}%  ⚡ +{kwh:.1f} kWh",
                    f"     {_effort_emoji(r.get('effort','medium'))} {r.get('effort')}  💰 ₹{r.get('estimated_cost_inr')}",
                    f"     _{why}_", "",
                ]

    lines += ["━━━━━━━━━━━━━━━━━━━━━━━", "_Powered by SolarSense AI · PVGIS + NASA POWER + Open-Meteo + DeepSeek_"]
    return "\n".join(lines)