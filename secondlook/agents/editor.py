"""Editor: compares verified candidates, rules, and writes a story template (design §3.4)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .. import config
from ..checker.roster import Roster
from ..checker.text import check_text
from ..contracts.agents_io import EditorLLMOutput, Moment, Rejection, Story, Verdict
from ..contracts.claims import ClaimRecord
from ..ledger.store import LedgerStore
from ..matchstate.tools import MatchTools
from ..moments.factual import story_id_for
from .llm import ManagedLLM
from .runtime import prompt, run_agent
from .scouts import ScoutResult

ScoutRunner = Callable[[str, str, str], Awaitable[ScoutResult]]  # (scout, question, suffix)


@dataclass
class EditorDecision:
    status: str  # story | fallback
    verdict: Verdict | None = None
    story: Story | None = None
    records: dict[str, ClaimRecord] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    editor_calls: int = 0
    evidence_round: bool = False
    reason: str = ""


def _candidate_view(c, records) -> dict:
    return {"candidate_id": c.candidate_id, "scout": c.scout, "thesis_tag": c.thesis_tag,
            "thesis": c.thesis, "claims": [
                {"claim_id": x.claim_id, "type": x.type, "params": x.params,
                 "value": records[x.claim_id].value} for x in c.claims]}


async def edit_moment(llm: ManagedLLM, tools: MatchTools, store: LedgerStore, roster: Roster,
                      periods: list[dict[str, int]], moment: Moment, token: str,
                      results: list[ScoutResult], rerun_scout: ScoutRunner,
                      revision: int = 1, supersedes_revision: int | None = None,
                      reopen: dict | None = None) -> EditorDecision:
    dec = EditorDecision("fallback")
    notes = [r.note for r in results if r.note]
    errors: list[str] = []
    for _ in range(2):  # at most two Editor calls (one evidence round)
        cands = [c for r in results for c in r.candidates]
        records: dict[str, ClaimRecord] = {}
        for r in results:
            records.update(r.claim_records)
        dec.records, dec.notes = records, notes
        if not cands:
            dec.reason = "no verified candidate"
            return dec
        payload = {"moment": {"moment_id": moment.moment_id, "kind": moment.kind,
                              "team_id": moment.team_id, "detail": moment.detail},
                   "candidates": [_candidate_view(c, records) for c in cands],
                   "notes": notes, "evidence_round_used": dec.evidence_round,
                   "previous_errors": errors, "reopen": reopen}
        run = await run_agent(llm, "editor", prompt("editor"), payload, EditorLLMOutput, tools,
                              ["verify_claim", "get_dossier"], moment.moment_id,
                              max_tool_rounds=1, max_tokens=500, timeout_s=config.TIMEOUT_EDITOR)
        dec.editor_calls += 1
        if run.outcome != "ok" or run.output is None:
            dec.reason = f"editor: {run.outcome}"
            notes.append(dec.reason)
            return dec
        out: EditorLLMOutput = run.output  # type: ignore[assignment]
        req = out.evidence_request
        if req is not None and not dec.evidence_round and dec.editor_calls < 2:
            dec.evidence_round = True
            fresh = await rerun_scout(req.to_scout, req.question, "r2")
            results = [r for r in results if r.scout != req.to_scout] + [fresh]
            if fresh.note:
                notes.append(fresh.note)
            continue
        chosen = next((c for c in cands if c.candidate_id == out.chosen_candidate_id), None)
        if chosen is None:
            dec.reason = "editor chose no candidate"
            return dec
        if chosen.scout == "system":  # only the correction facts survive: the notice stands
            dec.reason = "notice stands"
            return dec
        allowed = {x.claim_id for x in chosen.claims}
        for c in cands:
            if c.scout == "system":
                allowed |= {x.claim_id for x in c.claims}
        errors = []
        if not out.claim_ids or not set(out.claim_ids) <= allowed:
            errors.append("claim_ids must be a non-empty subset of the chosen candidate's claims")
        else:
            chk = check_text(out.text_en, [records[i] for i in out.claim_ids], roster, "en",
                             "analyst", store=store, periods=periods)
            if not chk.ok:
                errors += chk.errors
        if errors:
            if dec.editor_calls < 2:
                continue
            dec.reason = "editor story failed the checker: " + "; ".join(errors)[:200]
            return dec
        rej = {r.candidate_id: r for r in out.rejected}
        rejected = [rej.get(c.candidate_id) or Rejection(candidate_id=c.candidate_id,
                                                          reason="not chosen")
                    for c in cands if c.candidate_id != chosen.candidate_id]
        vid = f"vd-{moment.moment_id.removeprefix('mo-')}-r{revision}"
        dec.verdict = Verdict(verdict_id=vid, moment_id=moment.moment_id,
                              chosen_candidate_id=chosen.candidate_id, rejected=rejected,
                              notes=notes, as_of_rev=store.current_rev())
        dec.story = Story(story_id=story_id_for(moment.moment_id), revision=revision,
                          moment_id=moment.moment_id, verdict_id=vid, claim_ids=out.claim_ids,
                          text_en=out.text_en, thesis_tag=chosen.thesis_tag,
                          supersedes_revision=supersedes_revision)
        dec.status = "story"
        return dec
    dec.reason = "editor exhausted its calls"
    return dec


def persist(store: LedgerStore, moment: Moment, results: list[ScoutResult],
            dec: EditorDecision) -> None:
    chosen = dec.verdict.chosen_candidate_id if dec.verdict else None
    for r in results:
        for c in r.candidates:
            store.set_status("candidate", c.candidate_id,
                             "chosen" if c.candidate_id == chosen else "rejected")
    if dec.status == "story" and dec.verdict and dec.story:
        store.put("verdict", dec.verdict)
        store.put("story", dec.story)
        key = f"{dec.story.story_id}:{dec.story.revision}"
        store.add_deps([("claim", cid, "story", key) for cid in dec.story.claim_ids])
    else:
        store.set_status("moment", moment.moment_id, "fallback")
