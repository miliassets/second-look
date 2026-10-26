"""MANUAL live smoke run (design S12): `GITHUB_TOKEN=... pytest -m live`. At most ~10 calls on the
60'-70' slice. NOT run by the builder: no token was available. Skipped without a token."""
import os

import pytest

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="no GITHUB_TOKEN")
async def test_live_scouts_on_the_goal_moment():
    from secondlook.agents.llm import make_llm
    from secondlook.agents.scouts import run_scout
    from secondlook.publish.bus import Bus
    from secondlook.runner import RunConfig, Runner
    r = Runner(RunConfig(speed="max"), Bus())
    await r.run()
    m = next(x for x in r.detector.moments if x.kind == "goal")
    llm = make_llm("github", store=r.store)
    for name in ("pressure", "rhythm"):
        res = await run_scout(name, llm, r.tools, r.checker, r.store, m, r.tools.by_moment[m.moment_id])
        print(name, res.status, [c.thesis for c in res.candidates], res.note)
        assert res.status in ("ok", "invalid", "timeout")
