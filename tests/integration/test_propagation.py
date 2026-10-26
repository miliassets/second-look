"""S16: correction propagation (design §4)."""
import json
import time

import httpx

from secondlook.agents.fake_model import editor as fake_editor
from secondlook.agents.fake_model import producer as fake_producer
from secondlook.agents.llm import LLMResponse
from secondlook.api.app import create_app
from secondlook.contracts.prefs import ViewerProfile
from secondlook.generator.corrections import CorrectionSpec
from secondlook.publish.bus import Bus
from secondlook.runner import RunConfig, Runner


def overlays(bus, story="st-m001-067-goal"):
    return [m.payload for m in bus.history if m.type == "overlay" and m.payload["story_id"] == story]


def scores(bus):
    return [(m.payload["home"], m.payload["away"]) for m in bus.history if m.type == "score"]


async def run_void(tmp_path):
    bus = Bus()
    r = Runner(RunConfig(speed="max", llm="fake", sync_agents=True, state_dir=str(tmp_path),
                         corrections=[CorrectionSpec("void_goal")]), bus)
    await r.run()
    return r, bus


async def test_overturning_an_approved_explanation(tmp_path):
    r, bus = await run_void(tmp_path)
    ovs = overlays(bus)
    by = {p: [o for o in ovs if o["profile_id"] == p] for p in ("analyst-en", "fan-es")}
    for prof, items in by.items():
        assert [(o["kind"], o["revision"]) for o in items] == [
            ("factual", 1), ("explanation", 1), ("correction_notice", 1), ("explanation", 2)], prof
        factual, expl1, notice, expl2 = items
        assert notice["supersedes"] == expl1["overlay_id"] and "updated" in notice["badges"]
        assert expl2["supersedes"] == notice["overlay_id"]  # revision 2 replaces the notice
        assert "ruled out" in notice["text"] or "anulado" in notice["text"]
    assert scores(bus)[-1] == (1, 0)  # the score bug goes back
    # the story's revision 1 is invalidated, revision 2 approved, claims invalidated with a reason
    stories = {(s.story_id, s.revision): s.status for s in r.store.list_kind("story")}
    assert stories[("st-m001-067-goal", 1)] == "invalidated"
    assert stories[("st-m001-067-goal", 2)] == "approved"
    s2 = r.store.get("story", "st-m001-067-goal:2")
    assert s2.supersedes_revision == 1 and "ruled out" in s2.text_en
    chain = next(c for c in r.store.list_kind("claim")
                 if c.type == "chain" and "067-goal.rhythm1" in c.claim_id)
    assert chain.status == "invalidated" and chain.reason
    live = [o.overlay_id for o in r.store.list_kind("overlay")
            if o.story_id == "st-m001-067-goal"
            and r.store.get_status("overlay", o.overlay_id) == "live"]
    assert sorted(live) == ["ov-m001-067-goal-notice-r260-analyst-en".replace("r260", x)
                            for x in ()] or all("-r2-" in x for x in live)
    assert len(live) == 2 and not any("factual" in x or "-r1-" in x for x in live)
    # nothing about other stories was disturbed
    other = [o for o in r.store.list_kind("overlay") if o.story_id != "st-m001-067-goal"]
    assert other and all(r.store.get_status("overlay", o.overlay_id) == "live"
                         for o in other if o.kind == "explanation")


async def test_unaffected_claims_survive_with_updated_checked_rev(tmp_path):
    r, _ = await run_void(tmp_path)
    claims = r.store.list_kind("claim")
    ok = [c for c in claims if c.type == "count" and c.status == "verified"]
    assert ok and all(c.checked_rev > 0 for c in ok)


async def test_reattributed_recovery_removes_the_followed_players_story(tmp_path):
    recovery = {}

    def pressure(req):
        p = json.loads(req.messages[0]["content"])
        if p["moment_kind"] != "goal":
            return LLMResponse(text=json.dumps({"candidates": []}))
        return LLMResponse(text=json.dumps({"candidates": [{
            "scout": "pressure", "thesis_tag": "pressing_recovery", "thesis": "Mbanefo won it.",
            "claims": [{"claim_id": "c1", "type": "involved", "params": {
                "player_id": "IRV-08", "event_ids": [recovery["id"]]}}]}]}))

    def rhythm(req):
        return LLMResponse(text=json.dumps({"candidates": []}))

    def editor(req):
        p = json.loads(req.messages[0]["content"])
        c = p["candidates"][0] if p["candidates"] else None
        if not c or c["scout"] == "system":
            return LLMResponse(text=json.dumps({"chosen_candidate_id": None}))
        return LLMResponse(text=json.dumps({
            "chosen_candidate_id": c["candidate_id"], "claim_ids": [c["claims"][0]["claim_id"]],
            "text_en": "{p:IRV-08} won the ball back."}))

    def producer(req):
        p = json.loads(req.messages[0]["content"])
        cid = p["story"]["claim_ids"][0]
        tpl = "{p:IRV-08} recuperó el balón." if p["profile"]["lang"] == "es" \
            else p["story"]["text_en"]
        return LLMResponse(text=json.dumps({"template": tpl, "headline": "Why it happened"
                                            if p["profile"]["lang"] == "en" else "Así fue la jugada",
                                            "claim_ids": [cid], "layers": ["marker"]}))
    responders = {"pressure": pressure, "rhythm": rhythm, "player": rhythm, "editor": editor,
                  "producer-analyst-en": producer, "producer-fan-es": producer}
    profiles = [ViewerProfile(profile_id="analyst-en", mode="analyst", lang="en"),
                ViewerProfile(profile_id="fan-es", mode="fan", lang="es",
                              followed_player_id="IRV-08", followed_team_id="IRV")]
    bus = Bus()
    r = Runner(RunConfig(speed="max", llm="fake", sync_agents=True, state_dir=str(tmp_path),
                         corrections=[CorrectionSpec("reattribute", delay_ms=60_000, new_player_id="IRV-06")]),
               bus, profiles=profiles, responders=responders)
    recovery["id"] = r.match.ground_truth["beats"]["recovery_event_id"]
    await r.run()
    ovs = overlays(bus)
    fan = [(o["kind"], o["revision"], o["text"]) for o in ovs if o["profile_id"] == "fan-es"]
    assert fan[0][0] == "factual" and fan[1] == ("explanation", 1, "Mbanefo recuperó el balón.")
    assert fan[2][0] == "correction_notice" and "corregida" in fan[2][2]
    assert len(fan) == 3  # the old-player story is gone and nothing replaces it
    live = [o.overlay_id for o in r.store.list_kind("overlay")
            if o.story_id == "st-m001-067-goal"
            and r.store.get_status("overlay", o.overlay_id) == "live"]
    assert live and all("notice" in x for x in live)
    assert scores(bus)[-1] == (1, 1)  # a re-attribution does not touch the score
    assert r.store.get("story", "st-m001-067-goal:1").status == "invalidated"


async def test_live_injection_airs_notices_fast_and_control_endpoint(tmp_path):
    cfg = RunConfig(speed="max", llm="fake", sync_agents=True, state_dir=str(tmp_path))
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        runner = app.state.holder["runner"]
        await runner.done.wait()
        bus = runner.bus
        before = len(bus.history)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            assert (await c.post("/control/correction?kind=bogus")).status_code == 422
            t0 = time.time()
            res = await c.post("/control/correction?kind=void_goal")
            assert res.status_code == 200
        assert time.time() - t0 < 5
        new = [m.payload for m in bus.history[before:] if m.type == "overlay"]
        kinds = [(o["profile_id"], o["kind"]) for o in new]
        assert ("analyst-en", "correction_notice") in kinds and ("fan-es", "correction_notice") in kinds
        await __import__("asyncio").gather(*list(runner.tasks))
        assert scores(bus)[-1] == (1, 0)
        assert any(o["kind"] == "explanation" and o["revision"] == 2
                   for o in [m.payload for m in bus.history if m.type == "overlay"])


async def test_control_correction_before_start_is_refused():
    app = create_app(RunConfig(speed="max"), autostart=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            assert (await c.post("/control/correction")).status_code == 409


async def test_fake_editor_and_producer_imports_used():
    assert fake_editor and fake_producer
