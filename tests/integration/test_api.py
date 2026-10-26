import json

import httpx
import pytest

from secondlook.api.app import create_app
from secondlook.generator.corrections import CorrectionSpec
from secondlook.runner import RunConfig


def parse_sse(text):
    out, ev, data = [], None, []
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and ev:
            out.append((ev, json.loads("\n".join(data)) if data else None))
            ev, data = None, []
    if ev:
        out.append((ev, json.loads("\n".join(data))))
    return out


async def run_to_end(cfg, **kw):
    app = create_app(cfg, **kw)
    async with app.router.lifespan_context(app):
        runner = app.state.holder["runner"]
        await runner.done.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            res = {p: parse_sse((await c.get(f"/stream?profile={p}")).text)
                   for p in ("analyst-en", "fan-es")}
            state = (await c.get("/api/state")).json()
            health = (await c.get("/healthz")).json()
    return res, state, health, runner


async def test_sse_delivers_overlays_for_both_profiles():
    res, state, health, runner = await run_to_end(RunConfig(speed="max"))
    assert health == {"ok": True} and state["ended"] and state["started"]
    for prof, msgs in res.items():
        types = [t for t, _ in msgs]
        assert types[0] == "meta" and types[-1] == "end"
        overlays = [p for t, p in msgs if t == "overlay"]
        assert overlays and all(o["profile_id"] == prof for o in overlays)
        assert any(o["story_id"] == "st-m001-067-goal" and o["kind"] == "factual"
                   for o in overlays)
        assert "frame" in types and "event" in types
    en = next(p for t, p in res["analyst-en"] if t == "overlay" and "goal" in p["story_id"])
    es = next(p for t, p in res["fan-es"] if t == "overlay" and "goal" in p["story_id"])
    assert en["lang"] == "en" and es["lang"] == "es" and en["text"] != es["text"]
    assert es["text"] == "¡Gol de Ironvale! Marcó Vask. 1–1."
    scores = [p for t, p in res["fan-es"] if t == "score"]
    assert (scores[0]["home"], scores[0]["away"]) == (1, 0)
    assert (scores[-1]["home"], scores[-1]["away"]) == (1, 1)
    meta = res["fan-es"][0][1]
    assert meta["label"] == "Synthetic match" and {p["profile_id"] for p in meta["profiles"]} == {
        "analyst-en", "fan-es"}
    assert runner.store.current_rev() == len(runner.match.events)


async def test_late_overlays_in_order_and_stored_with_deps():
    _, _, _, runner = await run_to_end(RunConfig(speed="max"))
    ovs = runner.store.list_kind("overlay")
    assert len(ovs) == 6
    shows = [o.show_at_match_ms for o in ovs]
    assert shows == sorted(shows)
    goal_ov = next(o for o in ovs if o.profile_id == "analyst-en" and o.story_id.endswith("goal"))
    parents = runner.store.parents("overlay", goal_ov.overlay_id)
    assert parents and all(k == "claim" for k, _ in parents)


async def test_void_correction_updates_score_bug():
    cfg = RunConfig(speed="max", corrections=[CorrectionSpec("void_goal")])
    res, _, _, _ = await run_to_end(cfg)
    scores = [(p["home"], p["away"]) for t, p in res["analyst-en"] if t == "score"]
    assert scores == [(1, 0), (1, 1), (1, 0)]
    texts = [p["text"] for t, p in res["analyst-en"] if t == "overlay"]
    assert any("ruled out" in x for x in texts)


async def test_wait_mode_does_not_start_until_asked():
    app = create_app(RunConfig(speed="max"), autostart=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            assert (await c.get("/api/state")).json()["started"] is False
            assert (await c.post("/control/start")).json() == {"started": True}
            assert (await c.post("/control/start")).status_code == 409
            await app.state.holder["runner"].done.wait()
            assert (await c.get("/api/state")).json()["ended"] is True


async def test_static_front_end_is_served():
    app = create_app(RunConfig(speed="max"), autostart=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for path in ("/", "/viewer.html", "/js/viewer.js", "/css/app.css", "/i18n/es.json"):
            r = await c.get(path)
            assert r.status_code == 200, path
        assert "Synthetic" in (await c.get("/")).text


@pytest.mark.parametrize("faults", ["demo", "heavy"])
async def test_run_with_feed_faults_still_publishes_goal_card(faults):
    res, _, _, runner = await run_to_end(RunConfig(speed="max", faults=faults))
    overlays = [p for t, p in res["analyst-en"] if t == "overlay"]
    assert any(o["story_id"] == "st-m001-067-goal" for o in overlays)
