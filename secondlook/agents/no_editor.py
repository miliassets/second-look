"""Ablation: scouts run, no Editor; the first candidate whose claims all verified is published
(design §7). Producers and the checker are unchanged."""
from __future__ import annotations

import asyncio
import time

from ..contracts.agents_io import Moment, Story
from ..moments.factual import story_id_for
from .producers import ProducerResult, run_producer
from .scouts import run_scout
from .workflow import Services, publish_outputs


def mechanical_template(cand, records, team: str | None) -> tuple[str, list[str]] | None:
    by_type = {c.type: c for c in cand.claims}
    if "chain" in by_type and team:
        c = by_type["chain"].claim_id
        return f"{{t:{team}}} moved the ball through {{{c}}} passes, then scored.", [c]
    if "count" in by_type and team:
        c = by_type["count"].claim_id
        return f"{{t:{team}}} won the ball back {{{c}}} times in the spell before this.", [c]
    if "involved" in by_type or "event_fact" in by_type:
        c = (by_type.get("involved") or by_type["event_fact"]).claim_id
        pid = (by_type.get("involved") or by_type["event_fact"]).params.get("player_id")
        if pid:
            return f"{{p:{pid}}} was involved, backed by {{{c}}}.", [c]
    if "phase" in by_type and team:
        c = by_type["phase"].claim_id
        return f"Rhythm: {{t:{team}}} in {{{c}}}.", [c]
    return None


async def run_no_editor(svc: Services, moment: Moment, token: str) -> dict:
    t0 = time.time()
    results = await asyncio.gather(*[run_scout(n, svc.llm, svc.tools, svc.checker, svc.store,
                                               moment, token) for n in svc.scouts])
    proposed = sum(r.proposed_claims for r in results)
    stripped = sum(len(r.stripped) for r in results)
    out = {"status": "fallback", "proposed": proposed, "stripped": stripped, "story": None,
           "overlays": [], "calls": 0}
    bad = {s.owner_id for r in results for s in r.stripped}
    cands = [c for r in results for c in r.candidates if c.candidate_id not in bad]
    for cand in cands:
        recs = {k: v for r in results for k, v in r.claim_records.items()}
        tpl = mechanical_template(cand, recs, moment.team_id)
        if tpl is None:
            continue
        text, ids = tpl
        story = Story(story_id=story_id_for(moment.moment_id), revision=1,
                      moment_id=moment.moment_id, claim_ids=ids, text_en=text,
                      thesis_tag=cand.thesis_tag)
        svc.store.put("story", story)
        svc.store.add_deps([("claim", c, "story", f"{story.story_id}:1") for c in ids])
        producers = {}
        for prof in svc.profiles:
            pr = await run_producer(svc.llm, svc.store, svc.roster, svc.periods, prof, story,
                                    recs, {"moment_id": moment.moment_id, "kind": moment.kind,
                                           "team_id": moment.team_id},
                                    sorted({p for p in [x.params.get("player_id") for x in
                                                        cand.claims] if p}))
            producers[prof.profile_id] = pr.model_dump(mode="json")
        overlays, _ = publish_outputs(svc, moment, story, recs, producers, t0, {})
        out.update(status="ok" if overlays else "fallback", story=story, overlays=overlays)
        return out
    return out


__all__ = ["run_no_editor", "ProducerResult"]
