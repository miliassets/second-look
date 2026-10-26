"""Producers: one instance per viewer profile; each may only select from the story's claims."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import config
from ..checker.roster import Roster
from ..checker.text import check_text
from ..contracts.agents_io import ProducerLLMOutput, Story, ViewerOutput
from ..contracts.claims import ClaimRecord
from ..contracts.prefs import ViewerProfile
from ..ledger.store import LedgerStore
from .llm import ManagedLLM
from .runtime import prompt, run_agent

MAX_FAN_LAYERS = 2


class ProducerResult(BaseModel):
    profile_id: str
    status: str  # ok | timeout | invalid | budget | error | rejected | skipped
    output: ViewerOutput | None = None
    text: str = ""
    spans: list = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


async def run_producer(llm: ManagedLLM, store: LedgerStore, roster: Roster,
                       periods: list[dict[str, int]], profile: ViewerProfile, story: Story,
                       records: dict[str, ClaimRecord], moment_view: dict,
                       evidence_players: list[str]) -> ProducerResult:
    res = ProducerResult(profile_id=profile.profile_id, status="error")
    claims = [records[c] for c in story.claim_ids]
    payload = {
        "profile": profile.model_dump(), "moment": moment_view, "evidence_players": evidence_players,
        "story": {"story_id": story.story_id, "claim_ids": story.claim_ids,
                  "text_en": story.text_en},
        "claims": [{"claim_id": c.claim_id, "type": c.type, "params": c.params, "value": c.value}
                   for c in claims]}
    run = await run_agent(llm, f"producer-{profile.profile_id}", prompt("producer"), payload,
                          ProducerLLMOutput, None, None, story.moment_id, max_tool_rounds=0,
                          max_tokens=250, timeout_s=config.TIMEOUT_PRODUCER)
    if run.outcome != "ok" or run.output is None:
        res.status = run.outcome
        return res
    out: ProducerLLMOutput = run.output  # type: ignore[assignment]
    errors = []
    if not out.claim_ids or not set(out.claim_ids) <= set(story.claim_ids):
        errors.append("claim_ids must be a non-empty subset of the story's claims")
    else:
        chk = check_text(out.template, [records[i] for i in out.claim_ids], roster, profile.lang,
                         profile.mode, store=store, periods=periods)
        head = check_text(out.headline, [], roster, profile.lang, profile.mode)
        errors += chk.errors + [f"headline: {e}" for e in head.errors]
        if not errors:
            res.text, res.spans = chk.text, [s.model_dump() for s in chk.spans]
    layers = out.layers[:MAX_FAN_LAYERS] if profile.mode == "fan" else out.layers
    if errors:
        res.status, res.errors = "rejected", errors
        return res
    res.status = "ok"
    res.output = ViewerOutput(
        output_id=f"vo-{story.story_id.removeprefix('st-')}-r{story.revision}-{profile.profile_id}",
        story_id=story.story_id, story_revision=story.revision, profile_id=profile.profile_id,
        lang=profile.lang, template=out.template, headline=out.headline,
        claim_ids=out.claim_ids, layers=list(layers), status="approved")
    return res
