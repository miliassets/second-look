"""Correction propagation (design §4): find affected claims, re-check, invalidate descendants in
one transaction, and say which overlays must be replaced. Deterministic, no LLM."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..checker.verify import Checker
from ..contracts.claims import Claim, ClaimRecord
from ..contracts.overlay import Overlay
from ..ledger.ingest import IngestResult
from ..ledger.store import LedgerStore


@dataclass
class Invalidation:
    rev: int
    invalidated_claims: dict[str, str] = field(default_factory=dict)  # claim_id -> reason
    descendants: list[tuple[str, str]] = field(default_factory=list)
    superseded: list[Overlay] = field(default_factory=list)  # overlays taken off air
    moments: list[str] = field(default_factory=list)  # moment ids to reopen


def _reason(old: ClaimRecord, new: ClaimRecord) -> str:
    if new.status != "verified":
        return new.reason or f"claim is now {new.status}"
    return f"value was {old.value}, now {new.value}"


def moment_of_story(story_id: str) -> str:
    return "mo-" + story_id.removeprefix("st-")


def propagate(store: LedgerStore, checker: Checker, res: IngestResult, now_ms: int
              ) -> Invalidation | None:
    if res.rev is None or res.kind not in ("correction", "late", "gap"):
        return None
    touched = checker.store.claims_touching(res.touched_ids, res.touched_points)
    if not touched:
        return None
    rev = store.current_rev()
    inv = Invalidation(rev=rev)
    updates: list[tuple[str, str, str, dict]] = []
    for cid in touched:
        old = store.get("claim", cid)
        if old is None or old.status == "invalidated":
            continue
        new = checker.verify(Claim(claim_id=cid, type=old.type, params=old.params,
                                   value=old.asserted), now_ms, old.owner_kind, old.owner_id,
                             rev=rev, record=False)
        if new.status == "verified" and new.value == old.value:
            updates.append(("claim", cid, "verified", {"checked_rev": rev}))
            continue
        inv.invalidated_claims[cid] = _reason(old, new)
        updates.append(("claim", cid, "invalidated",
                        {"reason": inv.invalidated_claims[cid], "checked_rev": rev}))
    # breadth-first over the dependency graph
    seen: set[tuple[str, str]] = set()
    q: deque[tuple[str, str]] = deque(("claim", c) for c in inv.invalidated_claims)
    moments: dict[str, None] = {}
    while q:
        node = q.popleft()
        for child in store.children(*node):
            if child in seen:
                continue
            seen.add(child)
            q.append(child)
            kind, cid = child
            inv.descendants.append(child)
            if kind == "overlay":
                ov = store.get("overlay", cid)
                if ov is not None and store.get_status("overlay", cid) == "live":
                    inv.superseded.append(ov)
                    moments[moment_of_story(ov.story_id)] = None
                updates.append(("overlay", cid, "superseded", {}))
            elif kind in ("candidate", "story", "viewer_output"):
                row = store.get(kind, cid)
                if kind == "candidate" and row is not None:
                    moments[row.moment_id] = None
                if kind == "story" and row is not None:
                    moments[row.moment_id] = None
                updates.append((kind, cid, "invalidated", {}))
    # overlays that cite an invalidated claim directly (factual cards) are children above too
    for mid in moments:
        mo = store.get("moment", mid)
        if mo is not None:
            updates.append(("moment", mid, "reopened", {}))
    store.batch_set_status(updates)
    inv.moments = [m for m in moments if store.get("moment", m) is not None]
    return inv
