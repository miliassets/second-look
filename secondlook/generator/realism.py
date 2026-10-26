"""Realism checks (synthetic-data-design.md §6). Ranges are typical professional totals for a
whole match, both teams combined unless noted; they are `UNVERIFIED` until cited in the README."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from ..ledger.store import StoredEvent
from ..stats.engine import StatsEngine
from ..timeline import to_match_ms
from . import GeneratedMatch

RANGES = {
    "passes": (800, 1100), "pass_completion_pct": (75, 88), "shots": (18, 32), "goals": (0, 7),
    "corners": (6, 14), "fouls": (16, 28), "yellow_cards": (1, 6),
    "possession_min_pct": (35, 50), "possession_max_pct": (50, 65),
    "distance_km": (9, 13), "shot_speed_min_kmh": (50, 200), "shot_speed_max_kmh": (0, 120),
}


@dataclass
class Report:
    seed: int
    measures: dict[str, float]
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def measure(m: GeneratedMatch, with_distance: bool = True) -> dict[str, float]:
    periods = m.metadata.periods
    evs = [e for e in m.events if e.type != "event_correction"]
    voided = {e.qualifiers["corrects"] for e in m.events if e.type == "event_correction"
              and e.qualifiers.get("kind") == "void"}
    passes = [e for e in evs if e.type == "pass"]
    out: dict[str, float] = {
        "passes": len(passes),
        "pass_completion_pct": 100 * sum(1 for e in passes if e.outcome == "complete")
        / max(1, len(passes)),
        "shots": sum(1 for e in evs if e.type == "shot"),
        "goals": sum(1 for e in evs if e.type == "goal" and e.event_id not in voided),
        "corners": sum(1 for e in evs if e.type == "corner"),
        "fouls": sum(1 for e in evs if e.type == "foul"),
        "yellow_cards": sum(1 for e in evs if e.type == "card" and e.outcome == "yellow"),
    }
    speeds = [float(e.qualifiers["speed_kmh"]) for e in evs if e.type == "shot"]
    out["shot_speed_min_kmh"], out["shot_speed_max_kmh"] = min(speeds), max(speeds)
    stored = [StoredEvent(event=e, match_ms=to_match_ms(e.period, e.match_clock_ms, periods))
              for e in evs]
    st = StatsEngine(stored)
    total = periods[0]["length_ms"] + periods[1]["length_ms"]
    shares = [st.rate("possession_share", t, 0, total).value for t in ("KBY", "IRV")]
    out["possession_min_pct"], out["possession_max_pct"] = min(shares), max(shares)
    if with_distance:
        dist: dict[str, float] = {}
        last: dict[str, tuple[float, float]] = {}
        frames_seen: dict[str, int] = {}
        for f in m.frames:
            for p in f.players:
                frames_seen[p.player_id] = frames_seen.get(p.player_id, 0) + 1
                if p.player_id in last:
                    dist[p.player_id] = dist.get(p.player_id, 0.0) + math.hypot(
                        p.x - last[p.player_id][0], p.y - last[p.player_id][1])
                last[p.player_id] = (p.x, p.y)
        n = max(frames_seen.values())
        gk = {p.player_id for p in m.metadata.players if p.role == "GK"}
        full = [d for pid, d in dist.items() if frames_seen[pid] >= 0.97 * n and pid not in gk]
        if full:
            out["distance_km"] = statistics.mean(full) / 1000
    return out


def check(seed: int, measures: dict[str, float]) -> Report:
    r = Report(seed, measures)
    for k, (lo, hi) in RANGES.items():
        if k in measures and not lo <= measures[k] <= hi:
            r.failures.append(f"{k}={measures[k]:.1f} outside {lo}-{hi}")
    return r


def report_markdown(reports: list[Report], title: str = "Realism report") -> str:
    n = len(reports)
    ok = sum(1 for r in reports if r.ok)
    lines = [f"# {title}", "", f"{ok} of {n} seeds pass every range (ranges `UNVERIFIED`; "
             "typical whole-match totals, both teams).", "",
             "| Measure | Range | Mean | Min | Max | Seeds outside |", "| :- | :- | -: | -: | -: | -: |"]
    for k, (lo, hi) in RANGES.items():
        vals = [r.measures[k] for r in reports if k in r.measures]
        if not vals:
            continue
        out = sum(1 for v in vals if not lo <= v <= hi)
        lines.append(f"| {k} | {lo}-{hi} | {statistics.mean(vals):.1f} | {min(vals):.1f} | "
                     f"{max(vals):.1f} | {out} |")
    goals = [r.measures["goals"] for r in reports]
    lines += ["", f"Goals per match: mean {statistics.mean(goals):.2f} over {n} seeds "
              "(target about 2.7).", "", "## Failing seeds", ""]
    bad = [r for r in reports if not r.ok]
    lines += [f"- seed {r.seed}: {'; '.join(r.failures)}" for r in bad[:30]] or ["- none"]
    return "\n".join(lines) + "\n"
