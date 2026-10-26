import json

import httpx

from secondlook.agents import fake_model
from secondlook.agents.askwhy import ask_why
from secondlook.agents.llm import LLMResponse, ManagedLLM, ScriptedClient
from secondlook.agents.workflow import Services
from secondlook.api.app import create_app
from secondlook.contracts.prefs import DEFAULT_PROFILES
from secondlook.generator.engine import PERIODS
from secondlook.matchstate.tools import MatchTools
from secondlook.publish.bus import Bus
from secondlook.publish.publisher import Publisher
from secondlook.runner import RunConfig
from tests.conftest import World, ms

EN, ES = DEFAULT_PROFILES


def svc_for(w, responders=None):
    tools = MatchTools(w.store, w.checker, PERIODS)
    llm = ManagedLLM(ScriptedClient(responders or fake_model.responders()), w.store, None, None,
                     "fake")
    bus = Bus()
    return Services(llm, tools, w.checker, w.store, w.roster, PERIODS,
                    publisher=Publisher(w.store, bus, w.checker)), bus


async def test_supported_question_gets_a_sourced_answer(fresh_world):
    w = fresh_world
    svc, bus = svc_for(w)
    now = ms(w.goal) + 1000
    r = await ask_why(svc, "Why did Ironvale score?", EN, now)
    assert r.supported and r.text.startswith("Score 1–1. Ironvale held ")
    assert r.claims and all(c.status == "verified" for c in r.claims)
    assert r.overlay.kind == "answer" and r.overlay.slot == "side_panel"
    assert r.overlay.profile_id == "analyst-en" and r.overlay.claims
    assert [m.profile_id for m in bus.history if m.type == "overlay"] == ["analyst-en"]


async def test_spanish_fan_answer(fresh_world):
    w = fresh_world
    svc, _ = svc_for(w)
    r = await ask_why(svc, "¿Por qué marcó Ironvale?", ES, ms(w.goal) + 1000)
    assert r.supported and r.text.startswith("Marcador 1–1. Ironvale tuvo ")
    assert "," in r.text.split("tuvo")[1] or "%" in r.text


async def test_unsupported_question_is_refused_with_top_facts(fresh_world):
    w = fresh_world
    svc, bus = svc_for(w)
    r = await ask_why(svc, "Who will win the league?", EN, ms(w.goal) + 1000)
    assert not r.supported and r.reason == "unsupported"
    assert r.text.startswith("I can't support an answer from the match data. Score 1–1.")
    assert "Kestrel Bay:" in r.text and "Ironvale:" in r.text and "% possession" in r.text
    assert all(c.status == "verified" for c in r.claims) and len(r.claims) == 3
    es = await ask_why(svc, "¿Quién ganará la liga?", ES, ms(w.goal) + 1000)
    assert es.text.startswith("No puedo respaldar una respuesta con los datos del partido. Marcador")
    assert len([m for m in bus.history if m.type == "overlay"]) == 2


async def test_never_cites_future_events(fresh_world):
    w = fresh_world
    svc, _ = svc_for(w)
    now = ms(w.goal) - 60_000  # a minute before the goal
    r = await ask_why(svc, "What is the score?", EN, now)
    assert r.supported and r.text.startswith("Score 1–0.")
    by_id = {e.event_id: e for e in w.match.events}
    assert r.overlay.evidence_ids and all(ms(by_id[i]) <= now for i in r.overlay.evidence_ids)
    # a model that tries to cite the future is refused
    def sneaky(req):
        return LLMResponse(text=json.dumps({"supported": True, "claims": [{
            "claim_id": "a1", "type": "score", "params": {"at_ms": ms(w.goal), "home": 1,
                                                        "away": 1}}],
            "template": "Score {a1}."}))
    svc2, _ = svc_for(w, {"askwhy": sneaky})
    r2 = await ask_why(svc2, "score?", EN, now)
    assert not r2.supported and r2.text.startswith("I can't support")


async def test_made_up_claims_are_refused(fresh_world):
    w = fresh_world

    def liar(req):
        return LLMResponse(text=json.dumps({"supported": True, "claims": [{
            "claim_id": "a1", "type": "score", "params": {"at_ms": ms(w.goal), "home": 5,
                                                        "away": 0}}],
            "template": "Score {a1}."}))
    svc, _ = svc_for(w, {"askwhy": liar})
    r = await ask_why(svc, "score?", EN, ms(w.goal) + 1000)
    assert not r.supported and "5" not in r.text.split("Score")[0]
    assert "Score 1–1" in r.text  # the facts line, computed by the checker


async def test_bad_output_and_digits_fall_back_to_refusal(fresh_world):
    w = fresh_world
    svc, _ = svc_for(w, {"askwhy": lambda req: LLMResponse(text="nonsense")})
    r = await ask_why(svc, "score?", EN, ms(w.goal) + 1000)
    assert not r.supported and r.outcome == "invalid"
    svc2, _ = svc_for(w, {"askwhy": lambda req: LLMResponse(text=json.dumps({
        "supported": True, "claims": [{"claim_id": "a1", "type": "score", "params": {
            "at_ms": ms(w.goal), "home": 1, "away": 1}}], "template": "It is 1-1 {a1}."}))})
    r2 = await ask_why(svc2, "score?", EN, ms(w.goal) + 1000)
    assert not r2.supported


async def test_ask_endpoint(tmp_path):
    app = create_app(RunConfig(speed="max", llm="fake", sync_agents=True,
                               state_dir=str(tmp_path)))
    async with app.router.lifespan_context(app):
        await app.state.holder["runner"].done.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            ok = (await c.post("/ask", json={"question": "Why did Ironvale score?",
                                             "profile_id": "analyst-en"})).json()
            assert ok["supported"] and ok["text"].startswith("Score 1–1.")
            no = (await c.post("/ask", json={"question": "weather?", "profile_id": "fan-es"})
                  ).json()
            assert not no["supported"] and no["text"].startswith("No puedo")
            assert (await c.post("/ask", json={"question": ""})).status_code == 422
            assert (await c.post("/ask", json={"question": "x", "profile_id": "zz"})
                    ).status_code == 404


async def test_ask_requires_agents_and_a_started_feed():
    app = create_app(RunConfig(speed="max", llm="none"))
    async with app.router.lifespan_context(app):
        await app.state.holder["runner"].done.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            assert (await c.post("/ask", json={"question": "score?"})).status_code == 503
    app2 = create_app(RunConfig(speed="max"), autostart=False)
    async with app2.router.lifespan_context(app2):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2),
                                     base_url="http://t") as c:
            assert (await c.post("/ask", json={"question": "score?"})).status_code == 409


def test_world_fixture_used():
    assert World
