"""S18: the full 90-minute match through the whole pipeline."""
import pytest

from secondlook.publish.bus import Bus
from secondlook.runner import RunConfig, Runner

pytestmark = pytest.mark.slow


async def test_full_demo_match_with_agents_and_the_81_minute_correction(tmp_path):
    bus = Bus()
    r = Runner(RunConfig(speed="max", llm="fake", scope="full", scenario="demo",
                         sync_agents=True, state_dir=str(tmp_path)), bus)
    await r.run()
    kinds = [m.kind for m in r.detector.moments]
    assert {"goal", "tactical_shift", "card", "fast_shot", "disallowed_goal"} <= set(kinds)
    scores = [(m.payload["home"], m.payload["away"]) for m in bus.history if m.type == "score"]
    # 0-0, Tazlan 14', Vask 67', Fenn 81' (2-1 on air for a while), then ruled out: 1-1
    assert scores == [(0, 0), (1, 0), (1, 1), (1, 2), (1, 1)]
    notice = [m.payload for m in bus.history if m.type == "overlay"
              and m.payload["kind"] == "correction_notice"]
    assert {o["profile_id"] for o in notice} == {"analyst-en", "fan-es"}
    assert all(m.status in ("published", "fallback", "open") for m in r.store.list_kind("moment"))
    assert {c["outcome"] for c in r.store.calls()} <= {"ok"}
    assert len(r.detector.moments) >= 8
