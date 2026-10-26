"""Fail-closed claim verification (design §3.5).

A claim is `verified` only if the checker computes its own value and it matches. Exceptions,
unknown types, windows past `now_ms`, and an open feed gap overlapping the window give
`unknown`, which callers treat as failed. Missing evidence is never zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import config
from ..contracts.claims import Claim, ClaimRecord
from ..contracts.events import Frame
from ..ledger.store import LedgerStore
from ..stats.engine import StatsEngine
from ..stats.phases import phase_label
from ..stats.physical import PhysicalEngine


class Unknown(Exception):
    """Cannot be computed or checked; fail closed."""


class Failed(Exception):
    """Computed and contradicts the claim."""


@dataclass
class Outcome:
    value: Any
    event_ids: list[str]
    windows: list[tuple[str | None, list[str], int, int]]  # team, types, from, to


def _win(w) -> tuple[int, int]:
    if isinstance(w, dict):
        return int(w["from_ms"]), int(w["to_ms"])
    return int(w[0]), int(w[1])


def _asserted(claim: Claim, *keys: str):
    if claim.value is not None:
        return claim.value
    for k in keys:
        if k in claim.params:
            return claim.params[k]
    return None


class Checker:
    def __init__(self, store: LedgerStore, periods: list[dict[str, int]],
                 frames: list[Frame] | None = None):
        self.store = store
        self.periods = periods
        self.phys = PhysicalEngine(frames, periods) if frames else None

    # ---- context
    def stats_at(self, now_ms: int, rev: int | None = None) -> StatsEngine:
        return StatsEngine.from_store(self.store, as_of_rev=rev, to_ms=now_ms)

    def gap_ranges(self, stats: StatsEngine, now_ms: int) -> list[tuple[int, int]]:
        by_seq = sorted((s.event.seq, s.match_ms) for s in stats.evs)
        out = []
        for g in self.store.gaps(open_only=True):
            lo = max((ms for sq, ms in by_seq if sq < g.from_seq), default=0)
            hi = min((ms for sq, ms in by_seq if sq > g.to_seq), default=now_ms)
            out.append((lo, hi))
        return out

    # ---- public
    def verify(self, claim: Claim, now_ms: int, owner_kind: str = "candidate",
               owner_id: str = "", rev: int | None = None, record: bool = True) -> ClaimRecord:
        rev = self.store.current_rev() if rev is None else rev
        rec = ClaimRecord(claim_id=claim.claim_id, owner_kind=owner_kind, owner_id=owner_id,
                          type=claim.type, params=dict(claim.params), asserted=claim.value,
                          checked_rev=rev)
        out: Outcome | None = None
        try:
            stats = self.stats_at(now_ms, rev)
            out = self._dispatch(claim, stats, now_ms, rev)
            rec.value = out.value
            rec.evidence_ids = list(dict.fromkeys(out.event_ids))
            self._gap_guard(out, stats, now_ms)
            self._compare(claim, out)
            rec.status = "verified"
        except Failed as e:
            rec.status, rec.reason = "failed", str(e)
        except Unknown as e:
            rec.status, rec.reason = "unknown", str(e)
        except Exception as e:  # fail closed
            rec.status, rec.reason = "unknown", f"error: {type(e).__name__}: {e}"
        if record:
            self.store.put("claim", rec)
            if out is not None:
                self.store.add_claim_event_deps(rec.claim_id, rec.evidence_ids)
                for team, types, lo, hi in out.windows:
                    self.store.add_claim_window_dep(rec.claim_id, team, types, lo, hi)
        return rec

    def verify_many(self, claims: list[Claim], now_ms: int, owner_kind: str = "candidate",
                    owner_id: str = "", rev: int | None = None) -> list[ClaimRecord]:
        return [self.verify(c, now_ms, owner_kind, owner_id, rev) for c in claims]

    # ---- internals
    def _gap_guard(self, out: Outcome, stats: StatsEngine, now_ms: int) -> None:
        if not out.windows:
            return
        gaps = self.gap_ranges(stats, now_ms)
        for _, _, lo, hi in out.windows:
            for glo, ghi in gaps:
                if glo <= hi and ghi >= lo:
                    raise Unknown("open feed gap overlaps the window")

    def _need_window(self, lo: int, hi: int, now_ms: int) -> None:
        if lo > hi:
            raise Unknown("window is reversed")
        if hi > now_ms:
            raise Unknown("window extends past the moment's now")

    def _event(self, stats: StatsEngine, event_id: str, now_ms: int):
        s = self.store.get_event(event_id)
        if s is None:
            raise Failed(f"event {event_id} not found")
        if s.match_ms > now_ms:
            raise Unknown("event is after the moment's now")
        if s.status != "active":
            raise Failed(f"event {event_id} is {s.status}")
        return s

    def _dispatch(self, c: Claim, st: StatsEngine, now_ms: int, rev: int) -> Outcome:
        fn = getattr(self, f"_check_{c.type}", None)
        if fn is None:
            raise Unknown(f"unknown claim type {c.type}")
        return fn(c, st, now_ms)

    def _check_event_fact(self, c, st, now_ms):
        p = c.params
        field = p["field"]
        if field == "status":  # a fact about the ledger itself; allowed on void/corrected events
            s = self.store.get_event(p["event_id"])
            if s is None:
                raise Failed(f"event {p['event_id']} not found")
            return Outcome(s.status, [s.event.event_id], [])
        s = self._event(st, p["event_id"], now_ms)
        e = s.event
        if field == "qualifiers.speed_kmh":
            v = e.qualifiers.get("speed_kmh")
        elif field == "qualifiers.body_part":
            v = e.qualifiers.get("body_part")
        elif field in ("type", "player_id", "team_id", "outcome"):
            v = getattr(e, field)
        else:
            raise Unknown(f"unsupported field {field}")
        if v is None:
            raise Unknown("field not present on the event")
        return Outcome(v, [e.event_id], [])

    def _check_count(self, c, st, now_ms):
        p = c.params
        lo, hi = int(p["from_ms"]), int(p["to_ms"])
        self._need_window(lo, hi, now_ms)
        stat = st.count(p["metric"], p.get("team_id"), lo, hi, p.get("player_id"))
        return Outcome(stat.value, stat.event_ids, [(p.get("team_id"), stat.event_types, lo, hi)])

    def _check_rate(self, c, st, now_ms):
        p = c.params
        lo, hi = int(p["from_ms"]), int(p["to_ms"])
        self._need_window(lo, hi, now_ms)
        stat = st.rate(p["metric"], p["team_id"], lo, hi)
        if stat.value is None:
            raise Unknown("no data in the window")
        types = ["pass"] if p["metric"] == "pass_completion" else (
            ["pressure"] if p["metric"] == "press_success" else [])
        return Outcome(round(stat.value, 2), stat.event_ids, [(p["team_id"], types, lo, hi)])

    def _check_chain(self, c, st, now_ms):
        p = c.params
        pid = int(p["possession_id"])
        stat = st.chain(pid)
        if not stat.event_ids:
            raise Failed("possession not found")
        last = max(st.by_id[e].match_ms for e in stat.event_ids)
        if last > now_ms:
            raise Unknown("chain extends past the moment's now")
        ended = stat.event_types[0] if stat.event_types else None
        first = min(st.by_id[e].match_ms for e in stat.event_ids)
        return Outcome({"passes": stat.value, "ended_with_event_id": ended}, stat.event_ids,
                       [(None, [], first, last)])

    def _check_gap_seconds(self, c, st, now_ms):
        p = c.params
        a = self._event(st, p["from_event_id"], now_ms)
        b = self._event(st, p["to_event_id"], now_ms)
        if b.match_ms < a.match_ms:
            raise Failed("from_event is not before to_event")
        return Outcome((b.match_ms - a.match_ms) / 1000.0, [a.event.event_id, b.event.event_id], [])

    def _check_physical(self, c, st, now_ms):
        if self.phys is None:
            raise Unknown("no tracking frames available")
        p = c.params
        lo, hi = int(p["from_ms"]), int(p["to_ms"])
        self._need_window(lo, hi, now_ms)
        r = self.phys.metric(p["metric"], p["player_id"], lo, hi)
        if r.samples == 0:
            raise Unknown("no tracking frames in the window")
        v = round(r.value, 2) if isinstance(r.value, float) else r.value
        return Outcome(v, [], [(None, ["frame"], lo, hi)])

    def _check_compare(self, c, st, now_ms):
        p = c.params
        wa, wb = _win(p["window_a"]), _win(p["window_b"])
        for lo, hi in (wa, wb):
            self._need_window(lo, hi, now_ms)
        r = st.compare(p["metric"], p["team_id"], wa, wb)
        if r["delta"] is None:
            raise Unknown("no data in one of the windows")
        types = ["pass"] if p["metric"] in ("passes", "completed_passes", "pass_completion") else []
        return Outcome({"a": r["a"], "b": r["b"], "delta": round(r["delta"], 2)}, r["event_ids"],
                       [(p["team_id"], types, wa[0], wa[1]), (p["team_id"], types, wb[0], wb[1])])

    def _check_phase(self, c, st, now_ms):
        p = c.params
        lo, hi = int(p["from_ms"]), int(p["to_ms"])
        self._need_window(lo, hi, now_ms)
        label = phase_label(st, p["team_id"], lo, hi)
        evs = [s.event.event_id for s in st.window(lo, hi)]
        return Outcome(label, evs, [(None, [], lo, hi)])

    def _check_score(self, c, st, now_ms):
        at = int(c.params["at_ms"])
        if at > now_ms:
            raise Unknown("score time is past the moment's now")
        sc, ids = st.score(at)
        home, away = st.home, st.away
        if home is None:
            raise Unknown("no match metadata")
        return Outcome({"home": sc.get(home, 0), "away": sc.get(away, 0)}, ids,
                       [(None, ["goal", "own_goal"], 0, at)])

    def _check_involved(self, c, st, now_ms):
        p = c.params
        ids = list(p["event_ids"])
        for eid in ids:
            s = self._event(st, eid, now_ms)
            if s.event.player_id != p["player_id"]:
                raise Failed(f"{p['player_id']} is not the actor in {eid}")
        return Outcome(p["player_id"], ids, [])

    # ---- compare asserted vs computed
    def _compare(self, c: Claim, out: Outcome) -> None:
        v, p = out.value, c.params
        t = c.type
        if t == "event_fact":
            a = _asserted(c, "value")
            if a is None:
                raise Failed("no value asserted")
            if p["field"] == "qualifiers.speed_kmh":
                ok = abs(float(a) - float(v)) <= config.TOL_SPEED_KMH
            else:
                ok = str(a) == str(v)
            if not ok:
                raise Failed(f"{p['field']} is {v}, not {a}")
        elif t == "count":
            a = _asserted(c, "value")
            if a is None or int(a) != v:
                raise Failed(f"count is {v}, not {a}")
        elif t == "rate":
            a = _asserted(c, "value_pct", "value")
            if a is None or abs(float(a) - v) > config.TOL_RATE_PTS:
                raise Failed(f"rate is {v}, not {a}")
        elif t == "chain":
            a = _asserted(c, "passes")
            if isinstance(a, dict):  # agents may copy the dossier's whole value object
                a = a.get("passes")
            if a is None and "ended_with_event_id" not in p:
                raise Failed("nothing asserted")
            if a is not None and int(a) != v["passes"]:
                raise Failed(f"chain has {v['passes']} passes, not {a}")
            if "ended_with_event_id" in p and p["ended_with_event_id"] != v["ended_with_event_id"]:
                raise Failed(f"chain ended with {v['ended_with_event_id']}")
        elif t == "gap_seconds":
            a = _asserted(c, "seconds")
            if a is None or abs(float(a) - v) > config.TOL_GAP_S:
                raise Failed(f"gap is {v} s, not {a}")
        elif t == "physical":
            a = _asserted(c, "value")
            if a is None:
                raise Failed("no value asserted")
            m = p["metric"]
            if m == "top_speed_kmh":
                ok = abs(float(a) - v) <= config.TOL_PHYS_SPEED_KMH
            elif m == "distance_m":
                ok = abs(float(a) - v) <= config.TOL_PHYS_DIST_FRAC * max(v, 1e-9)
            else:
                ok = int(a) == v
            if not ok:
                raise Failed(f"{m} is {v}, not {a}")
        elif t == "compare":
            d = v["delta"]
            direction, mn = p["direction"], float(p.get("min_delta", 0))
            ok = (d >= mn and d > 0) if direction == "up" else (-d >= mn and d < 0)
            if not ok:
                raise Failed(f"delta is {d}, not {direction} by {mn}")
        elif t == "phase":
            if p["label"] != v:
                raise Failed(f"phase is {v}, not {p['label']}")
        elif t == "score":
            if int(p["home"]) != v["home"] or int(p["away"]) != v["away"]:
                raise Failed(f"score is {v['home']}-{v['away']}")
        elif t == "involved":
            pass  # checked in _check_involved
