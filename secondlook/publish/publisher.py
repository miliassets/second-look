"""Publisher: publish-time re-check (race guard, design §4) and overlay assembly."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..checker.verify import Checker
from ..contracts.claims import Claim, ClaimRecord
from ..contracts.overlay import Overlay
from ..ledger.store import LedgerStore
from .bus import Bus


@dataclass
class PublishResult:
    published: bool
    reason: str = ""
    stale_claims: list[str] = field(default_factory=list)


class Publisher:
    def __init__(self, store: LedgerStore, bus: Bus, checker: Checker):
        self.store, self.bus, self.checker = store, bus, checker

    def recheck(self, claims: list[ClaimRecord], now_ms: int) -> list[str]:
        """Re-verify every claim at the current rev. Returns the ids that no longer hold or whose
        computed value changed (the text was rendered from the old value)."""
        rev = self.store.current_rev()
        stale = []
        for rec in claims:
            new = self.checker.verify(
                Claim(claim_id=rec.claim_id, type=rec.type, params=rec.params, value=rec.asserted),
                now_ms, rec.owner_kind, rec.owner_id, rev=rev, record=False)
            if new.status != "verified" or new.value != rec.value:
                stale.append(rec.claim_id)
        return stale

    def publish(self, overlay: Overlay, claims: list[ClaimRecord], now_ms: int
                ) -> PublishResult:
        stale = self.recheck(claims, now_ms)
        if stale:
            return PublishResult(False, "claims no longer hold at the current rev", stale)
        self.store.put("overlay", overlay)
        self.store.add_deps([("claim", c.claim_id, "overlay", overlay.overlay_id)
                             for c in claims])
        if overlay.supersedes:
            self.store.set_status("overlay", overlay.supersedes, "superseded")
        self.bus.publish("overlay", overlay.model_dump(mode="json"), overlay.profile_id)
        return PublishResult(True)
