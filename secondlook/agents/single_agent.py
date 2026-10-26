"""Comparison baseline: ONE agent given all tools and asked for the story plus both viewer
outputs in a single structured response (design §7). Same checker, same tools, same dossier."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .. import config
from ..checker.text import check_text
from ..contracts.agents_io import Moment, Story, ThesisTag, ViewerOutput
from ..contracts.claims import Claim, ClaimRecord
from ..moments.factual import story_id_for
from .producers import ProducerResult
from .runtime import prompt, run_agent
from .scouts import SCOUT_TOOLS
from .workflow import Services, publish_outputs


class SingleLLMOutput(BaseModel):
    thesis_tag: ThesisTag | None = None
    claims: list[Claim] = Field(default_factory=list)
    analyst_template: str = ""
    analyst_headline: str = ""
    fan_template: str = ""
    fan_headline: str = ""
    layers: list[str] = Field(default_factory=list)


@dataclass
class SingleResult:
    status: str
    story: Story | None = None
    overlays: list[dict] = field(default_factory=list)
    proposed: int = 0
    stripped: int = 0
    calls: int = 0
    errors: list[str] = field(default_factory=list)


async def run_single(svc: Services, moment: Moment, token: str) -> SingleResult:
    t0 = time.time()
    res = SingleResult("error")
    dossier = svc.tools.get_dossier(token)
    base = {"moment_token": token, "moment_kind": moment.kind, "dossier": dossier,
            "followed_players": sorted({p.followed_player_id for p in svc.profiles
                                        if p.followed_player_id})}
    errors: list[str] = []
    for attempt in range(2):  # one revision allowed
        payload = {**base, "previous_errors": errors}
        run = await run_agent(svc.llm, "single", prompt("single"), payload, SingleLLMOutput,
                              svc.tools, SCOUT_TOOLS, moment.moment_id, max_tool_rounds=2,
                              max_tokens=700, timeout_s=config.TIMEOUT_SINGLE)
        res.calls += len(run.call_ids)
        if run.outcome != "ok" or run.output is None:
            res.status = run.outcome
            return res
        out: SingleLLMOutput = run.output  # type: ignore[assignment]
        if out.thesis_tag is None or not out.claims:
            res.status = "no_story"
            return res
        sid = story_id_for(moment.moment_id)
        recs: dict[str, ClaimRecord] = {}
        res.proposed = len(out.claims)
        res.stripped = 0
        renamed = {}
        for i, c in enumerate(out.claims, start=1):
            new = f"{moment.moment_id}.single.c{i}"
            renamed[c.claim_id] = new
            rec = svc.checker.verify(c.model_copy(update={"claim_id": new}), moment.now_ms,
                                     "candidate", f"{moment.moment_id}.single")
            if rec.status == "verified":
                recs[new] = rec
            else:
                res.stripped += 1

        def fix(tpl: str, renamed=renamed) -> str:
            for old, new in renamed.items():
                tpl = tpl.replace("{" + old + "}", "{" + new + "}")
            return tpl
        producers: dict[str, dict] = {}
        story_ids: list[str] = []
        errors = []
        for prof in svc.profiles:
            tpl, head = (fix(out.analyst_template), out.analyst_headline) if prof.mode == \
                "analyst" else (fix(out.fan_template), out.fan_headline)
            ids = [c for c in recs if "{" + c + "}" in tpl]
            chk = check_text(tpl, [recs[c] for c in ids], svc.roster, prof.lang, prof.mode,
                             store=svc.store, periods=svc.periods) if ids else None
            hd = check_text(head, [], svc.roster, prof.lang, prof.mode)
            if chk is None or not chk.ok or not hd.ok:
                errors += (chk.errors if chk else ["no claim placeholders"]) + hd.errors
                producers[prof.profile_id] = {"profile_id": prof.profile_id, "status": "rejected"}
                continue
            story_ids += ids
            vo = ViewerOutput(
                output_id=f"vo-{sid.removeprefix('st-')}-r1-{prof.profile_id}", story_id=sid,
                story_revision=1, profile_id=prof.profile_id, lang=prof.lang, template=tpl,
                headline=head, claim_ids=ids, layers=[x for x in out.layers if x in (
                    "chain", "marker", "zone", "player_halo")], status="approved")
            producers[prof.profile_id] = ProducerResult(
                profile_id=prof.profile_id, status="ok", output=vo, text=chk.text,
                spans=[s.model_dump() for s in chk.spans]).model_dump(mode="json")
        if not story_ids and attempt == 0:
            continue  # revise once
        if not story_ids:
            res.status, res.errors = "rejected", errors
            return res
        story = Story(story_id=sid, revision=1, moment_id=moment.moment_id,
                      claim_ids=list(dict.fromkeys(story_ids)),
                      text_en=out.analyst_template, thesis_tag=out.thesis_tag)
        svc.store.put("story", story)
        svc.store.add_deps([("claim", c, "story", f"{sid}:1") for c in story.claim_ids])
        overlays, _ = publish_outputs(svc, moment, story, recs, producers, t0, {})
        res.status, res.story, res.overlays = "ok", story, overlays
        return res
    return res
