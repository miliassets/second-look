"""Scripted `event_correction` events (design §3 'Feed realism').

A correction is part of the feed: it arrives as its own event after the target. Ledger rule
(design §2.2): rows are never deleted; the target becomes `corrected`; a replacement version is
inserted as a new row whose `qualifiers.corrects` points at the old id.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import MATCH_ID
from ..contracts.events import Event


@dataclass(frozen=True)
class CorrectionSpec:
    kind: str  # "void_goal" | "reattribute"
    delay_ms: int = 25_000
    target_event_id: str | None = None  # reattribute: the event to fix (default: the recovery)
    new_player_id: str | None = None  # reattribute
    reason: str = ""


def _find(events: list[Event], event_id: str) -> Event:
    return next(e for e in events if e.event_id == event_id)


def insert_corrections(events: list[Event], specs: list[CorrectionSpec], truth: dict
                       ) -> list[Event]:
    """Return a new list with correction events inserted; re-sequences `seq` (ids are stable)."""
    if not specs:
        return events
    out = [e.model_copy(deep=True) for e in events]
    pending: list[tuple[int, Event]] = []
    n = 0
    for spec in specs:
        n += 1
        if spec.kind == "void_goal":
            goal = _find(out, spec.target_event_id or truth["beats"]["goal"]["event_id"])
            clock = goal.match_clock_ms + spec.delay_ms
            q = {"corrects": goal.event_id, "kind": "void",
                 "reason": spec.reason or "offside after review", "replacement": None,
                 "also_voids": list(goal.related_ids)}
            team, target = goal.team_id, goal
        elif spec.kind == "reattribute":
            tid = spec.target_event_id or truth["beats"]["recovery_event_id"]
            target = _find(out, tid)
            new_pid = spec.new_player_id
            assert new_pid, "reattribute needs new_player_id"
            clock = target.match_clock_ms + spec.delay_ms
            repl = target.model_copy(deep=True)
            repl.event_id = f"{MATCH_ID}-r{n:03d}"
            repl.player_id = new_pid
            repl.qualifiers = {**repl.qualifiers, "corrects": target.event_id}
            q = {"corrects": target.event_id, "kind": "replace",
                 "reason": spec.reason or "attribution fixed after review",
                 "replacement": repl.model_dump()}
            team = target.team_id
        else:
            raise ValueError(spec.kind)
        ev = Event(event_id=f"{MATCH_ID}-x{n:03d}", match_id=MATCH_ID, seq=0, type="event_correction",
                   period=target.period, match_clock_ms=clock, wall_ts=None, team_id=team,
                   player_id=None, possession_id=None, related_ids=[target.event_id],
                   qualifiers=q)
        pending.append((clock, ev))
    for clock, ev in pending:
        idx = len(out)
        for i, e in enumerate(out):
            if (e.period, e.match_clock_ms) > (ev.period, clock):
                idx = i
                break
        ev.wall_ts = out[min(idx, len(out) - 1)].wall_ts
        out.insert(idx, ev)
    for i, e in enumerate(out, start=1):
        e.seq = i
    return out
