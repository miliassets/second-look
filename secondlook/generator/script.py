"""Match script: the big beats and the style nudges (synthetic-data-design.md §5.6, §7)."""
from __future__ import annotations

from dataclasses import dataclass, field

SLICE_START_MS = 600_000  # 55:00 (period 2 clock)
SLICE_END_MS = 1_500_000  # 70:00
SUB_AT_MS = 780_000  # 58:00
BEAT_AFTER_MS = 1_280_000  # goal beat starts after 66:20, goal lands ~67:00

DEFAULTS = {"pass_skill": 0.0, "press": 0.12, "tempo": 1.0, "forward": 0.45, "shot": 0.14}


@dataclass
class Window:
    from_ms: int
    to_ms: int
    label: str
    deltas: dict[str, dict[str, float]] = field(default_factory=dict)  # team -> param -> value
    period: int = 2


SLICE_WINDOWS = [
    Window(600_000, 960_000, "even_slow", {
        "KBY": {"tempo": 1.35, "press": 0.08, "forward": 0.30},
        "IRV": {"tempo": 1.35, "press": 0.08, "forward": 0.30}}),
    Window(960_000, 1_380_000, "irv_control", {
        "IRV": {"pass_skill": 0.07, "forward": 0.55, "tempo": 0.9, "press": 0.22},
        "KBY": {"pass_skill": -0.07, "press": 0.05, "tempo": 1.0}}),
    Window(1_380_000, 1_500_000, "even", {
        "KBY": {"tempo": 1.1}, "IRV": {"tempo": 1.1}}),
]


def params(team: str, clock_ms: int, windows: list[Window], style: dict[str, float],
           period: int = 2) -> dict:
    p = dict(DEFAULTS)
    p.update(style)
    for w in windows:
        if w.period == period and w.from_ms <= clock_ms < w.to_ms:
            p.update(w.deltas.get(team, {}))
    return p


def ground_truth_phases() -> list[dict]:
    return [
        {"from_min": 55, "to_min": 60, "label": "even", "note": "slow even rhythm"},
        {"from_min": 60, "to_min": 68, "label": "irv_control",
         "note": "Ironvale take control through the midfield gap"},
        {"from_min": 68, "to_min": 70, "label": "even", "note": "settles"},
    ]
