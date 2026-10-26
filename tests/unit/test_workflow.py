import json

import pytest

from secondlook.agents import fake_model
from secondlook.agents.llm import LLMResponse, ManagedLLM, ScriptedClient
from secondlook.agents.workflow import Services, resume_moment, run_moment
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
    w.store.put("moment", m)
    tok = tools.issue_token(m)
    return w, tools, m, tok


def svc(w, tools, responders, **kw):
    llm = ManagedLLM(ScriptedClient(responders), w.store, None, None, "fake")
    return Services(llm, tools, w.checker, w.store, w.roster, PERIODS, **kw)


def agent_calls(w, agent):
    return [c for c in w.store.calls() if c["agent"] == agent]


async def test_editor_picks_the_verified_candidate_and_writes_a_valid_story(env, tmp_path):
    w, tools, m, tok = env
    out = await run_moment(svc(w, tools, fake_model.responders()), m, tok, tmp_path)
    assert out.status == "story" and out.evidence_round is False and out.editor_calls == 1
    st, vd = out.story, out.verdict
    assert st["story_id"] == "st-m001-067-goal" and st["revision"] == 1
    assert st["thesis_tag"] == "sustained_possession"
    assert vd["chosen_candidate_id"] == "mo-m001-067-goal.rhythm1"
    assert sorted(r["candidate_id"] for r in vd["rejected"]) == [
        "mo-m001-067-goal.player1", "mo-m001-067-goal.pressure1"]
    assert all(cid in out.claims and out.claims[cid]["status"] == "verified"
               for cid in st["claim_ids"])
    assert "11" not in st["text_en"]  # numbers only via placeholders
    # persisted: verdict, story, candidate statuses, claim->story deps
    assert w.store.get("story", "st-m001-067-goal:1").text_en == st["text_en"]
    assert w.store.get("verdict", vd["verdict_id"]).chosen_candidate_id
    assert w.store.get("candidate", "mo-m001-067-goal.rhythm1").status == "chosen"
    assert w.store.get("candidate", "mo-m001-067-goal.pressure1").status == "rejected"
    assert ("story", "st-m001-067-goal:1") in w.store.children("claim", st["claim_ids"][0])
    # text really renders through the checker
    from secondlook.checker.text import check_text
    r = check_text(st["text_en"], [w.store.get("claim", c) for c in st["claim_ids"]], w.roster,
                   "en", "analyst", store=w.store, periods=PERIODS)
    assert r.ok and r.text == "Ironvale moved the ball through 11 passes, then scored."


async def test_scout_timeout_is_recorded_as_a_note_not_as_no_pressure(env, tmp_path):
    w, tools, m, tok = env
    responders = {**fake_model.responders(), "pressure": lambda r: LLMResponse(text="garbage")}
    out = await run_moment(svc(w, tools, responders), m, tok, tmp_path)
    assert out.status == "story"
    assert "pressure: returned invalid output" in out.verdict["notes"]
    assert out.story["thesis_tag"] == "sustained_possession"


async def test_evidence_request_loop_runs_at_most_once(env, tmp_path):
    w, tools, m, tok = env
    n = {"editor": 0}

    def editor(req):
        n["editor"] += 1
        p = json.loads(req.messages[0]["content"])
        if not p["evidence_round_used"] or n["editor"] == 1:
            return LLMResponse(text=json.dumps({"evidence_request": {
                "to_scout": "pressure", "question": "where was the ball won?"}}))
        return fake_model.editor(req)
    s = svc(w, tools, {**fake_model.responders(), "editor": editor})
    out = await run_moment(s, m, tok, tmp_path)
    assert out.evidence_round is True and out.editor_calls == 2 and out.status == "story"
    assert n["editor"] == 2 and len(agent_calls(w, "pressure")) == 2
    # the re-run scout used a new candidate id (suffix r2)
    assert w.store.get("candidate", "mo-m001-067-goal.pressurer21") is not None


async def test_second_evidence_request_does_not_loop(env, tmp_path):
    w, tools, m, tok = env

    def always_ask(req):
        return LLMResponse(text=json.dumps({"evidence_request": {
            "to_scout": "rhythm", "question": "more?"}}))
    out = await run_moment(svc(w, tools, {**fake_model.responders(), "editor": always_ask}),
                           m, tok, tmp_path)
    assert out.status == "fallback" and out.editor_calls == 2 and out.evidence_round is True
    assert len(agent_calls(w, "rhythm")) == 4  # (tool round + answer) x (first run, evidence round)
    assert w.store.get("moment", m.moment_id).status == "fallback"


async def test_editor_timeout_makes_the_moment_fall_back(env, tmp_path):
    import asyncio
    w, tools, m, tok = env

    async def slow(req):
        await asyncio.sleep(3)
    from secondlook import config
    old = config.TIMEOUT_EDITOR
    import secondlook.agents.editor as ed
    ed.config = type("C", (), {"TIMEOUT_EDITOR": 0.05})
    try:
        out = await run_moment(svc(w, tools, {**fake_model.responders(), "editor": slow}),
                               m, tok, tmp_path)
    finally:
        ed.config = config
        config.TIMEOUT_EDITOR = old
    assert out.status == "fallback" and "timeout" in out.reason
    assert "editor: timeout" in out.notes
    assert w.store.get("moment", m.moment_id).status == "fallback"


async def test_story_with_digits_or_foreign_claims_is_repaired_once_then_rejected(env, tmp_path):
    w, tools, m, tok = env
    calls = {"n": 0}

    def bad_then_good(req):
        calls["n"] += 1
        if calls["n"] == 1:
            r = json.loads(fake_model.editor(req).text)
            r["text_en"] = "Ironvale scored after 11 passes."
            return LLMResponse(text=json.dumps(r))
        p = json.loads(req.messages[0]["content"])
        assert any("digit" in e for e in p["previous_errors"])
        return fake_model.editor(req)
    out = await run_moment(svc(w, tools, {**fake_model.responders(), "editor": bad_then_good}),
                           m, tok, tmp_path)
    assert out.status == "story" and out.editor_calls == 2

    def always_bad(req):
        r = json.loads(fake_model.editor(req).text)
        r["claim_ids"] = ["not-a-claim"]
        return LLMResponse(text=json.dumps(r))
    out2 = await run_moment(svc(w, tools, {**fake_model.responders(), "editor": always_bad}),
                            m, tok, tmp_path / "b")
    assert out2.status == "fallback" and "subset" in out2.reason


async def test_no_candidates_means_fallback_without_calling_the_editor(env, tmp_path):
    w, tools, m, tok = env
    empty = lambda req: LLMResponse(text=json.dumps({"candidates": []}))  # noqa: E731
    s = svc(w, tools, {"pressure": empty, "rhythm": empty, "player": empty,
                       "editor": fake_model.editor})
    out = await run_moment(s, m, tok, tmp_path)
    assert out.status == "fallback" and out.reason == "no verified candidate"
    assert agent_calls(w, "editor") == []


async def test_kill_and_resume_makes_no_repeat_scout_calls(env, tmp_path):
    w, tools, m, tok = env
    crashing = svc(w, tools, fake_model.responders(), crash_in_editor=True)
    try:
        out = await run_moment(crashing, m, tok, tmp_path)
        assert out.status == "fallback"  # the framework may swallow the crash into a failed run
    except RuntimeError:
        pass
    before = {a: len(agent_calls(w, a)) for a in ("pressure", "rhythm", "editor")}
    assert before["pressure"] == 1 and before["rhythm"] == 2 and before["editor"] == 0
    healthy = svc(w, tools, fake_model.responders())
    out = await resume_moment(healthy, m, tmp_path)
    after = {a: len(agent_calls(w, a)) for a in ("pressure", "rhythm", "editor")}
    assert out.status == "story" and out.story["story_id"] == "st-m001-067-goal"
    assert after["pressure"] == 1 and after["rhythm"] == 2  # scouts NOT called again
    assert after["editor"] == 1
