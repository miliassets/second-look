"""One global timeline: match_ms = sum of earlier periods' actual lengths + match_clock_ms."""
from __future__ import annotations

from collections.abc import Sequence


def period_offsets(periods: Sequence[dict[str, int]]) -> dict[int, int]:
    out: dict[int, int] = {}
    acc = 0
    for p in sorted(periods, key=lambda p: p["period"]):
        out[p["period"]] = acc
        acc += p["length_ms"]
    return out


def to_match_ms(period: int, clock_ms: int, periods: Sequence[dict[str, int]]) -> int:
    return period_offsets(periods)[period] + clock_ms


def from_match_ms(match_ms: int, periods: Sequence[dict[str, int]]) -> tuple[int, int]:
    offs = period_offsets(periods)
    best = min(offs)
    for p, o in offs.items():
        if match_ms >= o:
            best = p
    return best, match_ms - offs[best]


def format_clock(period: int, clock_ms: int) -> str:
    """Display clock: 67:12; stoppage shown as 45+2:10 / 90+3:05 when over the period's nominal end."""
    base_min = {1: 0, 2: 45, 3: 90, 4: 105}[period]
    nominal = 45 if period <= 2 else 15
    total_s = clock_ms // 1000
    mins, secs = divmod(total_s, 60)
    if mins >= nominal:
        return f"{base_min + nominal}+{mins - nominal}:{secs:02d}"
    return f"{base_min + mins}:{secs:02d}"
