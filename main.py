# """
# main.py
# -------
# Terminal entry point. Asks for lat/lon, runs the graph, prints a Rich report.

# Usage:
#     python main.py
#     python main.py 23.0258 72.5873   # Ahmedabad
# """

# import sys

# from dotenv import load_dotenv
# from rich.console import Console
# from rich.panel import Panel
# from rich.table import Table

# from graph import build_graph
# from tools import geocode_place

# load_dotenv()
# console = Console()

# SEVERITY_COLORS = {"low": "green", "medium": "yellow", "high": "red"}
# EFFORT_COLORS = {"low": "green", "medium": "yellow", "high": "red"}


# def _parse_coords(text: str) -> tuple[float, float] | None:
#     """Return (lat, lon) if text is a valid coordinate pair, else None."""
#     parts = [p for p in text.replace(",", " ").split() if p]
#     if len(parts) != 2:
#         return None
#     try:
#         lat, lon = float(parts[0]), float(parts[1])
#     except ValueError:
#         return None
#     if -90 <= lat <= 90 and -180 <= lon <= 180:
#         return lat, lon
#     return None


# def _choose_candidate(candidates: list[dict]) -> dict | None:
#     """Show geocoding matches and let the user pick — prevents running on the wrong place."""
#     if not candidates:
#         return None
#     if len(candidates) == 1:
#         c = candidates[0]
#         console.print(f"[green]✓ Found:[/green] {c['display_name']}  "
#                       f"[dim]({c['lat']:.4f}, {c['lon']:.4f})[/dim]")
#         return c
#     console.print("\n[bold]Multiple matches — which one?[/bold]")
#     for i, c in enumerate(candidates, 1):
#         console.print(f"  [cyan]{i}[/cyan]. {c['display_name']}  "
#                       f"[dim]({c['lat']:.4f}, {c['lon']:.4f})[/dim]")
#     while True:
#         choice = console.input("[bold]Pick a number (or Enter for 1):[/bold] ").strip()
#         if choice == "":
#             return candidates[0]
#         if choice.isdigit() and 1 <= int(choice) <= len(candidates):
#             return candidates[int(choice) - 1]
#         console.print("[red]Invalid choice — try again.[/red]")


# def resolve_location(text: str) -> tuple[float, float]:
#     """Accept either 'lat, lon' or a place name/address; return validated coordinates."""
#     coords = _parse_coords(text)
#     if coords:
#         return coords
#     console.print(f"[dim]Looking up “{text}”…[/dim]")
#     chosen = _choose_candidate(geocode_place(text))
#     if not chosen:
#         raise ValueError(f"Could not find a location for “{text}”. "
#                          f"Try adding more detail (e.g. 'Limbdi, Gujarat, India') or enter coordinates.")
#     return chosen["lat"], chosen["lon"]


# def get_coords() -> tuple[float, float]:
#     # CLI args: either two numbers (lat lon) or a place name ("Limbdi Gujarat")
#     if len(sys.argv) >= 2:
#         joined = " ".join(sys.argv[1:])
#         return resolve_location(joined)
#     console.print("[bold cyan]☀  SolarSense AI — Solar Panel Efficiency Advisor[/bold cyan]\n")
#     console.print("[dim]Enter a place name/address (e.g. 'Limbdi, Gujarat') or coordinates "
#                   "('23.02, 72.58').[/dim]")
#     while True:
#         text = console.input("[bold]Location:[/bold] ").strip()
#         if not text:
#             console.print("[red]Please enter something.[/red]")
#             continue
#         try:
#             return resolve_location(text)
#         except ValueError as e:
#             console.print(f"[red]{e}[/red]")


# def score_color(score: int) -> str:
#     return "green" if score >= 75 else "yellow" if score >= 50 else "red"


# def render_report(final_state: dict) -> None:
#     raw = final_state.get("raw_data", {})
#     analysis = final_state.get("analysis", {})
#     physics = final_state.get("physics", {})
#     recs = final_state.get("recommendations", {})
#     errors = final_state.get("errors", [])

#     console.print()
#     location = raw.get("location_name", "Unknown")
#     console.print(Panel.fit(
#         f"[bold white]☀  Today's Solar Efficiency Snapshot[/bold white]\n[dim]{location}[/dim]\n"
#         f"[dim]Coordinates: {final_state['lat']}, {final_state['lon']}[/dim]",
#         border_style="cyan",
#     ))

#     if errors:
#         console.print(Panel("\n".join(f"• {e}" for e in errors),
#                             title="[yellow]⚠  Data Source Warnings[/yellow]", border_style="yellow"))

#     # Efficiency score (controllable health) + environmental context line
#     score = analysis.get("todays_efficiency_score", physics.get("efficiency_score", 0))
#     loss = analysis.get("todays_estimated_loss_percent", physics.get("controllable_loss_pct", 0))
#     avail = physics.get("irradiance_availability_pct")
#     color = score_color(score)
#     bar = "█" * (score // 5) + "░" * (20 - score // 5)
#     env_line = ""
#     if avail is not None:
#         env_line = (f"\n[dim]Sky today: delivering {avail}% of clear-sky potential "
#                     f"(weather — not recoverable)[/dim]")
#     console.print(Panel(
#         f"[bold {color}]{score}/100[/bold {color}]   [{color}]{bar}[/{color}]"
#         f"     [dim](controllable losses ~{loss}% right now)[/dim]{env_line}\n\n"
#         f"{analysis.get('todays_summary', '')}",
#         title="[bold]🔴 Live Efficiency — Right Now[/bold]", border_style=color,
#     ))

#     # PVGIS
#     pvgis = raw.get("pvgis") or {}
#     if pvgis:
#         t = Table(title="📊 PVGIS Baseline (1 kWp, optimal tilt)", show_header=False, border_style="blue")
#         t.add_column(style="cyan"); t.add_column(style="white")
#         t.add_row("Annual yield", f"{pvgis.get('annual_yield_kwh_per_kwp')} kWh/year per kWp")
#         t.add_row("Optimal tilt", f"{pvgis.get('optimal_tilt_deg')}°")
#         t.add_row("Optimal azimuth", f"{pvgis.get('optimal_azimuth_deg')}° (0=south)")
#         t.add_row("Elevation", f"{pvgis.get('elevation_m')} m")
#         console.print(t)

#     # Current conditions
#     weather = raw.get("weather") or {}
#     aq = raw.get("air_quality") or {}
#     if weather or aq:
#         t = Table(title="🌤  Current Conditions & Air Quality (RIGHT NOW)", show_header=False, border_style="blue")
#         t.add_column(style="cyan"); t.add_column(style="white")
#         if weather:
#             t.add_row("Temperature", f"{weather.get('current_temp_c')} °C")
#             if physics.get("cell_temp_c") is not None:
#                 t.add_row("Est. cell temp", f"{physics.get('cell_temp_c')} °C")
#             t.add_row("Cloud cover", f"{weather.get('current_cloud_cover_percent')}%")
#             t.add_row("Wind speed", f"{weather.get('current_wind_speed_m_s')} m/s")
#             t.add_row("Live shortwave radiation", f"{weather.get('current_shortwave_radiation_w_m2')} W/m²")
#         if aq:
#             t.add_row("PM2.5", f"{aq.get('pm2_5_ug_m3')} µg/m³")
#             t.add_row("PM10", f"{aq.get('pm10_ug_m3')} µg/m³")
#         console.print(t)

#     # Factors
#     factors = analysis.get("factors", [])
#     if factors:
#         t = Table(title="🔬 What's Hurting Your Output RIGHT NOW (controllable)", border_style="magenta")
#         t.add_column("Factor", style="bold"); t.add_column("Severity")
#         t.add_column("Today's value", style="dim"); t.add_column("Impact", justify="right")
#         t.add_column("Why", style="dim")
#         for f in factors:
#             sev = f.get("severity", "low")
#             sc = SEVERITY_COLORS.get(sev, "white")
#             t.add_row(f.get("name", ""), f"[{sc}]{sev.upper()}[/{sc}]",
#                       str(f.get("current_value", "")), f"-{f.get('todays_impact_percent', 0)}%",
#                       f.get("explanation", ""))
#         console.print(t)

#     # Action plan
#     baseline = recs.get("annual_baseline_kwh", 0)
#     recoverable = recs.get("todays_recoverable_percent", 0)
#     console.print(Panel(
#         f"[bold]System assumption:[/bold] {recs.get('system_assumption', 'N/A')}\n"
#         f"[bold]Annual baseline yield:[/bold] {baseline:.0f} kWh/year\n"
#         f"[bold green]Recoverable from today's controllable loss:[/bold green] ~{recoverable}%",
#         title="[green]💡 Action Plan[/green]", border_style="green",
#     ))

#     def _group(title: str, items: list, border: str, kwh_field: str, kwh_label: str, is_year: bool) -> None:
#         if not items:
#             return
#         t = Table(title=title, border_style=border)
#         t.add_column("#", justify="right", style="bold"); t.add_column("Action", style="white")
#         t.add_column("Gain", justify="right", style="green"); t.add_column(kwh_label, justify="right", style="cyan")
#         t.add_column("Effort"); t.add_column("Cost (INR)", style="yellow")
#         why_field = "payback_notes" if is_year else "why_now"
#         t.add_column("Payback" if is_year else "Why now", style="dim")
#         for i, r in enumerate(items, 1):
#             eff = r.get("effort", "medium")
#             kwh = r.get(kwh_field, 0) or 0
#             kwh_str = f"{kwh:.0f}" if is_year else f"{kwh:.1f}"   # FIX: .1f so sub-1 kWh isn't shown as "0"
#             ec = EFFORT_COLORS.get(eff, "white")
#             t.add_row(str(i), r.get("action", ""), f"+{r.get('estimated_gain_percent', 0)}%",
#                       kwh_str, f"[{ec}]{eff}[/{ec}]", str(r.get("estimated_cost_inr", "")), r.get(why_field, ""))
#         console.print(t)

#     _group("🚨 DO NOW — Recover power today", recs.get("do_now", []), "red",
#            "estimated_extra_kwh_today", "Extra kWh today", False)
#     _group("📅 THIS WEEK — Quick wins", recs.get("do_this_week", []), "yellow",
#            "estimated_extra_kwh_per_week", "Extra kWh/week", False)
#     _group("🏗  LONG-TERM — Bigger upgrades", recs.get("long_term", []), "blue",
#            "estimated_extra_kwh_per_year", "Extra kWh/year", True)

#     console.print("\n[dim]Report by SolarSense AI · DeepSeek + physics engine + PVGIS + NASA POWER + Open-Meteo[/dim]\n")


# def render_debug(final_state: dict) -> None:
#     """Dump RAW DeepSeek output (before validation) next to the fixes applied.
#     This is what to copy/paste when asking for a behavior audit."""
#     console.print()
#     console.rule("[bold magenta]🐛 DEBUG — raw LLM output before validation[/bold magenta]", style="magenta")

#     console.print(Panel(
#         final_state.get("analysis_raw", "(none)"),
#         title="[magenta]1) ANALYST — raw DeepSeek response[/magenta]",
#         border_style="magenta", expand=True,
#     ))
#     afix = final_state.get("analysis_issues", [])
#     console.print(Panel(
#         "\n".join(f"• {x}" for x in afix) if afix else "[green]No repairs needed.[/green]",
#         title="[magenta]   ANALYST — validator repairs[/magenta]", border_style="dim magenta",
#     ))

#     console.print(Panel(
#         final_state.get("advice_raw", "(none)"),
#         title="[magenta]2) ADVISOR — raw DeepSeek response[/magenta]",
#         border_style="magenta", expand=True,
#     ))
#     dfix = final_state.get("advice_issues", [])
#     console.print(Panel(
#         "\n".join(f"• {x}" for x in dfix) if dfix else "[green]No repairs needed.[/green]",
#         title="[magenta]   ADVISOR — validator repairs[/magenta]", border_style="dim magenta",
#     ))
#     console.rule("[dim magenta]end debug[/dim magenta]", style="magenta")


# def main():
#     debug = "--debug" in sys.argv
#     if debug:
#         sys.argv = [a for a in sys.argv if a != "--debug"]
#     lat, lon = get_coords()
#     final_state = build_graph().invoke({"lat": lat, "lon": lon})
#     render_report(final_state)
#     if debug:
#         render_debug(final_state)


# if __name__ == "__main__":
#     main()





















"""
main.py
-------
Terminal entry point. Asks for lat/lon, runs the graph, prints a Rich report.

Usage:
    python main.py
    python main.py 23.0258 72.5873   # Ahmedabad
"""

import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from graph import build_graph
from tools import geocode_place

load_dotenv()
console = Console()

SEVERITY_COLORS = {"low": "green", "medium": "yellow", "high": "red"}
EFFORT_COLORS = {"low": "green", "medium": "yellow", "high": "red"}


def _parse_coords(text: str) -> tuple[float, float] | None:
    """Return (lat, lon) if text is a valid coordinate pair, else None."""
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _choose_candidate(candidates: list[dict]) -> dict | None:
    """Show geocoding matches and let the user pick — prevents running on the wrong place."""
    if not candidates:
        return None
    if len(candidates) == 1:
        c = candidates[0]
        console.print(f"[green]✓ Found:[/green] {c['display_name']}  "
                      f"[dim]({c['lat']:.4f}, {c['lon']:.4f})[/dim]")
        return c
    console.print("\n[bold]Multiple matches — which one?[/bold]")
    for i, c in enumerate(candidates, 1):
        console.print(f"  [cyan]{i}[/cyan]. {c['display_name']}  "
                      f"[dim]({c['lat']:.4f}, {c['lon']:.4f})[/dim]")
    while True:
        choice = console.input("[bold]Pick a number (or Enter for 1):[/bold] ").strip()
        if choice == "":
            return candidates[0]
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
        console.print("[red]Invalid choice — try again.[/red]")


def _query_variants(text: str) -> list[str]:
    """Progressively broader queries so a too-specific address still resolves.
    'shivji Nagar veraval' -> ['shivji Nagar veraval', 'Nagar veraval', 'veraval']
    Generic (no country hard-coding): assumes the city tends to come last."""
    text = text.strip()
    seen: list[str] = []

    def add(q: str) -> None:
        q = q.strip(" ,")
        if q and q.lower() not in (s.lower() for s in seen):
            seen.append(q)

    add(text)
    if "," in text:  # drop leading comma-segments (most specific first)
        parts = [p.strip() for p in text.split(",") if p.strip()]
        for i in range(1, len(parts)):
            add(", ".join(parts[i:]))
    words = text.replace(",", " ").split()  # then drop leading words
    for i in range(1, len(words)):
        add(" ".join(words[i:]))
    return seen


def resolve_location(text: str) -> tuple[float, float]:
    """Accept either 'lat, lon' or a place name/address; return validated coordinates.
    Falls back to broader queries when a hyperlocal address has no exact match."""
    coords = _parse_coords(text)
    if coords:
        return coords

    console.print(f"[dim]Looking up “{text}”…[/dim]")
    variants = _query_variants(text)
    for q in variants:
        try:
            candidates = geocode_place(q)
        except Exception as e:
            raise ValueError(
                f"Location lookup service is unreachable ({e}). "
                f"Check your internet connection, or enter coordinates instead "
                f"(e.g. '20.9077 70.3673')."
            )
        if candidates:
            if q.lower() != text.lower():
                console.print(f"[yellow]No exact match for “{text}” — showing the closest "
                              f"broader area “{q}”.[/yellow]")
            chosen = _choose_candidate(candidates)
            if chosen:
                return chosen["lat"], chosen["lon"]

    raise ValueError(
        f"Could not find “{text}”. Try a broader place (e.g. just the city + state + "
        f"country like 'Veraval, Gujarat, India'), or enter coordinates (e.g. '20.9077 70.3673')."
    )


def get_coords() -> tuple[float, float]:
    # CLI args: either two numbers (lat lon) or a place name ("Limbdi Gujarat")
    if len(sys.argv) >= 2:
        joined = " ".join(sys.argv[1:])
        return resolve_location(joined)
    console.print("[bold cyan]☀  SolarSense AI — Solar Panel Efficiency Advisor[/bold cyan]\n")
    console.print("[dim]Enter a place name/address (e.g. 'Limbdi, Gujarat') or coordinates "
                  "('23.02, 72.58').[/dim]")
    while True:
        text = console.input("[bold]Location:[/bold] ").strip()
        if not text:
            console.print("[red]Please enter something.[/red]")
            continue
        try:
            return resolve_location(text)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")


def score_color(score: int) -> str:
    return "green" if score >= 75 else "yellow" if score >= 50 else "red"


def render_report(final_state: dict) -> None:
    raw = final_state.get("raw_data", {})
    analysis = final_state.get("analysis", {})
    physics = final_state.get("physics", {})
    recs = final_state.get("recommendations", {})
    errors = final_state.get("errors", [])

    console.print()
    location = raw.get("location_name", "Unknown")
    console.print(Panel.fit(
        f"[bold white]☀  Today's Solar Efficiency Snapshot[/bold white]\n[dim]{location}[/dim]\n"
        f"[dim]Coordinates: {final_state['lat']}, {final_state['lon']}[/dim]",
        border_style="cyan",
    ))

    if errors:
        console.print(Panel("\n".join(f"• {e}" for e in errors),
                            title="[yellow]⚠  Data Source Warnings[/yellow]", border_style="yellow"))

    # Efficiency score (controllable health) + environmental context line
    score = analysis.get("todays_efficiency_score", physics.get("efficiency_score", 0))
    loss = analysis.get("todays_estimated_loss_percent", physics.get("controllable_loss_pct", 0))
    avail = physics.get("irradiance_availability_pct")
    color = score_color(score)
    bar = "█" * (score // 5) + "░" * (20 - score // 5)
    env_line = ""
    if avail is not None:
        env_line = (f"\n[dim]Sky today: delivering {avail}% of clear-sky potential "
                    f"(weather — not recoverable)[/dim]")
    console.print(Panel(
        f"[bold {color}]{score}/100[/bold {color}]   [{color}]{bar}[/{color}]"
        f"     [dim](controllable losses ~{loss}% right now)[/dim]{env_line}\n\n"
        f"{analysis.get('todays_summary', '')}",
        title="[bold]🔴 Live Efficiency — Right Now[/bold]", border_style=color,
    ))

    # PVGIS
    pvgis = raw.get("pvgis") or {}
    if pvgis:
        t = Table(title="📊 PVGIS Baseline (1 kWp, optimal tilt)", show_header=False, border_style="blue")
        t.add_column(style="cyan"); t.add_column(style="white")
        t.add_row("Annual yield", f"{pvgis.get('annual_yield_kwh_per_kwp')} kWh/year per kWp")
        t.add_row("Optimal tilt", f"{pvgis.get('optimal_tilt_deg')}°")
        t.add_row("Optimal azimuth", f"{pvgis.get('optimal_azimuth_deg')}° (0=south)")
        t.add_row("Elevation", f"{pvgis.get('elevation_m')} m")
        console.print(t)

    # Current conditions
    weather = raw.get("weather") or {}
    aq = raw.get("air_quality") or {}
    if weather or aq:
        t = Table(title="🌤  Current Conditions & Air Quality (RIGHT NOW)", show_header=False, border_style="blue")
        t.add_column(style="cyan"); t.add_column(style="white")
        if weather:
            t.add_row("Temperature", f"{weather.get('current_temp_c')} °C")
            if physics.get("cell_temp_c") is not None:
                t.add_row("Est. cell temp", f"{physics.get('cell_temp_c')} °C")
            t.add_row("Cloud cover", f"{weather.get('current_cloud_cover_percent')}%")
            t.add_row("Wind speed", f"{weather.get('current_wind_speed_m_s')} m/s")
            t.add_row("Live shortwave radiation", f"{weather.get('current_shortwave_radiation_w_m2')} W/m²")
        if aq:
            t.add_row("PM2.5", f"{aq.get('pm2_5_ug_m3')} µg/m³")
            t.add_row("PM10", f"{aq.get('pm10_ug_m3')} µg/m³")
        console.print(t)

    # Factors
    factors = analysis.get("factors", [])
    if factors:
        t = Table(title="🔬 What's Hurting Your Output RIGHT NOW (controllable)", border_style="magenta")
        t.add_column("Factor", style="bold"); t.add_column("Severity")
        t.add_column("Today's value", style="dim"); t.add_column("Impact", justify="right")
        t.add_column("Why", style="dim")
        for f in factors:
            sev = f.get("severity", "low")
            sc = SEVERITY_COLORS.get(sev, "white")
            t.add_row(f.get("name", ""), f"[{sc}]{sev.upper()}[/{sc}]",
                      str(f.get("current_value", "")), f"-{f.get('todays_impact_percent', 0)}%",
                      f.get("explanation", ""))
        console.print(t)

    # Action plan
    baseline = recs.get("annual_baseline_kwh", 0)
    recoverable = recs.get("todays_recoverable_percent", 0)
    console.print(Panel(
        f"[bold]System assumption:[/bold] {recs.get('system_assumption', 'N/A')}\n"
        f"[bold]Annual baseline yield:[/bold] {baseline:.0f} kWh/year\n"
        f"[bold green]Recoverable from today's controllable loss:[/bold green] ~{recoverable}%",
        title="[green]💡 Action Plan[/green]", border_style="green",
    ))

    def _group(title: str, items: list, border: str, kwh_field: str, kwh_label: str, is_year: bool) -> None:
        if not items:
            return
        t = Table(title=title, border_style=border)
        t.add_column("#", justify="right", style="bold"); t.add_column("Action", style="white")
        t.add_column("Gain", justify="right", style="green"); t.add_column(kwh_label, justify="right", style="cyan")
        t.add_column("Effort"); t.add_column("Cost (INR)", style="yellow")
        why_field = "payback_notes" if is_year else "why_now"
        t.add_column("Payback" if is_year else "Why now", style="dim")
        for i, r in enumerate(items, 1):
            eff = r.get("effort", "medium")
            marginal = r.get("confidence") == "marginal" or not r.get("estimated_gain_percent")
            kwh = r.get(kwh_field)
            if marginal:
                gain_str, kwh_str = "marginal", "—"
            else:
                kwh = kwh or 0
                kwh_str = f"{kwh:.0f}" if is_year else f"{kwh:.1f}"
                gain_str = f"+{r.get('estimated_gain_percent', 0)}%"
            ec = EFFORT_COLORS.get(eff, "white")
            t.add_row(str(i), r.get("action", ""), gain_str,
                      kwh_str, f"[{ec}]{eff}[/{ec}]", str(r.get("estimated_cost_inr", "")), r.get(why_field, ""))
        console.print(t)

    _group("🚨 DO NOW — Recover power today", recs.get("do_now", []), "red",
           "estimated_extra_kwh_today", "Extra kWh today", False)
    _group("📅 THIS WEEK — Quick wins", recs.get("do_this_week", []), "yellow",
           "estimated_extra_kwh_per_week", "Extra kWh/week", False)
    _group("🏗  LONG-TERM — Bigger upgrades", recs.get("long_term", []), "blue",
           "estimated_extra_kwh_per_year", "Extra kWh/year", True)

    console.print("\n[dim]Report by SolarSense AI · DeepSeek + physics engine + PVGIS + NASA POWER + Open-Meteo[/dim]\n")


def render_debug(final_state: dict) -> None:
    """Dump RAW DeepSeek output (before validation) next to the fixes applied.
    This is what to copy/paste when asking for a behavior audit."""
    console.print()
    console.rule("[bold magenta]🐛 DEBUG — raw LLM output before validation[/bold magenta]", style="magenta")

    console.print(Panel(
        final_state.get("analysis_raw", "(none)"),
        title="[magenta]1) ANALYST — raw DeepSeek response[/magenta]",
        border_style="magenta", expand=True,
    ))
    afix = final_state.get("analysis_issues", [])
    console.print(Panel(
        "\n".join(f"• {x}" for x in afix) if afix else "[green]No repairs needed.[/green]",
        title="[magenta]   ANALYST — validator repairs[/magenta]", border_style="dim magenta",
    ))

    console.print(Panel(
        final_state.get("advice_raw", "(none)"),
        title="[magenta]2) ADVISOR — raw DeepSeek response[/magenta]",
        border_style="magenta", expand=True,
    ))
    dfix = final_state.get("advice_issues", [])
    console.print(Panel(
        "\n".join(f"• {x}" for x in dfix) if dfix else "[green]No repairs needed.[/green]",
        title="[magenta]   ADVISOR — validator repairs[/magenta]", border_style="dim magenta",
    ))
    console.rule("[dim magenta]end debug[/dim magenta]", style="magenta")


def main():
    debug = "--debug" in sys.argv
    if debug:
        sys.argv = [a for a in sys.argv if a != "--debug"]
    lat, lon = get_coords()
    final_state = build_graph().invoke({"lat": lat, "lon": lon})
    render_report(final_state)
    if debug:
        render_debug(final_state)


if __name__ == "__main__":
    main()