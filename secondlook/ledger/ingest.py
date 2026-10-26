"""Ingestor: the single writer of the ledger (dedupe, match_ms, revisions, gaps, corrections)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.events import Delivery, Event
from ..timeline import to_match_ms
from .store import LedgerStore, StoredEvent

GAP_GRACE_MS = 5000  # a missing seq becomes an open gap only if still missing after this


@dataclass
class IngestResult:
    duplicate: bool = False
    rev: int | None = None
    kind: str | None = None  # append | late | correction | gap
    event_id: str | None = None
    touched_ids: list[str] = field(default_factory=list)
    touched_points: list[tuple[str | None, str, int]] = field(default_factory=list)
    gap_revs: list[int] = field(default_factory=list)  # revs from gaps that opened first


class Ingestor:
    def __init__(self, store: LedgerStore, periods: list[dict[str, int]]):
        self.store = store
        self.periods = periods
        self.max_ms = -1
        self.highest_seq = 0
        self.pending: dict[int, int] = {}  # missing seq -> arrival_ms first noticed
        self.open_gap_seqs: set[int] = set()

    def ms(self, e: Event) -> int:
        return to_match_ms(e.period, e.match_clock_ms, self.periods)

    def tick(self, now_ms: int) -> list[int]:
        """Promote missing seqs older than the grace window to open gaps."""
        revs = []
        for seq in sorted(s for s, t in self.pending.items() if now_ms - t >= GAP_GRACE_MS):
            del self.pending[seq]
            rev = self.store.next_rev("gap", None, now_ms)
            self.store.open_gap(seq, seq, rev, now_ms)
            self.open_gap_seqs.add(seq)
            revs.append(rev)
        return revs

    def ingest(self, d: Delivery) -> IngestResult:
        e = d.event
        res = IngestResult()
        if self.store.has_event(e.event_id):
            self.store.bump_stat("duplicates")
            res.duplicate = True
            return res
        res.gap_revs = self.tick(d.arrival_match_ms)
        ms = self.ms(e)
        # seq bookkeeping (out-of-band corrections, e.g. injected live, are not part of the sequence)
        if e.type == "event_correction" and e.seq != self.highest_seq + 1:
            return self._correction(d, e, ms, res)
        if e.seq > self.highest_seq + 1:
            for miss in range(self.highest_seq + 1, e.seq):
                if miss not in self.open_gap_seqs:
                    self.pending.setdefault(miss, d.arrival_match_ms)
        self.highest_seq = max(self.highest_seq, e.seq)
        closes_gap = e.seq in self.open_gap_seqs
        self.pending.pop(e.seq, None)

        if e.type == "event_correction":
            return self._correction(d, e, ms, res)

        late = ms < self.max_ms
        kind = "gap" if closes_gap else "late" if late else "append"
        rev = self.store.next_rev(kind, e.event_id, d.arrival_match_ms)
        self.store.append_event(StoredEvent(
            event=e, match_ms=ms, arrival_order=d.arrival_order,
            arrival_match_ms=d.arrival_match_ms, rev=rev))
        if closes_gap:
            self.store.close_gap(e.seq, rev)
            self.open_gap_seqs.discard(e.seq)
        self.max_ms = max(self.max_ms, ms)
        res.rev, res.kind, res.event_id = rev, kind, e.event_id
        if kind != "append":
            res.touched_ids = [e.event_id]
            res.touched_points = [(e.team_id, e.type, ms)]
        return res

    def _correction(self, d: Delivery, e: Event, ms: int, res: IngestResult) -> IngestResult:
        q = e.qualifiers
        target_id = q["corrects"]
        target = self.store.get_event(target_id)
        rev = self.store.next_rev("correction", e.event_id, d.arrival_match_ms)
        self.store.append_event(StoredEvent(
            event=e, match_ms=ms, arrival_order=d.arrival_order,
            arrival_match_ms=d.arrival_match_ms, rev=rev))
        self.max_ms = max(self.max_ms, ms)
        res.rev, res.kind, res.event_id = rev, "correction", e.event_id
        if target is None:  # correction for an event we never saw: record it, nothing to fix
            return res
        res.touched_ids = [target_id]
        res.touched_points = [(target.event.team_id, target.event.type, target.match_ms)]
        if q.get("kind") == "void":
            self.store.apply_correction(target_id, "void", e.event_id, rev)
        else:
            repl = Event.model_validate(q["replacement"])
            self.store.apply_correction(target_id, "corrected", repl.event_id, rev)
            self.store.append_event(StoredEvent(
                event=repl, match_ms=self.ms(repl), arrival_order=d.arrival_order,
                arrival_match_ms=d.arrival_match_ms, rev=rev))
            res.touched_ids.append(repl.event_id)
            res.touched_points.append((repl.team_id, repl.type, self.ms(repl)))
        return res
