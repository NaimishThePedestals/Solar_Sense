# """
# graph.py
# --------
# LangGraph workflow: collect -> physics -> analyze -> advise -> END

# physics is a NEW deterministic step (no LLM) that computes ground-truth numbers
# the analyst/advisor are grounded against. Each node is wrapped with live logging.
# """

# import time
# from typing import Any, TypedDict

# from langgraph.graph import END, StateGraph
# from rich.console import Console

# from agents import advisor_agent, analyst_agent, data_collector_agent, physics_agent

# console = Console()


# class SolarState(TypedDict, total=False):
#     lat: float
#     lon: float
#     raw_data: dict[str, Any]
#     errors: list[str]
#     physics: dict[str, Any]
#     analysis: dict[str, Any]
#     analysis_issues: list[str]
#     recommendations: dict[str, Any]
#     advice_issues: list[str]


# def _log_start(step: str, total: int, name: str, emoji: str, desc: str) -> float:
#     console.print()
#     console.rule(f"[bold cyan]{emoji}  STEP {step}/{total} — {name}[/bold cyan]", style="cyan")
#     console.print(f"[dim]→ {desc}[/dim]")
#     return time.time()


# def _log_end(t0: float, summary: str) -> None:
#     console.print(f"[bold green]✓ Done[/bold green] [dim]({time.time() - t0:.2f}s)[/dim] — {summary}")


# def collect_with_logging(state: SolarState) -> SolarState:
#     t0 = _log_start("1", 4, "DATA COLLECTOR", "🛰", "Calling free APIs: PVGIS, Open-Meteo, Air Quality, NASA, Marine")
#     new = data_collector_agent(state)
#     raw, errors = new.get("raw_data", {}), new.get("errors", [])
#     ok = [k for k in ("pvgis", "weather", "air_quality", "nasa_power") if raw.get(k)]
#     if raw.get("marine"):
#         ok.append("marine")
#     console.print(f"  [green]✓ Location:[/green] {raw.get('location_name', 'Unknown')}")
#     console.print(f"  [green]✓ Sources OK:[/green] {', '.join(ok) if ok else 'NONE'}")
#     for err in errors:
#         console.print(f"  [yellow]⚠ {err}[/yellow]")
#     _log_end(t0, f"{len(ok)} sources returned data")
#     return new


# def physics_with_logging(state: SolarState) -> SolarState:
#     t0 = _log_start("2", 4, "PHYSICS ENGINE", "📐", "Computing cell temp & losses in code (Faiman + clear-sky)")
#     new = physics_agent(state)
#     p = new["physics"]
#     console.print(f"  [green]✓ Cell temp:[/green] {p['cell_temp_c']}°C  "
#                   f"[green]Heat:[/green] {p['heat_loss_pct']}%  [green]Soiling:[/green] {p['soiling_loss_pct']}%")
#     console.print(f"  [green]✓ Controllable loss:[/green] {p['controllable_loss_pct']}%  "
#                   f"[dim](score {p['efficiency_score']}/100)[/dim]")
#     console.print(f"  [green]✓ Clear-sky availability:[/green] {p['irradiance_availability_pct']}% "
#                   f"[dim](environmental, not recoverable)[/dim]")
#     _log_end(t0, f"score {p['efficiency_score']}/100, recoverable ~{p['recoverable_today_pct']}%")
#     return new


# def analyze_with_logging(state: SolarState) -> SolarState:
#     t0 = _log_start("3", 4, "ANALYST AGENT", "🔬", "DeepSeek names + explains factors, grounded on the physics")
#     new = analyst_agent(state)
#     a = new.get("analysis", {})
#     console.print(f"  [green]✓ Factors:[/green] {len(a.get('factors', []))}")
#     for issue in new.get("analysis_issues", []):
#         console.print(f"  [yellow]⚠ {issue}[/yellow]")
#     _log_end(t0, f"{len(a.get('factors', []))} factors")
#     return new


# def advise_with_logging(state: SolarState) -> SolarState:
#     t0 = _log_start("4", 4, "ADVISOR AGENT", "💡", "DeepSeek writes ranked actions; validator repairs + caps them")
#     new = advisor_agent(state)
#     r = new.get("recommendations", {})
#     n = len(r.get("do_now", [])) + len(r.get("do_this_week", [])) + len(r.get("long_term", []))
#     console.print(f"  [green]✓ Actions:[/green] {len(r.get('do_now', []))} now · "
#                   f"{len(r.get('do_this_week', []))} week · {len(r.get('long_term', []))} long-term")
#     console.print(f"  [green]✓ Recoverable today:[/green] ~{r.get('todays_recoverable_percent', 0)}%")
#     for issue in new.get("advice_issues", []):
#         console.print(f"  [yellow]⚠ fixed: {issue}[/yellow]")
#     _log_end(t0, f"{n} recommendations ready")
#     console.print()
#     console.rule("[bold green]✓ Pipeline complete — generating report...[/bold green]", style="green")
#     return new


# def build_graph():
#     wf = StateGraph(SolarState)
#     wf.add_node("collect", collect_with_logging)
#     wf.add_node("physics", physics_with_logging)
#     wf.add_node("analyze", analyze_with_logging)
#     wf.add_node("advise", advise_with_logging)
#     wf.set_entry_point("collect")
#     wf.add_edge("collect", "physics")
#     wf.add_edge("physics", "analyze")
#     wf.add_edge("analyze", "advise")
#     wf.add_edge("advise", END)
#     return wf.compile()












"""
graph.py
--------
LangGraph workflow: collect -> physics -> analyze -> advise -> END

physics is a NEW deterministic step (no LLM) that computes ground-truth numbers
the analyst/advisor are grounded against. Each node is wrapped with live logging.
"""

import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from rich.console import Console

from agents import advisor_agent, analyst_agent, data_collector_agent

console = Console()


class SolarState(TypedDict, total=False):
    lat: float
    lon: float
    profile: dict[str, Any]
    raw_data: dict[str, Any]
    errors: list[str]
    analysis: dict[str, Any]
    analysis_issues: list[str]
    analysis_raw: str
    recommendations: dict[str, Any]
    advice_issues: list[str]
    advice_raw: str


def _log_start(step: str, total: int, name: str, emoji: str, desc: str) -> float:
    console.print()
    console.rule(f"[bold cyan]{emoji}  STEP {step}/{total} — {name}[/bold cyan]", style="cyan")
    console.print(f"[dim]→ {desc}[/dim]")
    return time.time()


def _log_end(t0: float, summary: str) -> None:
    console.print(f"[bold green]✓ Done[/bold green] [dim]({time.time() - t0:.2f}s)[/dim] — {summary}")


def collect_with_logging(state: SolarState) -> SolarState:
    t0 = _log_start("1", 4, "DATA COLLECTOR", "🛰", "Calling free APIs: PVGIS, Open-Meteo, Air Quality, NASA, Marine")
    new = data_collector_agent(state)
    raw, errors = new.get("raw_data", {}), new.get("errors", [])
    ok = [k for k in ("pvgis", "weather", "air_quality", "nasa_power") if raw.get(k)]
    if raw.get("marine"):
        ok.append("marine")
    console.print(f"  [green]✓ Location:[/green] {raw.get('location_name', 'Unknown')}")
    console.print(f"  [green]✓ Sources OK:[/green] {', '.join(ok) if ok else 'NONE'}")
    for err in errors:
        console.print(f"  [yellow]⚠ {err}[/yellow]")
    _log_end(t0, f"{len(ok)} sources returned data")
    return new





def analyze_with_logging(state: SolarState) -> SolarState:
    t0 = _log_start("3", 4, "ANALYST AGENT", "🔬", "DeepSeek names + explains factors, grounded on the physics")
    new = analyst_agent(state)
    a = new.get("analysis", {})
    console.print(f"  [green]✓ Factors:[/green] {len(a.get('factors', []))}")
    for issue in new.get("analysis_issues", []):
        console.print(f"  [yellow]⚠ {issue}[/yellow]")
    _log_end(t0, f"{len(a.get('factors', []))} factors")
    return new


def advise_with_logging(state: SolarState) -> SolarState:
    t0 = _log_start("4", 4, "ADVISOR AGENT", "💡", "DeepSeek writes ranked actions; validator repairs + caps them")
    new = advisor_agent(state)
    r = new.get("recommendations", {})
    n = len(r.get("do_now", [])) + len(r.get("do_this_week", [])) + len(r.get("long_term", []))
    console.print(f"  [green]✓ Actions:[/green] {len(r.get('do_now', []))} now · "
                  f"{len(r.get('do_this_week', []))} week · {len(r.get('long_term', []))} long-term")
    console.print(f"  [green]✓ Recoverable today:[/green] ~{r.get('todays_recoverable_percent', 0)}%")
    for issue in new.get("advice_issues", []):
        console.print(f"  [yellow]⚠ fixed: {issue}[/yellow]")
    _log_end(t0, f"{n} recommendations ready")
    console.print()
    console.rule("[bold green]✓ Pipeline complete — generating report...[/bold green]", style="green")
    return new


def build_graph():
    wf = StateGraph(SolarState)
    wf.add_node("collect", collect_with_logging)
    wf.add_node("analyze", analyze_with_logging)
    wf.add_node("advise", advise_with_logging)
    wf.set_entry_point("collect")
    wf.add_edge("collect", "analyze")
    wf.add_edge("analyze", "advise")
    wf.add_edge("advise", END)
    return wf.compile()