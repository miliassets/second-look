"""Moment detector: decides when agents run (design §3.2). Deterministic, no AI."""
from __future__ import annotations

from .. import config
from ..contracts.agents_io import Moment
from ..ledger.ingest import IngestResult
from ..ledger.store import LedgerStore
from ..stats.engine import StatsEngine
from ..stats.phases import phase_changes, phase_label

MIN_PHASE_SPACING_MS = 180_000


class MomentDetector:
    def __init__(self, store: LedgerStore, periods: list[dict[str, int]]):
        self.store = store
        self.periods = periods
        self.seen_triggers: set[str] = set()
        self.ids: dict[str, int] = {}
        self.last_phase_check = -10**9
        self.timelines: dict[str, list[tuple[int, str]]] = {}
        self.reported_changes: set[tuple[str, int]] = set()
        self.last_phase_moment_ms = -10**9
        self.last_phase_change_ms = 0
        self.moments: list[Moment] = []

    # ---- id and window helpers
    def _minute(self, period: int, clock_ms: int) -> int:
        """Football minute number: 66:54 is the 67th minute."""
        base = {1: 0, 2: 45, 3: 90, 4: 105}[period]
        return base + clock_ms // 60_000 + 1  # the 67th minute is 66:00-66:59

    def _open(self, kind: str, trigger_id: str, period: int, clock_ms: int, now_ms: int,
              team_id: str | None = None, detail: dict | None = None) -> Moment | None:
        if trigger_id in self.seen_triggers:
            return None
        self.seen_triggers.add(trigger_id)
        base = f"mo-m001-{self._minute(period, clock_ms):03d}-{kind}"
        n = self.ids.get(base, 0) + 1
        self.ids[base] = n
        mid = base if n == 1 else f"{base}-{n}"
        lo = max(now_ms - config.MOMENT_LOOKBACK_MS, self.last_phase_change_ms)
        m = Moment(moment_id=mid, kind=kind, trigger_event_id=trigger_id,
                   window_from_ms=min(lo, now_ms), window_to_ms=now_ms, now_ms=now_ms,
                   as_of_rev=self.store.current_rev(), team_id=team_id, detail=detail or {})
        self.store.put("moment", m)
        self.moments.append(m)
        return m

    # ---- main entry
    def process(self, res: IngestResult) -> list[Moment]:
        if res.duplicate or res.event_id is None:
            return []
        se = self.store.get_event(res.event_id)
        if se is None:
            return []
        e, now = se.event, se.match_ms
        out: list[Moment] = []

        def add(m):
            if m is not None:
                out.append(m)

        if res.kind == "correction":
            q = e.qualifiers
            target = self.store.get_event(q.get("corrects", ""))
            if q.get("kind") == "void" and target is not None and target.event.type == "goal":
                add(self._open("disallowed_goal", e.event_id, e.period, e.match_clock_ms, now,
                               team_id=target.event.team_id))
            return out
        if e.type == "goal":
            add(self._open("goal", e.event_id, e.period, e.match_clock_ms, now, e.team_id))
        elif e.type == "shot" and e.outcome in ("saved", "scored"):
            if float(e.qualifiers.get("speed_kmh", 0)) >= config.SHOT_SPEED_MOMENT_KMH \
                    and e.outcome != "scored":
                add(self._open("fast_shot", e.event_id, e.period, e.match_clock_ms, now,
                               e.team_id))
        elif e.type == "card":
            add(self._open("card", e.event_id, e.period, e.match_clock_ms, now, e.team_id))
        elif e.type == "tactical_shift":
            add(self._open("tactical_shift", e.event_id, e.period, e.match_clock_ms, now,
                           e.team_id))
        elif e.type == "period_end" and e.period == 1:
            add(self._open("half_time", e.event_id, e.period, e.match_clock_ms, now))
        elif e.type == "match_end":
            add(self._open("full_time", e.event_id, e.period, e.match_clock_ms, now))
        if e.type not in ("match_metadata", "lineup"):
            for m in self._phase_check(se):
                out.append(m)
        return out

    def _phase_check(self, se) -> list[Moment]:
        now = se.match_ms
        if now - self.last_phase_check < config.PHASE_STEP_MS:
            return []
        self.last_phase_check = now
        stats = StatsEngine.from_store(self.store, to_ms=now)
        teams = [t for t in stats.teams]
        labels = {}
        for t in teams:
            lbl = phase_label(stats, t, now - config.PHASE_WINDOW_MS, now)
            self.timelines.setdefault(t, []).append((now, lbl))
            labels[t] = lbl
        out = []
        for t in teams:
            for at, lbl in phase_changes(self.timelines[t]):
                key = (t, at)
                if key in self.reported_changes:
                    continue
                self.reported_changes.add(key)
                notable = lbl == "control" or (lbl == "chaos" and all(
                    v == "chaos" for v in labels.values()))
                if not notable or at - self.last_phase_moment_ms < MIN_PHASE_SPACING_MS:
                    continue
                self.last_phase_moment_ms = at
                self.last_phase_change_ms = at - config.PHASE_WINDOW_MS
                e = se.event
                m = self._open("phase_change", f"phase:{t}:{at}", e.period, e.match_clock_ms, now,
                               team_id=t, detail={"label": lbl, "since_ms": self.last_phase_change_ms})
                if m:
                    out.append(m)
        return out
