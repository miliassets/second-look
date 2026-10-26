"""Physical numbers from tracking frames (2 Hz): top speed, distance, sprints."""
from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..contracts.events import Frame
from ..timeline import to_match_ms

MS_TO_KMH = 3.6


@dataclass
class PhysStat:
    value: float | int
    samples: int  # frames read


class PhysicalEngine:
    def __init__(self, frames: list[Frame], periods: list[dict[str, int]]):
        self.track: dict[str, list[tuple[int, float, float, int]]] = {}
        for f in sorted(frames, key=lambda f: (f.period, f.match_clock_ms)):
            ms = to_match_ms(f.period, f.match_clock_ms, periods)
            for p in f.players:
                self.track.setdefault(p.player_id, []).append((ms, p.x, p.y, f.period))

    def _legs(self, player_id: str, from_ms: int, to_ms: int):
        pts = [p for p in self.track.get(player_id, []) if from_ms <= p[0] <= to_ms]
        for (t0, x0, y0, p0), (t1, x1, y1, p1) in zip(pts, pts[1:], strict=False):
            dt = (t1 - t0) / 1000.0
            if dt <= 0 or p0 != p1 or dt > 2.0:  # never join positions across the half-time swap
                continue
            d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            yield t0, t1, d, d / dt * MS_TO_KMH
        return

    def top_speed_kmh(self, player_id: str, from_ms: int, to_ms: int) -> PhysStat:
        legs = list(self._legs(player_id, from_ms, to_ms))
        return PhysStat(max((v for *_, v in legs), default=0.0), len(legs) + 1 if legs else 0)

    def distance_m(self, player_id: str, from_ms: int, to_ms: int) -> PhysStat:
        legs = list(self._legs(player_id, from_ms, to_ms))
        return PhysStat(sum(d for _, _, d, _ in legs), len(legs) + 1 if legs else 0)

    def sprints(self, player_id: str, from_ms: int, to_ms: int) -> PhysStat:
        """Runs at or above SPRINT_KMH lasting at least SPRINT_MIN_S."""
        n = 0
        run_start: int | None = None
        last_end = 0
        legs = list(self._legs(player_id, from_ms, to_ms))
        for t0, t1, _, v in legs:
            if v >= config.SPRINT_KMH:
                if run_start is None:
                    run_start = t0
                last_end = t1
            else:
                if run_start is not None and (last_end - run_start) / 1000 >= config.SPRINT_MIN_S:
                    n += 1
                run_start = None
        if run_start is not None and (last_end - run_start) / 1000 >= config.SPRINT_MIN_S:
            n += 1
        return PhysStat(n, len(legs) + 1 if legs else 0)

    def metric(self, name: str, player_id: str, from_ms: int, to_ms: int) -> PhysStat:
        return {"top_speed_kmh": self.top_speed_kmh, "distance_m": self.distance_m,
                "sprints": self.sprints}[name](player_id, from_ms, to_ms)
