import json

from secondlook.agents import fake_model
from secondlook.agents.llm import LLMResponse, ManagedLLM, ScriptedClient
from secondlook.agents.workflow import Services, run_moment
from secondlook.checker.verify import Checker
from secondlook.contracts.agents_io import Moment
from secondlook.contracts.events import Delivery
from secondlook.generator.corrections import CorrectionSpec
from secondlook.generator.engine import PERIODS
from secondlook.matchstate.tools import MatchTools
from secondlook.moments.factual import factual_overlay_id
from secondlook.publish.bus import Bus
from secondlook.publish.publisher import Publisher
from tests.conftest import World, build, ms


def make(w, responders, store=None, checker=None, **kw):
    store = store or w.store
    checker = checker or w.checker
    tools = MatchTools(store, checker, PERIODS)
    m = Moment(moment_id="mo-m001-067-goal", kind="goal", trigger_event_id=w.goal.event_id,
               window_from_ms=ms(w.goal) - 120_000, window_to_ms=ms(w.goal), now_ms=ms(w.goal),
               as_of_rev=w.goal.seq, team_id="IRV")
    store.put("moment", m)
    tok = tools.issue_token(m)
    bus = Bus()
    llm = ManagedLLM(ScriptedClient(responders), store, None, None, "fake")
    svc = Services(llm, tools, checker, store, w.roster, PERIODS,
                   publisher=Publisher(store, bus, checker), clock_ms=lambda: ms(w.goal) + 9000,
                   **kw)
    return svc, m, tok, bus


async def test_both_profiles_get_checked_explanations(fresh_world, tmp_path):
    w = fresh_world
    svc, m, tok, bus = make(w, fake_model.responders())
    out = await run_moment(svc, m, tok, tmp_path)
    assert out.status == "story" and out.producers == {"analyst-en": "ok", "fan-es": "ok"}
    ovs = {o["profile_id"]: o for o in out.overlays}
    en, es = ovs["analyst-en"], ovs["fan-es"]
    assert en["text"] == "Ironvale moved the ball through 11 passes, then scored."
    assert es["text"] == "Vask remató tras 11 pases seguidos."
    assert en["lang"] == "en" and es["lang"] == "es" and en["viewer_mode"] != es["viewer_mode"]
    assert es["spans"][0]["ref"] == "p:IRV-10" and es["text"][:4] == "Vask"
    assert en["kind"] == es["kind"] == "explanation" and en["revision"] == 1
    assert en["supersedes"] == factual_overlay_id(m.moment_id, "analyst-en")
    assert es["supersedes"] == factual_overlay_id(m.moment_id, "fan-es")
    en_layers = {x["type"] for x in en["layers"]}
    es_layers = {x["type"] for x in es["layers"]}
    assert {"chain", "zone", "marker"} <= en_layers and len(es["layers"]) <= 2
    assert en_layers != es_layers
    chain = next(x for x in en["layers"] if x["type"] == "chain")
    assert len(chain["event_ids"]) == 11 and len(chain["points"]) == 12
    assert any(x["type"] == "player_halo" and x["player_id"] == "IRV-10" for x in es["layers"])
    assert en["priority"] > 1 and en["source"]["latency_ms"] is not None
    assert en["show_at_match_ms"] == ms(w.goal) + 9000
    # stored, announced on the bus, moment marked published
    assert w.store.get("overlay", en["overlay_id"]) and w.store.get("overlay", es["overlay_id"])
    assert [m_.profile_id for m_ in bus.history if m_.type == "overlay"] == [
        "analyst-en", "fan-es"]
    assert w.store.get("moment", m.moment_id).status == "published"
    assert w.store.get("viewer_output", "vo-m001-067-goal-r1-fan-es").status == "approved"
    assert ("overlay", es["overlay_id"]) in w.store.children(
        "viewer_output", "vo-m001-067-goal-r1-fan-es")


async def test_producer_claims_are_a_subset_of_the_story_claims(fresh_world, tmp_path):
    w = fresh_world
    svc, m, tok, _ = make(w, fake_model.responders())
    out = await run_moment(svc, m, tok, tmp_path)
    story_ids = set(out.story["claim_ids"])
    for o in out.overlays:
        assert {c["claim_id"] for c in o["claims"]} <= story_ids

    def foreign(req):
        r = json.loads(fake_model.producer(req).text)
        r["claim_ids"] = ["mo-m001-067-goal.pressure1.c1"]  # a claim the story did not choose
        return LLMResponse(text=json.dumps(r))
    svc2, m2, tok2, _ = make(World(), {**fake_model.responders(), "producer-fan-es": foreign})
    out2 = await run_moment(svc2, m2, tok2, tmp_path / "b")
    assert out2.producers["fan-es"] == "rejected" and out2.producers["analyst-en"] == "ok"
    assert [o["profile_id"] for o in out2.overlays] == ["analyst-en"]


async def test_fan_timeout_leaves_analyst_unaffected(fresh_world, tmp_path):
    import asyncio
    w = fresh_world

    async def hang(req):
        await asyncio.sleep(30)
    import secondlook.agents.producers as pr
    from secondlook import config
    pr.config = type("C", (), {"TIMEOUT_PRODUCER": 0.05})
    try:
        svc, m, tok, bus = make(w, {**fake_model.responders(), "producer-fan-es": hang})
        out = await run_moment(svc, m, tok, tmp_path)
    finally:
        pr.config = config
    assert out.producers == {"analyst-en": "timeout", "fan-es": "ok"} or \
        out.producers == {"fan-es": "timeout", "analyst-en": "ok"}
    assert out.producers["fan-es"] == "timeout" and out.producers["analyst-en"] == "ok"
    assert [o["profile_id"] for o in out.overlays] == ["analyst-en"]
    assert out.overlays[0]["text"].startswith("Ironvale moved")
    assert w.store.get("moment", m.moment_id).status == "published"


async def test_spanish_output_must_pass_the_checker(fresh_world, tmp_path):
    w = fresh_world

    def bad_es(req):
        r = json.loads(fake_model.producer(req).text)
        r["template"] = "Vask remató porque hubo {c} pases".replace(
            "{c}", "{" + r["claim_ids"][0] + "}")
        return LLMResponse(text=json.dumps(r))
    svc, m, tok, _ = make(w, {**fake_model.responders(), "producer-fan-es": bad_es})
    out = await run_moment(svc, m, tok, tmp_path)
    assert out.producers["fan-es"] == "rejected" and out.producers["analyst-en"] == "ok"

    def digits(req):
        r = json.loads(fake_model.producer(req).text)
        r["template"] = "Remató tras 11 pases."
        return LLMResponse(text=json.dumps(r))
    svc2, m2, tok2, _ = make(World(), {**fake_model.responders(), "producer-fan-es": digits})
    out2 = await run_moment(svc2, m2, tok2, tmp_path / "b")
    assert out2.producers["fan-es"] == "rejected"


async def test_editor_fallback_skips_producers_and_publishes_nothing(fresh_world, tmp_path):
    w = fresh_world
    empty = lambda req: LLMResponse(text=json.dumps({"candidates": []}))  # noqa: E731
    svc, m, tok, bus = make(w, {"pressure": empty, "rhythm": empty, "player": empty})
    out = await run_moment(svc, m, tok, tmp_path)
    assert out.status == "fallback" and out.overlays == []
    assert out.producers == {"analyst-en": "skipped", "fan-es": "skipped"}
    assert not [x for x in bus.history if x.type == "overlay"]


async def test_publish_time_recheck_blocks_a_stale_story(tmp_path):
    """A correction lands while the producers work: the stale story must not air."""
    w = World(corrections=[CorrectionSpec("void_goal")])
    store, ing, _ = build(w.match, until_seq=w.goal.seq)
    checker = Checker(store, PERIODS, w.match.frames)
    rest = [e for e in w.match.events if e.seq > w.goal.seq]
    state = {"done": False}

    def producer_with_race(req):
        if not state["done"]:
            state["done"] = True
            for i, e in enumerate(rest):  # the correction arrives mid-workflow
                ing.ingest(Delivery(event=e, arrival_order=10_000 + i, arrival_match_ms=ms(e)))
        return fake_model.producer(req)
    stale_calls = []
    svc, m, tok, bus = make(w, {**fake_model.responders(), "producer-analyst-en": producer_with_race,
                                "producer-fan-es": producer_with_race},
                            store=store, checker=checker)
    svc.on_stale = lambda mom, ids: stale_calls.append((mom.moment_id, ids))
    out = await run_moment(svc, m, tok, tmp_path)
    assert out.status == "story" and out.overlays == []  # story was approved, never aired
    assert out.stale_claims and stale_calls and stale_calls[0][0] == m.moment_id
    assert not [x for x in bus.history if x.type == "overlay"]
    assert store.get("moment", m.moment_id).status != "published"
