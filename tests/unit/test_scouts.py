import asyncio
import json

import pytest

from secondlook.agents import fake_model
from secondlook.agents.llm import LLMResponse, ManagedLLM, ScriptedClient
from secondlook.agents.scouts import run_scout
from secondlook.contracts.agents_io import Moment
from secondlook.generator.engine import PERIODS
from secondlook.matchstate.tools import MatchTools
from tests.conftest import ms


@pytest.fixture
def env(fresh_world):
    w = fresh_world
    tools = MatchTools(w.store, w.checker, PERIODS)
    m = Moment(moment_id="mo-m001-067-goal", kind="goal", trigger_event_id=w.goal.event_id,
               window_from_ms=ms(w.goal) - 120_000, window_to_ms=ms(w.goal), now_ms=ms(w.goal),
               as_of_rev=w.goal.seq, team_id="IRV")
    tok = tools.issue_token(m)
    return w, tools, m, tok


def llm_for(w, responders):
    return ManagedLLM(ScriptedClient(responders), w.store, None, None, "fake")


async def scout(env, name, responders):
    w, tools, m, tok = env
    llm = llm_for(w, responders)
    return await run_scout(name, llm, tools, w.checker, w.store, m, tok), llm


def cand_json(**kw):
    base = {"scout": "pressure", "thesis_tag": "pressing_recovery", "thesis": "t", "claims": []}
    base.update(kw)
    return json.dumps({"candidates": [base]})


async def test_fake_scouts_produce_competing_verified_candidates(env):
    p, _ = await scout(env, "pressure", fake_model.responders())
    r, rllm = await scout(env, "rhythm", fake_model.responders())
    assert p.status == r.status == "ok"
    assert p.candidates[0].thesis_tag == "pressing_recovery"
    assert r.candidates[0].thesis_tag == "sustained_possession"
    for res in (p, r):
        assert res.stripped == [] and res.claim_records
        assert all(rec.status == "verified" for rec in res.claim_records.values())
        assert all(c.status == "proposed" and c.claims for c in res.candidates)
    chain = next(c for c in r.candidates[0].claims if c.type == "chain")
    assert chain.value["passes"] == 11
    # the rhythm scout used its tool round, and calls were logged
    outcomes = [c["agent"] for c in rllm.store.calls()]
    assert outcomes.count("rhythm") == 2


async def test_candidates_and_claim_deps_are_stored(env):
    w = env[0]
    r, _ = await scout(env, "pressure", fake_model.responders())
    cid = r.candidates[0].candidate_id
    assert w.store.get("candidate", cid).thesis_tag == "pressing_recovery"
    for c in r.candidates[0].claims:
        assert ("candidate", cid) in w.store.children("claim", c.claim_id)
        assert w.store.get("claim", c.claim_id).status == "verified"


async def test_failed_claims_are_stripped_and_empty_candidate_rejected(env):
    w, tools, m, tok = env
    good = {"claim_id": "a", "type": "score", "params": {"at_ms": ms(w.goal), "home": 1,
                                                         "away": 1}}
    bad = {"claim_id": "b", "type": "score", "params": {"at_ms": ms(w.goal), "home": 3,
                                                        "away": 0}}
    junk = {"claim_id": "c", "type": "count", "params": {"metric": "vibes"}, "value": 1}
    text = json.dumps({"candidates": [
        {"scout": "pressure", "thesis_tag": "pressing_recovery", "thesis": "x",
         "claims": [good, bad, junk]},
        {"scout": "pressure", "thesis_tag": "set_piece", "thesis": "y", "claims": [bad]}]})
    res, _ = await scout(env, "pressure", {"pressure": lambda r: LLMResponse(text=text)})
    assert res.proposed_claims == 4
    assert len(res.candidates) == 1 and len(res.candidates[0].claims) == 1
    assert [s.status for s in res.stripped] == ["failed", "unknown", "failed"]
    assert w.store.get("candidate", f"{m.moment_id}.pressure2").status == "rejected"


async def test_timeout_yields_timed_out_not_empty_evidence(env):
    async def slow(req):
        await asyncio.sleep(5)
    w, tools, m, tok = env
    llm = ManagedLLM(ScriptedClient({"pressure": slow}), w.store, None, None, "fake",
                     timeout_s=0.05)
    from secondlook.agents import runtime
    orig = runtime.run_agent

    async def quick(*a, **k):
        k["timeout_s"] = 0.05
        return await orig(*a, **k)
    import secondlook.agents.scouts as sc
    sc.run_agent = quick
    try:
        res = await sc.run_scout("pressure", llm, tools, w.checker, w.store, m, tok)
    finally:
        sc.run_agent = orig
    assert res.status == "timeout" and res.note == "pressure: timed out"
    assert res.candidates == [] and res.claim_records == {}


async def test_invalid_json_gets_one_repair_then_fails(env):
    seen = []

    def once_bad(req):
        seen.append(len(req.messages))
        return LLMResponse(text="not json") if len(seen) == 1 else LLMResponse(
            text=cand_json())
    res, _ = await scout(env, "pressure", {"pressure": once_bad})
    assert res.status == "ok" and seen == [1, 3]
    always = lambda req: LLMResponse(text="nope")  # noqa: E731
    res2, llm = await scout(env, "pressure", {"pressure": always})
    assert res2.status == "invalid" and "invalid" in res2.note
    assert [c["outcome"] for c in llm.store.calls()][-2:] == ["ok", "invalid"]


async def test_tool_rounds_are_capped_at_two(env):
    from secondlook.agents.llm import ToolCall
    n = {"c": 0}

    def greedy(req):
        n["c"] += 1
        tok = json.loads(req.messages[0]["content"])["moment_token"]
        if req.tools:
            return LLMResponse(tool_calls=[ToolCall(id=f"t{n['c']}", name="get_score",
                                                    arguments={"moment_token": tok})])
        return LLMResponse(text=cand_json())
    res, _ = await scout(env, "pressure", {"pressure": greedy})
    assert res.status == "ok" and n["c"] == 3  # 2 tool rounds + the final answer


async def test_tool_errors_go_back_to_the_model_not_the_run(env):
    from secondlook.agents.llm import ToolCall
    seen = {}

    def f(req):
        tool_msgs = [m for m in req.messages if m["role"] == "tool"]
        if not tool_msgs:
            return LLMResponse(tool_calls=[ToolCall(id="t1", name="get_event", arguments={
                "moment_token": "tok-bad", "event_id": "x"})])
        seen["content"] = tool_msgs[0]["content"]
        return LLMResponse(text=cand_json())
    res, _ = await scout(env, "pressure", {"pressure": f})
    assert res.status == "ok" and "ToolError" in seen["content"]


async def test_budget_outcome_is_reported(env):
    from secondlook.agents.llm import Budget
    w, tools, m, tok = env
    llm = ManagedLLM(ScriptedClient(fake_model.responders()), w.store, None, Budget(0, 0), "m",
                     live=True)
    res = await run_scout("pressure", llm, tools, w.checker, w.store, m, tok)
    assert res.status == "budget" and res.note == "pressure: skipped (call budget)"


async def test_player_scout_centres_on_the_acting_or_followed_player(env):
    w, tools, m, tok = env
    llm = llm_for(w, fake_model.responders())
    res = await run_scout("player", llm, tools, w.checker, w.store, m, tok,
                          extra={"followed_players": ["IRV-10"]})
    assert res.status == "ok" and res.stripped == []
    cand = res.candidates[0]
    assert cand.scout == "player" and cand.thesis_tag == "individual_action"
    assert {c.type for c in cand.claims} == {"event_fact", "involved"}
    assert all(r.status == "verified" for r in res.claim_records.values())
    # a followed player who did not act gets no story
    other = await run_scout("player", llm_for(w, fake_model.responders()), tools, w.checker,
                            w.store, m, tok, extra={"followed_players": ["KBY-06"]})
    assert other.status == "ok" and other.candidates == []


async def test_player_scout_failed_claims_stripped_and_timeout_noted(env):
    w, tools, m, tok = env
    wrong = json.dumps({"candidates": [{"scout": "player", "thesis_tag": "individual_action",
                                        "thesis": "x", "claims": [{
        "claim_id": "a", "type": "involved", "params": {
            "player_id": "KBY-06", "event_ids": [w.goal.event_id]}}]}]})
    res, _ = await scout(env, "player", {"player": lambda r: LLMResponse(text=wrong)})
    assert res.candidates == [] and [s.status for s in res.stripped] == ["failed"]
    res2, _ = await scout(env, "player", {"player": lambda r: LLMResponse(text="garbage")})
    assert res2.status == "invalid" and res2.note == "player: returned invalid output"
