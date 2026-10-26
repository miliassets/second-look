"""Pressure and Rhythm scouts: propose evidence-linked candidates; the checker strips what the
data cannot support (design §3.4)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import config
from ..checker.verify import Checker
from ..contracts.agents_io import Candidate, Moment, ScoutOutput
from ..contracts.claims import Claim, ClaimRecord
from ..ledger.store import LedgerStore
from ..matchstate.tools import MatchTools
from .llm import ManagedLLM
from .runtime import prompt, run_agent

SCOUT_TOOLS = ["get_events", "get_event", "get_possession", "get_stat", "compare_windows",
               "get_physical", "get_score", "verify_claim"]


class ScoutResult(BaseModel):
    scout: str
    status: str  # ok | timeout | invalid | budget | error
    candidates: list[Candidate] = Field(default_factory=list)  # claims all verified
    claim_records: dict[str, ClaimRecord] = Field(default_factory=dict)
    stripped: list[ClaimRecord] = Field(default_factory=list)  # failed/unknown claims removed
    proposed_claims: int = 0
    note: str = ""  # e.g. "pressure: timed out" (never "no pressure")
    call_ids: list[str] = Field(default_factory=list)


async def run_scout(scout: str, llm: ManagedLLM, tools: MatchTools, checker: Checker,
                    store: LedgerStore, moment: Moment, token: str,
                    extra: dict | None = None, id_suffix: str = "") -> ScoutResult:
    dossier = tools.get_dossier(token)
    payload = {"moment_token": token, "moment_kind": moment.kind, "dossier": dossier,
               **(extra or {})}
    run = await run_agent(llm, scout, prompt(scout), payload, ScoutOutput, tools, SCOUT_TOOLS,
                          moment.moment_id, max_tool_rounds=2, max_tokens=400,
                          timeout_s=config.TIMEOUT_SCOUT)
    res = ScoutResult(scout=scout, status=run.outcome, call_ids=run.call_ids)
    if run.outcome != "ok" or run.output is None:
        verb = {"timeout": "timed out", "invalid": "returned invalid output",
                "budget": "skipped (call budget)", "error": "failed"}[run.outcome]
        res.note = f"{scout}: {verb}"
        return res
    out: ScoutOutput = run.output  # type: ignore[assignment]
    for i, cand in enumerate(out.candidates, start=1):
        cid = f"{moment.moment_id}.{scout}{id_suffix}{i}"
        cand = cand.model_copy(update={"candidate_id": cid, "moment_id": moment.moment_id,
                                       "scout": scout, "as_of_rev": store.current_rev()})
        good: list[Claim] = []
        for j, c in enumerate(cand.claims, start=1):
            res.proposed_claims += 1
            local = f"{cid}.c{j}"
            rec = checker.verify(c.model_copy(update={"claim_id": local}), moment.now_ms,
                                 "candidate", cid)
            if rec.status == "verified":
                good.append(c.model_copy(update={"claim_id": local}))
                res.claim_records[local] = rec
            else:
                res.stripped.append(rec)
        cand = cand.model_copy(update={"claims": good})
        if not good:
            cand = cand.model_copy(update={"status": "rejected"})
            store.put("candidate", cand)
            continue
        store.put("candidate", cand)
        store.add_deps([("claim", c.claim_id, "candidate", cid) for c in good])
        res.candidates.append(cand)
    return res
