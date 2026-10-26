"""Deterministic stats over ledger events. Plain code, no AI.

Every query returns a value plus the event ids it read, so the checker can record dependencies.
Windows are inclusive on the global `match_ms` timeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config
from ..ledger.store import LedgerStore, StoredEvent

ON_BALL = {"pass", "carry", "dribble", "cross", "shot", "goal", "corner", "free_kick",
           "throw_in", "goal_kick", "penalty", "kickoff", "possession_change"}
RECOVERY_TYPES = {"tackle", "interception"}


@dataclass
class Stat:
    value: float | int | None
    event_ids: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)  # types the window dependency covers


def other(team: str, teams: tuple[str, ...]) -> str | None:
    rest = [t for t in teams if t != team]
    return rest[0] if rest else None


TYPES_FOR = {
    "passes": ["pass"], "completed_passes": ["pass"],
    "recoveries": sorted(RECOVERY_TYPES), "recoveries_att_third": sorted(RECOVERY_TYPES),
    "pressures": ["pressure"], "forced_turnovers": ["pressure"], "shots": ["shot"],
    "possession_changes": ["possession_change"], "fouls": ["foul"], "corners": ["corner"],
}


class StatsEngine:
    def __init__(self, events: list[StoredEvent]):
        self.evs = sorted(events, key=lambda s: (s.match_ms, s.event.seq))
        self.by_id = {s.event.event_id: s for s in self.evs}
        self.teams: tuple[str, ...] = tuple(sorted({s.event.team_id for s in self.evs
                                                    if s.event.team_id}))
        self.initial_score: dict[str, int] = {}
        self.home = self.away = None
        self.dir_p2: dict[str, int] = {}
        for s in self.evs:
            if s.event.type == "match_metadata":
                q = s.event.qualifiers
                self.initial_score = dict(q.get("score", {}))
                self.home, self.away = q.get("home"), q.get("away")
                self.dir_p2 = dict(q.get("attacking_direction_p2", {}))
        self._owner = self._possession_owners()

    @classmethod
    def from_store(cls, store: LedgerStore, as_of_rev: int | None = None,
                   to_ms: int | None = None) -> StatsEngine:
        return cls(store.get_events(to_ms=to_ms, as_of_rev=as_of_rev))

    # ----- helpers
    def window(self, from_ms: int, to_ms: int, types=None, team=None, player=None
               ) -> list[StoredEvent]:
        out = []
        for s in self.evs:
            if s.match_ms < from_ms:
                continue
            if s.match_ms > to_ms:
                break
            e = s.event
            if types is not None and e.type not in types:
                continue
            if team is not None and e.team_id != team:
                continue
            if player is not None and e.player_id != player:
                continue
            out.append(s)
        return out

    def attack_dir(self, team: str, period: int) -> int:
        d = self.dir_p2.get(team, 1)
        return d if period == 2 else -d

    def norm_x(self, s: StoredEvent) -> float | None:
        e = s.event
        if e.x is None or e.team_id is None:
            return None
        return e.x if self.attack_dir(e.team_id, e.period) >= 0 else config.PITCH_L - e.x

    def _possession_owners(self) -> dict[int, str]:
        owner: dict[int, str] = {}
        for s in self.evs:
            e = s.event
            if e.possession_id is None or e.team_id is None:
                continue
            if e.type in ("possession_change", "kickoff"):
                owner[e.possession_id] = e.team_id
        for s in self.evs:
            e = s.event
            if (e.possession_id is not None and e.possession_id not in owner and e.team_id
                    and e.type in ON_BALL):
                owner[e.possession_id] = e.team_id
        return owner

    # ----- counts
    def count(self, metric: str, team: str | None, from_ms: int, to_ms: int,
              player: str | None = None) -> Stat:
        if metric not in TYPES_FOR:
            raise ValueError(f"unknown count metric {metric}")
        evs = self.window(from_ms, to_ms)
        sel: list[StoredEvent] = []
        for s in evs:
            e = s.event
            if team is not None and e.team_id != team:
                continue
            if player is not None and e.player_id != player:
                continue
            ok = False
            if metric == "passes":
                ok = e.type == "pass"
            elif metric == "completed_passes":
                ok = e.type == "pass" and e.outcome == "complete"
            elif metric == "recoveries":
                ok = e.type in RECOVERY_TYPES and e.outcome == "won"
            elif metric == "recoveries_att_third":
                x = self.norm_x(s)
                ok = (e.type in RECOVERY_TYPES and e.outcome == "won" and x is not None
                      and x >= config.ATT_THIRD_X)
            elif metric == "pressures":
                ok = e.type == "pressure"
            elif metric == "forced_turnovers":
                ok = e.type == "pressure" and e.outcome == "forced_turnover"
            elif metric == "shots":
                ok = e.type == "shot"
            elif metric == "possession_changes":
                ok = e.type == "possession_change"
            elif metric == "fouls":
                ok = e.type == "foul"
            elif metric == "corners":
                ok = e.type == "corner"
            else:
                raise ValueError(f"unknown count metric {metric}")
            if ok:
                sel.append(s)
        return Stat(len(sel), [s.event.event_id for s in sel], TYPES_FOR[metric])

    # ----- rates (percent)
    def rate(self, metric: str, team: str, from_ms: int, to_ms: int) -> Stat:
        if metric == "pass_completion":
            a = self.count("completed_passes", team, from_ms, to_ms)
            b = self.count("passes", team, from_ms, to_ms)
            v = None if b.value == 0 else 100.0 * a.value / b.value
            return Stat(v, b.event_ids, ["pass"])
        if metric == "press_success":
            a = self.count("forced_turnovers", team, from_ms, to_ms)
            b = self.count("pressures", team, from_ms, to_ms)
            v = None if b.value == 0 else 100.0 * a.value / b.value
            return Stat(v, b.event_ids, ["pressure"])
        if metric == "possession_share":
            return self.possession_share(team, from_ms, to_ms)
        raise ValueError(f"unknown rate metric {metric}")

    def possession_share(self, team: str, from_ms: int, to_ms: int) -> Stat:
        evs = [s for s in self.window(from_ms, to_ms) if s.event.possession_id is not None
               and s.event.type not in ("match_metadata", "lineup")]
        tot = mine = 0
        for a, b in zip(evs, evs[1:], strict=False):
            seg = min(b.match_ms - a.match_ms, config.POSSESSION_SEG_CAP_MS)
            owner = self._owner.get(a.event.possession_id)
            if owner is None:
                continue
            tot += seg
            if owner == team:
                mine += seg
        v = None if tot == 0 else 100.0 * mine / tot
        return Stat(v, [s.event.event_id for s in evs],
                    ["pass", "carry", "shot", "possession_change", "tackle", "interception"])

    # ----- possession chains and sequences
    def chain(self, possession_id: int) -> Stat:
        evs = [s for s in self.evs if s.event.possession_id == possession_id]
        passes = [s for s in evs if s.event.type == "pass" and s.event.outcome == "complete"]
        tail = [s for s in evs if s.event.type not in ("possession_change", "kickoff")]
        return Stat(len(passes), [s.event.event_id for s in evs],
                    [tail[-1].event.event_id] if tail else [])

    def possession_of(self, event_id: str) -> int | None:
        s = self.by_id.get(event_id)
        return s.event.possession_id if s else None

    def gap_seconds(self, from_event_id: str, to_event_id: str) -> Stat:
        a, b = self.by_id.get(from_event_id), self.by_id.get(to_event_id)
        if a is None or b is None or b.match_ms < a.match_ms:
            return Stat(None, [from_event_id, to_event_id])
        return Stat((b.match_ms - a.match_ms) / 1000.0, [from_event_id, to_event_id])

    def score(self, at_ms: int) -> tuple[dict[str, int], list[str]]:
        sc = {t: self.initial_score.get(t, 0) for t in set(self.teams) | set(self.initial_score)}
        ids = []
        for s in self.evs:
            if s.match_ms > at_ms:
                break
            e = s.event
            if e.type == "goal" and e.team_id:
                sc[e.team_id] = sc.get(e.team_id, 0) + 1
                ids.append(e.event_id)
            elif e.type == "own_goal" and e.team_id:
                opp = other(e.team_id, self.teams)
                if opp:
                    sc[opp] = sc.get(opp, 0) + 1
                    ids.append(e.event_id)
        return sc, ids

    # ----- compare
    def compare(self, metric: str, team: str, window_a: tuple[int, int],
                window_b: tuple[int, int]) -> dict:
        def val(w):
            if metric in ("pass_completion", "possession_share", "press_success"):
                return self.rate(metric, team, *w)
            return self.count(metric, team, *w)
        a, b = val(window_a), val(window_b)
        delta = None if a.value is None or b.value is None else b.value - a.value
        return {"a": a.value, "b": b.value, "delta": delta,
                "event_ids": a.event_ids + b.event_ids}
