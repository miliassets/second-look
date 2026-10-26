"""Reopen a moment after a correction: the Editor runs first with the surviving candidates
(design §4 step 6)."""
from __future__ import annotations

from ..agents.scouts import ScoutResult
from ..agents.workflow import MomentOutcome, Services, run_reopen
from ..contracts.agents_io import Candidate, Moment
from ..contracts.claims import Claim, ClaimRecord
from ..moments.factual import story_id_for


def surviving_results(svc: Services, moment: Moment) -> tuple[list[ScoutResult],
                                                              dict[str, ClaimRecord]]:
    store = svc.store
    by_scout: dict[str, ScoutResult] = {}
    for cand in store.list_kind("candidate", moment_id=moment.moment_id):
        keep, recs = [], {}
        for c in cand.claims:
            rec = store.get("claim", c.claim_id)
            if rec is not None and rec.status == "verified":
                keep.append(c)
                recs[c.claim_id] = rec
        if not keep:
            continue
        cand2 = Candidate(**{**cand.model_dump(), "claims": [k.model_dump() for k in keep],
                             "status": "proposed"})
        r = by_scout.setdefault(cand.scout, ScoutResult(scout=cand.scout, status="ok"))
        r.candidates.append(cand2)
        r.claim_records.update(recs)
    return list(by_scout.values()), {}


def system_candidate(svc: Services, moment: Moment) -> tuple[ScoutResult | None, dict]:
    """If the trigger event was voided/corrected, record that as verified facts."""
    if not moment.trigger_event_id:
        return None, {}
    ev = svc.store.get_event(moment.trigger_event_id)
    if ev is None or ev.status == "active":
        return None, {}
    cid = f"{moment.moment_id}.system"
    claims = [
        Claim(claim_id=f"{cid}.c1", type="event_fact", params={
            "event_id": ev.event.event_id, "field": "status", "value": ev.status}),
        Claim(claim_id=f"{cid}.c2", type="score", params={
            "at_ms": moment.now_ms, "home": None, "away": None}),
    ]
    recs = {}
    # fill the score assertion with the checker's own current value
    tmp = svc.checker.verify(claims[1].model_copy(update={"params": {
        **claims[1].params, "home": 0, "away": 0}}), moment.now_ms, record=False)
    if tmp.value:
        claims[1].params.update(home=tmp.value["home"], away=tmp.value["away"])
    good = []
    for c in claims:
        rec = svc.checker.verify(c, moment.now_ms, "candidate", cid)
        if rec.status == "verified":
            recs[c.claim_id] = rec
            good.append(c)
    if not good:
        return None, {}
    cand = Candidate(candidate_id=cid, moment_id=moment.moment_id, scout="system",
                     thesis_tag="correction",
                     thesis="The triggering event was corrected after review.", claims=good,
                     as_of_rev=svc.store.current_rev())
    svc.store.put("candidate", cand)
    r = ScoutResult(scout="system", status="ok", candidates=[cand], claim_records=recs)
    return r, recs


async def reopen_moment(svc: Services, moment: Moment, reasons: dict[str, str],
                        notice_ids: dict[str, str], checkpoint_dir=None) -> MomentOutcome | None:
    store = svc.store
    stories = [s for s in store.list_kind("story", moment_id=moment.moment_id)]
    if not stories:
        return None  # only a factual card was on air; the notice is enough
    old = max(stories, key=lambda s: s.revision)
    rev = store.current_rev()
    m2 = moment.model_copy(update={"as_of_rev": rev, "status": "reopened"})
    store.put("moment", m2)
    token = svc.tools.issue_token(m2)
    results, _ = surviving_results(svc, m2)
    sysr, _ = system_candidate(svc, m2)
    if sysr:
        results.append(sysr)
    if not notice_ids:  # e.g. triggered by the race guard: supersede the notice already on air
        for ov in store.list_kind("overlay", story_id=story_id_for(moment.moment_id),
                                  kind="correction_notice"):
            if store.get_status("overlay", ov.overlay_id) == "live":
                notice_ids[ov.profile_id] = ov.overlay_id
    msg = {"moment": m2.model_dump(mode="json"), "token": token,
           "results": [r.model_dump(mode="json") for r in results],
           "revision": old.revision + 1, "supersedes_revision": old.revision,
           "supersedes_by_profile": notice_ids,
           "reopen": {"previous_story": old.text_en, "invalidated": reasons,
                      "story_id": story_id_for(moment.moment_id)}}
    return await run_reopen(svc, msg, checkpoint_dir)
