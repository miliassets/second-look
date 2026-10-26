"""Correction notices: deterministic overlays aired at once, superseding what was on screen."""
from __future__ import annotations

from ..checker.roster import Roster
from ..checker.verify import Checker
from ..contracts.agents_io import Moment
from ..contracts.overlay import Overlay
from ..contracts.prefs import ViewerProfile
from ..ledger.store import LedgerStore
from ..moments.factual import build_factual_card


def notice_for(correction_event_id: str, kind: str, superseded: Overlay, profile: ViewerProfile,
               store: LedgerStore, checker: Checker, roster: Roster, periods, rev: int,
               now_ms: int) -> tuple[Overlay, list] | None:
    """kind: 'disallowed_goal' (void goal) or 'correction' (anything else)."""
    m = Moment(moment_id=f"mo-notice-{correction_event_id}", kind=kind,
               trigger_event_id=correction_event_id, window_from_ms=now_ms,
               window_to_ms=now_ms, now_ms=now_ms, as_of_rev=rev)
    ev = store.get_event(correction_event_id)
    if ev is None:
        return None
    card = build_factual_card(m, profile, store, checker, roster, periods)
    if card is None:
        return None
    story_suffix = superseded.story_id.removeprefix("st-")
    ov = card.overlay.model_copy(update={
        "overlay_id": f"ov-{story_suffix}-notice-r{rev}-{profile.profile_id}",
        "story_id": superseded.story_id, "kind": "correction_notice", "priority": 3,
        "supersedes": superseded.overlay_id, "badges": ["updated"],
        "duration_ms": 12000})
    return ov, card.claims
