"""Delivery faults: late, duplicate and gapped events (synthetic-data-design.md §8.4)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts.events import Delivery, Event
from ..timeline import to_match_ms

NEVER_FAULT = {"match_metadata", "lineup"}


@dataclass(frozen=True)
class FaultConfig:
    late_rate: float = 0.0
    dup_rate: float = 0.0
    gap_rate: float = 0.0
    late_ms: tuple[int, int] = (1000, 4000)
    dup_ms: tuple[int, int] = (500, 3000)
    gap_ms: tuple[int, int] = (5000, 15000)  # a gapped event is withheld, then delivered


PRESETS = {
    "none": FaultConfig(),
    "demo": FaultConfig(late_rate=0.04, dup_rate=0.02, gap_rate=0.006),
    "heavy": FaultConfig(late_rate=0.15, dup_rate=0.08, gap_rate=0.03),
}


def apply_faults(events: list[Event], periods: list[dict[str, int]], cfg: FaultConfig,
                 seed: int) -> list[Delivery]:
    rng = np.random.Generator(np.random.PCG64(seed + 2))
    items: list[tuple[int, int, int, Event, str]] = []  # arrival_ms, tiebreak, seq, event, kind
    for e in events:
        base = to_match_ms(e.period, e.match_clock_ms, periods)
        kind, arrival = "normal", base
        if e.type not in NEVER_FAULT and e.type != "event_correction":
            r = rng.random()
            if r < cfg.gap_rate:
                kind, arrival = "late", base + int(rng.integers(*cfg.gap_ms))
            elif r < cfg.gap_rate + cfg.late_rate:
                kind, arrival = "late", base + int(rng.integers(*cfg.late_ms))
            if rng.random() < cfg.dup_rate:
                items.append((base + int(rng.integers(*cfg.dup_ms)) + (arrival - base), 1,
                              e.seq, e, "duplicate"))
        items.append((arrival, 0, e.seq, e, kind))
    items.sort(key=lambda it: (it[0], it[1], it[2]))
    return [Delivery(event=e, arrival_order=i, arrival_match_ms=a, kind=k)
            for i, (a, _, _, e, k) in enumerate(items)]
