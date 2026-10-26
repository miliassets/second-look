"""S15 gate (API level): goal -> factual card, then explanation with `supersedes`, in both views."""
from secondlook.runner import RunConfig
from tests.integration.test_api import run_to_end


async def test_goal_to_two_viewers_end_to_end_with_fake_llm(tmp_path):
    res, _, _, runner = await run_to_end(RunConfig(speed="max", llm="fake",
                                                   state_dir=str(tmp_path / ".state")))
    ov = {p: [x for t, x in msgs if t == "overlay"] for p, msgs in res.items()}
    goal = {p: [o for o in v if o["story_id"] == "st-m001-067-goal"] for p, v in ov.items()}
    for prof, items in goal.items():
        kinds = [o["kind"] for o in items]
        assert kinds == ["factual", "explanation"], (prof, kinds)  # factual first, then explanation
        factual, expl = items
        assert expl["supersedes"] == factual["overlay_id"] and expl["priority"] > factual["priority"]
    en, es = goal["analyst-en"][1], goal["fan-es"][1]
    assert en["text"] == "Ironvale moved the ball through 11 passes, then scored."
    assert es["text"] == "Vask remató tras 11 pases seguidos."
    assert en["lang"] == "en" and es["lang"] == "es"
    assert {x["type"] for x in en["layers"]} != {x["type"] for x in es["layers"]}
    assert len(es["layers"]) <= 2 and len(en["layers"]) >= 3
    assert en["evidence_ids"] and es["evidence_ids"]
    # every moment got a ruling: one verdict each, candidates recorded
    assert runner.store.list_kind("verdict")
    assert all(m.status in ("published", "fallback") for m in runner.store.list_kind("moment"))
    assert runner.store.calls()  # agent_calls logged
    assert {c["outcome"] for c in runner.store.calls()} == {"ok"}


async def test_agents_off_gives_factual_only():
    res, _, _, _ = await run_to_end(RunConfig(speed="max", llm="none"))
    kinds = {x["kind"] for t, x in res["fan-es"] if t == "overlay"}
    assert kinds == {"factual"}
