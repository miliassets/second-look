"""Factual cards: the deterministic fallback that always works (design §3.6).

Built only from `event_fact`, `score` and `phase` claims, checked like everything else.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ..checker.roster import Roster
from ..checker.text import TextResult, check_text
from ..checker.verify import Checker
from ..contracts.agents_io import Moment
from ..contracts.claims import Claim, ClaimRecord
from ..contracts.overlay import ClaimRef, MarkerLayer, Overlay
from ..contracts.overlay import HaloLayer as PlayerHaloLayer
from ..contracts.prefs import ViewerProfile
from ..ledger.store import LedgerStore

TEMPLATES = Path(__file__).parent / "templates"


@cache
def templates(lang: str) -> dict:
    return json.loads((TEMPLATES / f"{lang}.json").read_text())


@dataclass
class FactualCard:
    overlay: Overlay
    claims: list[ClaimRecord]
    text: TextResult


def story_id_for(moment_id: str) -> str:
    return "st-" + moment_id.removeprefix("mo-")


def factual_overlay_id(moment_id: str, profile_id: str) -> str:
    return f"ov-{moment_id.removeprefix('mo-')}-factual-{profile_id}"


def _claims_for(m: Moment, store: LedgerStore) -> tuple[list[Claim], dict[str, str]]:
    """Claims plus the mapping from generic template keys to placeholders."""
    ev = store.get_event(m.trigger_event_id) if m.trigger_event_id else None
    cid = m.moment_id + ".f"
    claims: list[Claim] = []
    keys: dict[str, str] = {}

    def fact(name, event, field, value):
        claims.append(Claim(claim_id=f"{cid}.{name}", type="event_fact",
                            params={"event_id": event.event.event_id, "field": field, "value": value}))

    def score(name, at_ms):
        claims.append(Claim(claim_id=f"{cid}.{name}", type="score", params={
            "at_ms": at_ms, "home": None, "away": None}))
        keys["score"] = "{" + f"{cid}.{name}" + "}"

    if m.kind == "goal" and ev:
        e = ev.event
        fact("team", ev, "team_id", e.team_id)
        fact("player", ev, "player_id", e.player_id)
        keys.update(p="{p:" + e.player_id + "}", t="{t:" + e.team_id + "}",
                    time="{time:" + e.event_id + "}")
        score("score", ev.match_ms)
    elif m.kind == "correction" and ev:
        tid = ev.event.team_id
        fact("team", ev, "team_id", tid)
        keys.update(t="{t:" + tid + "}")
    elif m.kind == "disallowed_goal" and ev:
        tid = ev.event.team_id  # the correction event carries the team; the goal itself is void
        fact("team", ev, "team_id", tid)
        keys.update(t="{t:" + tid + "}")
        score("score", ev.match_ms)
    elif m.kind == "fast_shot" and ev:
        e = ev.event
        fact("player", ev, "player_id", e.player_id)
        fact("team", ev, "team_id", e.team_id)
        fact("speed", ev, "qualifiers.speed_kmh", e.qualifiers["speed_kmh"])
        keys.update(p="{p:" + e.player_id + "}", t="{t:" + e.team_id + "}",
                    speed="{" + f"{cid}.speed" + "}")
    elif m.kind == "card" and ev:
        e = ev.event
        fact("player", ev, "player_id", e.player_id)
        fact("team", ev, "team_id", e.team_id)
        fact("card", ev, "outcome", e.outcome)
        keys.update(p="{p:" + e.player_id + "}", t="{t:" + e.team_id + "}",
                    card="{" + f"{cid}.card" + "}", time="{time:" + e.event_id + "}")
    elif m.kind == "tactical_shift" and ev:
        e = ev.event
        fact("team", ev, "team_id", e.team_id)
        keys.update(t="{t:" + e.team_id + "}", time="{time:" + e.event_id + "}")
    elif m.kind == "phase_change":
        t = m.team_id
        claims.append(Claim(claim_id=f"{cid}.phase", type="phase", params={
            "team_id": t, "from_ms": m.now_ms - 180_000, "to_ms": m.now_ms,
            "label": m.detail["label"]}))
        keys.update(t="{t:" + t + "}", phase="{" + f"{cid}.phase" + "}")
    elif m.kind in ("half_time", "full_time"):
        score("score", m.now_ms)
    return claims, keys


def _fill(tpl: str, keys: dict[str, str]) -> str:
    out = tpl
    for k, v in keys.items():
        out = out.replace("{" + k + "}", v)
    return out


def build_factual_card(m: Moment, profile: ViewerProfile, store: LedgerStore, checker: Checker,
                       roster: Roster, periods: list[dict[str, int]]) -> FactualCard | None:
    tpls = templates(profile.lang)
    tpl = tpls[profile.mode].get(m.kind)
    if tpl is None:
        return None
    claims, keys = _claims_for(m, store)
    if "score" in keys:
        # the score claim asserts the checker's own number: fill from a dry computation
        for c in claims:
            if c.type == "score":
                tmp = checker.verify(c.model_copy(update={"params": {
                    **c.params, "home": 0, "away": 0}}), m.now_ms, "factual", m.moment_id,
                    record=False)
                c.params["home"], c.params["away"] = tmp.value["home"], tmp.value["away"]
    recs = []
    for c in claims:
        if c.type == "event_fact" and c.params["value"] is None:
            return None
        recs.append(checker.verify(c, m.now_ms, "factual", m.moment_id))
    template = _fill(tpl, keys)
    res = check_text(template, recs, roster, profile.lang, profile.mode, store=store,
                     periods=periods)
    head = check_text(tpls["headline"][m.kind], [], roster, profile.lang, profile.mode)
    if not res.ok or not head.ok:
        return None
    ev = store.get_event(m.trigger_event_id) if m.trigger_event_id else None
    layers = []
    evidence = list(dict.fromkeys(i for r in recs for i in r.evidence_ids))
    if ev and ev.event.x is not None:
        pos = (ev.event.end_x, ev.event.end_y) if m.kind == "goal" and ev.event.end_x is not None \
            else (ev.event.x, ev.event.y)
        layers.append(MarkerLayer(at=pos, label="", event_id=ev.event.event_id))
    if ev and ev.event.player_id and m.kind in ("goal", "fast_shot", "card"):
        layers.append(PlayerHaloLayer(player_id=ev.event.player_id))
    if profile.mode == "fan":
        layers = layers[:2]
    overlay = Overlay(
        overlay_id=factual_overlay_id(m.moment_id, profile.profile_id),
        story_id=story_id_for(m.moment_id), revision=1, kind="factual",
        viewer_mode=profile.mode, profile_id=profile.profile_id, lang=profile.lang,
        show_at_match_ms=m.now_ms, duration_ms=8000, slot="lower_third", priority=1,
        headline=head.text, text=res.text, spans=res.spans, layers=layers,
        evidence_ids=evidence,
        claims=[ClaimRef(claim_id=r.claim_id, type=r.type, value=_plain(r))
                for r in recs if r.claim_id in res.claims_used])
    return FactualCard(overlay=overlay, claims=recs, text=res)


def _plain(r: ClaimRecord):
    v = r.value
    if isinstance(v, dict):
        if "passes" in v:
            return v["passes"]
        if "home" in v:
            return f"{v['home']}-{v['away']}"
        if "delta" in v:
            return v["delta"]
        return None
    return v
