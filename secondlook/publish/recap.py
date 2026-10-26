"""Half-time and full-time recaps: composed from the stories that are live right now.

A recap lists the current explanation of each cited story (never a superseded revision) and
shares those stories' claims, so a correction that invalidates a story also takes down the recap
(deps: claim -> overlay, story -> overlay) and the recap is rebuilt without it.
"""
from __future__ import annotations

from ..contracts.agents_io import Moment
from ..contracts.claims import ClaimRecord
from ..contracts.overlay import ClaimRef, Overlay, Source
from ..contracts.prefs import ViewerProfile
from ..ledger.store import LedgerStore
from ..moments.factual import story_id_for
from .publisher import Publisher

HEAD = {"en": {"half_time": "Half time", "full_time": "Full time"},
        "es": {"half_time": "Descanso", "full_time": "Final"}}
MAX_ITEMS = {"analyst": 5, "fan": 3}


def recap_overlay_id(moment_id: str, revision: int, profile_id: str) -> str:
    return f"ov-{moment_id.removeprefix('mo-')}-recap-r{revision}-{profile_id}"


def build_recap(store: LedgerStore, publisher: Publisher, profiles: list[ViewerProfile],
                m: Moment, now_ms: int, notice_ids: dict[str, str]) -> list[str]:
    published: list[str] = []
    story_id = story_id_for(m.moment_id)
    for prof in profiles:
        live = [o for o in store.list_kind("overlay", profile_id=prof.profile_id,
                                           kind="explanation")
                if store.get_status("overlay", o.overlay_id) == "live"
                and o.show_at_match_ms <= now_ms]
        live.sort(key=lambda o: (o.show_at_match_ms, o.overlay_id))
        live = live[-MAX_ITEMS[prof.mode]:]
        if not live:
            continue
        previous = [o for o in store.list_kind("overlay", story_id=story_id,
                                               profile_id=prof.profile_id, kind="recap")]
        rev = max((o.revision for o in previous), default=0) + 1
        recs: list[ClaimRecord] = []
        seen: set[str] = set()
        for o in live:
            for c in o.claims:
                rec = store.get("claim", c.claim_id)
                if rec is not None and rec.status == "verified" and c.claim_id not in seen:
                    seen.add(c.claim_id)
                    recs.append(rec)
        prev_live = [o for o in previous
                     if store.get_status("overlay", o.overlay_id) == "live"]
        ov = Overlay(
            overlay_id=recap_overlay_id(m.moment_id, rev, prof.profile_id), story_id=story_id,
            revision=rev, kind="recap", viewer_mode=prof.mode, profile_id=prof.profile_id,
            lang=prof.lang, show_at_match_ms=now_ms, duration_ms=45_000, slot="side_panel",
            priority=1, headline=HEAD[prof.lang][m.kind],
            text="\n".join(o.text for o in live),
            evidence_ids=list(dict.fromkeys(e for r in recs for e in r.evidence_ids)),
            claims=[ClaimRef(claim_id=r.claim_id, type=r.type, value=None) for r in recs],
            supersedes=notice_ids.get(prof.profile_id) or (prev_live[-1].overlay_id
                                                           if prev_live else None),
            source=Source(kind="live", trace_id="recap:" + ",".join(
                f"{o.story_id}:{o.revision}" for o in live)))
        if not recs:
            continue
        r = publisher.publish(ov, recs, now_ms)
        if r.published:
            store.add_deps([("story", f"{o.story_id}:{o.revision}", "overlay", ov.overlay_id)
                            for o in live])
            published.append(ov.overlay_id)
    return published
