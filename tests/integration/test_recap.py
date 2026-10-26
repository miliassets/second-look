"""S19: recaps cite only live revisions; story detail endpoint."""
import httpx

from secondlook.api.app import create_app
from secondlook.contracts.agents_io import Moment
from secondlook.publish.bus import Bus
from secondlook.runner import RunConfig, Runner


def ft_moment(r, kind="full_time"):
    m = Moment(moment_id=f"mo-m001-070-{kind}", kind=kind, window_from_ms=0, window_to_ms=r.now_ms,
               now_ms=r.now_ms, as_of_rev=r.store.current_rev())
    r.store.put("moment", m)
    return m


async def finished(tmp_path, **kw):
    bus = Bus()
    r = Runner(RunConfig(speed="max", llm="fake", sync_agents=True, state_dir=str(tmp_path), **kw),
               bus)
    await r.run()
    return r, bus


async def test_recap_lists_live_explanations_per_profile(tmp_path):
    r, bus = await finished(tmp_path)
    ids = r.build_recap(ft_moment(r))
    assert sorted(ids) == ["ov-m001-070-full_time-recap-r1-analyst-en",
                           "ov-m001-070-full_time-recap-r1-fan-es"]
    en = r.store.get("overlay", ids[0] if "analyst" in ids[0] else ids[1])
    es = r.store.get("overlay", ids[0] if "fan" in ids[0] else ids[1])
    assert en.kind == "recap" and en.slot == "side_panel" and en.headline == "Full time"
    assert es.headline == "Final" and en.lang == "en" and es.lang == "es"
    assert "Ironvale moved the ball through 11 passes, then scored." in en.text
    assert "Vask remató tras 11 pases seguidos." in es.text
    assert len(en.text.splitlines()) == 3 and en.claims
    # deps: cited stories feed the recap
    parents = r.store.parents("overlay", en.overlay_id)
    assert ("story", "st-m001-067-goal:1") in parents


async def test_recap_never_cites_a_superseded_revision(tmp_path):
    r, bus = await finished(tmp_path)
    r.build_recap(ft_moment(r))
    first = r.store.get("overlay", "ov-m001-070-full_time-recap-r1-analyst-en")
    assert "11 passes" in first.text
    await r.inject_correction("void_goal")  # rules the 67' goal out after the recap aired
    while r.tasks:
        import asyncio
        await asyncio.gather(*list(r.tasks))
    assert r.store.get_status("overlay", first.overlay_id) == "superseded"
    recaps = sorted((o for o in r.store.list_kind("overlay", kind="recap")
                     if o.profile_id == "analyst-en"), key=lambda o: o.revision)
    assert [o.revision for o in recaps] == [1, 2]
    live = recaps[-1]
    assert r.store.get_status("overlay", live.overlay_id) == "live"
    assert "11 passes" not in live.text and "moved the ball through" not in live.text
    assert "ruled out" in live.text  # revision 2 of the goal story is cited instead
    assert live.supersedes is not None
    cited = live.source.trace_id
    assert "st-m001-067-goal:2" in cited and "st-m001-067-goal:1" not in cited


async def test_story_detail_endpoint(tmp_path):
    app = create_app(RunConfig(speed="max", llm="fake", sync_agents=True,
                               state_dir=str(tmp_path)))
    async with app.router.lifespan_context(app):
        await app.state.holder["runner"].done.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            d = (await c.get("/api/story/st-m001-067-goal")).json()
            assert d["moment"]["kind"] == "goal"
            tags = {x["thesis_tag"]: x["status"] for x in d["candidates"]}
            assert tags == {"pressing_recovery": "rejected", "sustained_possession": "chosen",
                            "individual_action": "rejected"}
            assert all(k["status"] == "verified" for x in d["candidates"] for k in x["claims"])
            assert d["verdicts"][0]["rejected"] and d["calls"]
            assert (await c.get("/api/story/st-nope")).status_code == 404
