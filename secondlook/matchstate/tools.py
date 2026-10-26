"""Match-state tools for agents (design §3.3). Plain functions, tested directly; the MCP server
wraps them. Every tool takes a `moment_token`; the registry maps it to `now_ms` and `as_of_rev`
and every window is clamped to `<= now_ms`, so agents can never see the future."""
from __future__ import annotations

import hashlib
from typing import Any

from ..checker.verify import Checker
from ..contracts.agents_io import Moment
from ..contracts.claims import COUNT_METRICS, PHYS_METRICS, RATE_METRICS, Claim
from ..ledger.store import LedgerStore, StoredEvent
from ..stats.engine import StatsEngine
from ..timeline import format_clock

MAX_EVENTS = 200


class ToolError(Exception):
    pass


def compact(s: StoredEvent) -> dict:
    e = s.event
    d: dict[str, Any] = {"event_id": e.event_id, "type": e.type, "team_id": e.team_id,
                         "player_id": e.player_id, "ms": s.match_ms,
                         "clock": format_clock(e.period, e.match_clock_ms),
                         "possession_id": e.possession_id, "outcome": e.outcome}
    if e.x is not None:
        d["at"] = [e.x, e.y]
    if e.end_x is not None:
        d["to"] = [e.end_x, e.end_y]
    if e.qualifiers.get("speed_kmh") is not None:
        d["speed_kmh"] = e.qualifiers["speed_kmh"]
    return d


class MatchTools:
    def __init__(self, store: LedgerStore, checker: Checker, periods: list[dict[str, int]]):
        self.store, self.checker, self.periods = store, checker, periods
        self.tokens: dict[str, dict] = {}
        self.by_moment: dict[str, str] = {}

    # ---- tokens
    def issue_token(self, m: Moment) -> str:
        old = self.by_moment.get(m.moment_id)
        if old and self.tokens[old]["rev"] == m.as_of_rev:
            return old
        tok = "tok-" + hashlib.sha256(f"{m.moment_id}:{m.as_of_rev}".encode()).hexdigest()[:12]
        self.tokens[tok] = {"now_ms": m.now_ms, "rev": m.as_of_rev, "moment": m}
        self.by_moment[m.moment_id] = tok
        return tok

    def _ctx(self, token: str) -> dict:
        if token not in self.tokens:
            raise ToolError("unknown moment_token")
        return self.tokens[token]

    def _stats(self, ctx: dict) -> StatsEngine:
        return self.checker.stats_at(ctx["now_ms"], ctx["rev"])

    def _clamp(self, ctx, lo: int, hi: int) -> tuple[int, int]:
        hi = min(int(hi), ctx["now_ms"])
        lo = max(int(lo), 0)
        return lo, hi

    def _gaps(self, ctx, stats, lo, hi) -> list[dict]:
        return [{"from_ms": a, "to_ms": b} for a, b in self.checker.gap_ranges(
            stats, ctx["now_ms"]) if a <= hi and b >= lo]

    def _wrap(self, ctx, stats, lo, hi, evidence, **data) -> dict:
        return {**data, "evidence_ids": list(dict.fromkeys(evidence)),
                "gaps": self._gaps(ctx, stats, lo, hi), "now_ms": ctx["now_ms"]}

    # ---- claim objects
    def _compute(self, ctx, type_: str, params: dict) -> dict:
        """Compute a claim's true value without comparing to an assertion."""
        stats = self._stats(ctx)
        now = ctx["now_ms"]
        chk = self.checker
        claim = Claim.model_construct(claim_id="x", type=type_, params=params, value=None)
        out = chk._dispatch(claim, stats, now, ctx["rev"])
        chk._gap_guard(out, stats, now)
        return {"value": out.value, "evidence_ids": out.event_ids}

    def _fact(self, ctx, cid: str, type_: str, **params) -> dict | None:
        try:
            r = self._compute(ctx, type_, params)
        except Exception:
            return None
        v = r["value"]
        key = {"rate": "value_pct", "gap_seconds": "seconds"}.get(type_, "value")
        if type_ == "score":
            params = {**params, "home": v["home"], "away": v["away"]}
        elif type_ == "chain":
            params = {**params, "passes": v["passes"],
                      "ended_with_event_id": v["ended_with_event_id"]}
        elif type_ == "phase":
            params = {**params, "label": v}
        elif type_ in ("count", "rate", "physical", "event_fact"):
            params = {**params, key: v}
        return {"claim_id": cid, "type": type_, "params": params, "value": v,
                "evidence_ids": r["evidence_ids"][:12]}

    # ---- tools
    def get_dossier(self, moment_token: str) -> dict:
        ctx = self._ctx(moment_token)
        m: Moment = ctx["moment"]
        stats = self._stats(ctx)
        lo, hi = m.window_from_ms, m.window_to_ms
        facts: list[dict] = []
        n = 0

        def add(f):
            nonlocal n
            if f:
                n += 1
                f["claim_id"] = f"d{n}"
                facts.append(f)
        add(self._fact(ctx, "", "score", at_ms=hi))
        for t in stats.teams:
            add(self._fact(ctx, "", "phase", team_id=t, from_ms=lo, to_ms=hi))
            for metric in ("passes", "recoveries", "pressures", "shots", "possession_changes"):
                add(self._fact(ctx, "", "count", metric=metric, team_id=t, from_ms=lo, to_ms=hi))
            for metric in ("pass_completion", "possession_share"):
                add(self._fact(ctx, "", "rate", metric=metric, team_id=t, from_ms=lo, to_ms=hi))
        trig = self.store.get_event(m.trigger_event_id) if m.trigger_event_id else None
        if trig and trig.event.possession_id is not None and trig.status == "active":
            add(self._fact(ctx, "", "chain", possession_id=trig.event.possession_id))
            add(self._fact(ctx, "", "event_fact", event_id=trig.event.event_id,
                           field="player_id"))
        # notable players: most passes in the window
        passers: dict[str, int] = {}
        for s in stats.window(lo, hi, types={"pass"}):
            if s.event.player_id:
                passers[s.event.player_id] = passers.get(s.event.player_id, 0) + 1
        top = sorted(passers.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        return self._wrap(ctx, stats, lo, hi, [e for f in facts for e in f["evidence_ids"]],
                          moment={"moment_id": m.moment_id, "kind": m.kind, "team_id": m.team_id,
                                  "trigger_event_id": m.trigger_event_id,
                                  "window_from_ms": lo, "window_to_ms": hi, "detail": m.detail},
                          facts=facts, notable_players=[{"player_id": p, "passes": c}
                                                        for p, c in top])

    def get_events(self, moment_token: str, from_ms: int, to_ms: int,
                   types: list[str] | None = None, team_id: str | None = None,
                   player_id: str | None = None, limit: int = MAX_EVENTS) -> dict:
        ctx = self._ctx(moment_token)
        lo, hi = self._clamp(ctx, from_ms, to_ms)
        stats = self._stats(ctx)
        evs = stats.window(lo, hi, set(types) if types else None, team_id, player_id)
        limit = max(1, min(int(limit), MAX_EVENTS))
        out = [compact(s) for s in evs[:limit]]
        return self._wrap(ctx, stats, lo, hi, [e["event_id"] for e in out], events=out,
                          truncated=len(evs) > limit)

    def get_event(self, moment_token: str, event_id: str) -> dict:
        ctx = self._ctx(moment_token)
        s = self.store.get_event(event_id, as_of_rev=ctx["rev"])
        if s is None or s.match_ms > ctx["now_ms"]:
            raise ToolError("event not found")
        stats = self._stats(ctx)
        return self._wrap(ctx, stats, s.match_ms, s.match_ms, [event_id],
                          event={**compact(s), "status": s.status,
                                 "qualifiers": s.event.qualifiers})

    def get_possession(self, moment_token: str, possession_id: int) -> dict:
        ctx = self._ctx(moment_token)
        stats = self._stats(ctx)
        evs = [s for s in stats.evs if s.event.possession_id == possession_id]
        if not evs:
            raise ToolError("possession not found")
        c = stats.chain(possession_id)
        lo, hi = evs[0].match_ms, evs[-1].match_ms
        return self._wrap(ctx, stats, lo, hi, [s.event.event_id for s in evs],
                          possession_id=possession_id, passes=c.value, duration_ms=hi - lo,
                          events=[compact(s) for s in evs][:MAX_EVENTS])

    def get_stat(self, moment_token: str, metric: str, from_ms: int, to_ms: int,
                 team_id: str | None = None, player_id: str | None = None) -> dict:
        ctx = self._ctx(moment_token)
        lo, hi = self._clamp(ctx, from_ms, to_ms)
        stats = self._stats(ctx)
        if metric in COUNT_METRICS:
            st = stats.count(metric, team_id, lo, hi, player_id)
        elif metric in RATE_METRICS:
            if not team_id:
                raise ToolError("team_id required")
            st = stats.rate(metric, team_id, lo, hi)
        elif metric in PHYS_METRICS:
            if not player_id:
                raise ToolError("player_id required")
            return self.get_physical(moment_token, player_id, lo, hi)
        else:
            raise ToolError(f"unknown metric {metric}")
        return self._wrap(ctx, stats, lo, hi, st.event_ids, metric=metric, team_id=team_id,
                          player_id=player_id, from_ms=lo, to_ms=hi,
                          value=None if st.value is None else round(st.value, 2))

    def compare_windows(self, moment_token: str, metric: str, team_id: str,
                        window_a: list[int], window_b: list[int]) -> dict:
        ctx = self._ctx(moment_token)
        wa, wb = self._clamp(ctx, *window_a), self._clamp(ctx, *window_b)
        stats = self._stats(ctx)
        if metric not in (*COUNT_METRICS, *RATE_METRICS):
            raise ToolError(f"unknown metric {metric}")
        r = stats.compare(metric, team_id, wa, wb)
        return self._wrap(ctx, stats, min(wa[0], wb[0]), max(wa[1], wb[1]), r["event_ids"],
                          metric=metric, team_id=team_id, a=r["a"], b=r["b"],
                          delta=None if r["delta"] is None else round(r["delta"], 2),
                          window_a=list(wa), window_b=list(wb))

    def get_physical(self, moment_token: str, player_id: str, from_ms: int, to_ms: int) -> dict:
        ctx = self._ctx(moment_token)
        lo, hi = self._clamp(ctx, from_ms, to_ms)
        stats = self._stats(ctx)
        phys = self.checker.phys
        if phys is None:
            raise ToolError("no tracking frames")
        vals = {m: phys.metric(m, player_id, lo, hi).value for m in PHYS_METRICS}
        return self._wrap(ctx, stats, lo, hi, [], player_id=player_id, from_ms=lo, to_ms=hi,
                          top_speed_kmh=round(vals["top_speed_kmh"], 2),
                          distance_m=round(vals["distance_m"], 1), sprints=vals["sprints"])

    def get_score(self, moment_token: str, at_ms: int | None = None) -> dict:
        ctx = self._ctx(moment_token)
        at = min(int(at_ms if at_ms is not None else ctx["now_ms"]), ctx["now_ms"])
        stats = self._stats(ctx)
        sc, ids = stats.score(at)
        return self._wrap(ctx, stats, 0, at, ids, at_ms=at,
                          home={"team_id": stats.home, "goals": sc.get(stats.home, 0)},
                          away={"team_id": stats.away, "goals": sc.get(stats.away, 0)})

    def verify_claim(self, moment_token: str, claim: dict) -> dict:
        ctx = self._ctx(moment_token)
        try:
            c = Claim.model_validate(claim)
        except Exception as e:
            return {"status": "failed", "reason": f"invalid claim: {e}"}
        rec = self.checker.verify(c, ctx["now_ms"], rev=ctx["rev"], record=False)
        return {"claim_id": rec.claim_id, "status": rec.status, "reason": rec.reason,
                "value": rec.value, "evidence_ids": rec.evidence_ids}

    # name -> (callable, description) used by the MCP server and the agents' tool specs
    def catalogue(self) -> dict[str, Any]:
        return {n: getattr(self, n) for n in (
            "get_dossier", "get_events", "get_event", "get_possession", "get_stat",
            "compare_windows", "get_physical", "get_score", "verify_claim")}
