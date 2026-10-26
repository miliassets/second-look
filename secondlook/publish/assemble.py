"""Turn an approved ViewerOutput into overlay JSON with resolved layer coordinates (design §5)."""
from __future__ import annotations

from ..contracts.agents_io import Moment, Story, ViewerOutput
from ..contracts.claims import ClaimRecord
from ..contracts.overlay import (
    ChainLayer,
    ClaimRef,
    HaloLayer,
    MarkerLayer,
    Overlay,
    Source,
    Span,
    ZoneLayer,
)
from ..contracts.prefs import ViewerProfile
from ..ledger.store import LedgerStore
from ..moments.factual import factual_overlay_id
from ..stats.engine import StatsEngine


def explanation_overlay_id(story: Story, profile_id: str) -> str:
    return f"ov-{story.story_id.removeprefix('st-')}-r{story.revision}-{profile_id}"


def _plain(r: ClaimRecord):
    v = r.value
    if isinstance(v, dict):
        return v.get("passes") if "passes" in v else (
            f"{v['home']}-{v['away']}" if "home" in v else v.get("delta"))
    return v


def assemble_overlay(story: Story, vo: ViewerOutput, profile: ViewerProfile, text: str,
                     spans: list[dict], records: dict[str, ClaimRecord], moment: Moment,
                     store: LedgerStore, show_at_ms: int, latency_ms: int, headline: str,
                     supersedes: str | None, provider: str = "fake") -> Overlay:
    used = [records[c] for c in vo.claim_ids]
    evidence = list(dict.fromkeys(e for c in used for e in c.evidence_ids))
    stats = StatsEngine.from_store(store, to_ms=moment.now_ms)
    layers: list = []
    want = list(vo.layers)
    chain = next((c for c in used if c.type == "chain"), None)
    trig = store.get_event(moment.trigger_event_id) if moment.trigger_event_id else None
    actors = {s.event.player_id for e in evidence if (s := store.get_event(e)) and s.event.player_id}
    for kind in want:
        if kind == "chain" and chain:
            passes = [s.event for e in chain.evidence_ids
                      if (s := store.get_event(e)) and s.event.type == "pass"
                      and s.event.outcome == "complete" and s.event.x is not None]
            if len(passes) >= 2:
                pts = [(p.x, p.y) for p in passes] + [(passes[-1].end_x, passes[-1].end_y)]
                layers.append(ChainLayer(points=pts, event_ids=[p.event_id for p in passes]))
        elif kind == "marker" and trig and trig.event.x is not None:
            e = trig.event
            at = (e.end_x, e.end_y) if e.type in ("goal", "shot") and e.end_x is not None \
                else (e.x, e.y)
            layers.append(MarkerLayer(at=at, label="", event_id=e.event_id))
        elif kind == "zone" and moment.team_id and trig:
            d = stats.attack_dir(moment.team_id, trig.event.period)
            layers.append(ZoneLayer(rect=(70 if d >= 0 else 0, 0, 35, 68),
                                    label_key="attacking_third"))
        elif kind == "player_halo":
            who = profile.followed_player_id if profile.followed_player_id in actors else (
                trig.event.player_id if trig and trig.event.player_id in actors else None)
            if who:
                layers.append(HaloLayer(player_id=who))
    if profile.mode == "fan":
        layers = layers[:2]
    return Overlay(
        overlay_id=explanation_overlay_id(story, profile.profile_id), story_id=story.story_id,
        revision=story.revision, kind="explanation", viewer_mode=profile.mode,
        profile_id=profile.profile_id, lang=profile.lang, show_at_match_ms=show_at_ms,
        duration_ms=15000 if profile.mode == "analyst" else 12000,
        slot="lower_third", priority=2, headline=headline, text=text,
        spans=[Span(**s) for s in spans], badges=[], layers=layers, evidence_ids=evidence,
        claims=[ClaimRef(claim_id=c.claim_id, type=c.type, value=_plain(c)) for c in used],
        supersedes=supersedes or (factual_overlay_id(moment.moment_id, profile.profile_id)
                                  if story.revision == 1 else None),
        source=Source(kind="live", trace_id=f"{provider}:{story.story_id}:{story.revision}",
                      latency_ms=latency_ms))
