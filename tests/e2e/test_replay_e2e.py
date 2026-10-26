"""S21: static replay (no backend) shows the recorded overlay sequence; banner present."""
import functools
import http.server
import json
import shutil
import threading

import pytest

from secondlook.publish.bus import Bus
from secondlook.record.recorder import record_run
from secondlook.record.replay import export_replay
from secondlook.runner import RunConfig, Runner

pytestmark = pytest.mark.e2e


def run_async(coro_fn):
    """asyncio.run in a fresh thread: the Playwright sync API owns this thread's loop."""
    import asyncio
    out = {}

    def target():
        out["v"] = asyncio.run(coro_fn())
    th = threading.Thread(target=target)
    th.start()
    th.join()
    return out["v"]


@pytest.fixture
def static_site(tmp_path):
    cfg = RunConfig(speed="max", llm="fake", faults="demo", sync_agents=True,
                    state_dir=str(tmp_path / "st"))
    run_dir = run_async(lambda: record_run(cfg, tmp_path / "runs"))
    web = tmp_path / "web"
    shutil.copytree(__import__("pathlib").Path("web"), web, ignore=shutil.ignore_patterns("runs"))
    export_replay(run_dir, web / "runs")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bus = Bus()

    async def live():
        r = Runner(RunConfig(speed="max", llm="fake", faults="demo", sync_agents=True,
                             state_dir=str(tmp_path / "st2")), bus)
        await r.run()
    run_async(live)
    ids = {p: [m.payload["overlay_id"] for m in bus.history
               if m.type == "overlay" and m.payload["profile_id"] == p]
           for p in ("analyst-en", "fan-es")}
    yield f"http://127.0.0.1:{srv.server_address[1]}", ids, json.loads(
        (run_dir / "manifest.json").read_text())
    srv.shutdown()


def test_static_replay_matches_the_live_overlay_sequence(static_site, browser):
    url, live_ids, man = static_site
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url + "/")
    an, fan = page.frame_locator("#analyst"), page.frame_locator("#fan")
    page.locator("body[data-mode='replay']").wait_for(timeout=10_000)
    assert "Recorded agent run" in page.locator("#state").inner_text()
    assert "not live inference" in page.locator("#state").inner_text()
    an.locator("#banner:not([hidden])").wait_for(timeout=10_000)
    assert "Recorded agent run" in an.locator("#banner").inner_text()
    assert "Ejecución grabada" in fan.locator("#banner").inner_text()
    page.click('#replayBar [data-speed="60"]')  # not 1x: labelled
    an.locator("#speedLabel").filter(has_text="60×").wait_for(timeout=10_000)
    an.locator("body[data-ended='1']").wait_for(timeout=60_000)
    fan.locator("body[data-ended='1']").wait_for(timeout=60_000)
    for frame, prof in ((an, "analyst-en"), (fan, "fan-es")):
        got = frame.locator("#log li").evaluate_all(
            "els => els.map(e => e.dataset.overlayId).reverse()")  # log prepends: oldest last
        assert got == live_ids[prof], prof
    # recorded Ask why: recorded questions only
    assert "Recorded questions only" in an.locator(".ask-note").inner_text()
    an.locator("#askBtn").click()
    an.locator("#answer:not([hidden])").wait_for()
    assert an.locator("#answer").inner_text()
    # the analyst panel works from the recorded workflow
    an.locator('#log [data-overlay-id="ov-m001-067-goal-r1-analyst-en"]').click()
    an.locator("#details:not([hidden]) .cand-chosen").wait_for(timeout=10_000)
    assert errors == []
    page.close()
