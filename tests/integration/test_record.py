"""S21: recorder, replay loader and export."""
import json
from pathlib import Path

import pytest

from secondlook.publish.bus import Bus
from secondlook.record.recorder import record_run
from secondlook.record.replay import export_replay, load_run, replay_messages
from secondlook.runner import RunConfig, Runner


@pytest.fixture(scope="module")
async def recorded(tmp_path_factory):
    root = tmp_path_factory.mktemp("runs")
    cfg = RunConfig(speed="max", llm="fake", faults="demo", sync_agents=True,
                    state_dir=str(root / "state"))
    d = await record_run(cfg, root / "runs")
    # the same run, live, for comparison (same seed, same code: deterministic)
    bus = Bus()
    r = Runner(RunConfig(speed="max", llm="fake", faults="demo", sync_agents=True,
                         state_dir=str(root / "state2")), bus)
    await r.run()
    live = [(m.payload["profile_id"], m.payload["overlay_id"]) for m in bus.history
            if m.type == "overlay"]
    return d, live, root


async def test_files_manifest_and_metrics(recorded):
    d, _, _ = recorded
    for f in ("manifest.json", "metadata.json", "events.jsonl", "frames.jsonl.gz",
              "overlays.jsonl", "workflow.jsonl", "calls.jsonl", "askwhy.jsonl", "metrics.json",
              "stream.jsonl.gz"):
        assert (d / f).exists(), f
    man = json.loads((d / "manifest.json").read_text())
    assert man["label"] == "recorded agent run" and man["provider"] == "fake"
    assert man["live_inference"] is False and man["seed"] == 20261024
    assert "fake" in man["note"].lower() or "rule-based" in man["note"].lower()
    met = json.loads((d / "metrics.json").read_text())
    assert met["moments"] == 3 and met["explanations"] >= 3 and met["fallbacks"] == 0
    assert met["latency_ms_p95"] is not None and met["agent_calls"] > 0
    qa = [json.loads(x) for x in (d / "askwhy.jsonl").read_text().splitlines()]
    assert len(qa) == 6 and {q["profile_id"] for q in qa} == {"analyst-en", "fan-es"}
    assert any(q["supported"] for q in qa)
    secrets = ("GITHUB_TOKEN", "FOUNDRY_KEY", "Bearer ")
    for p in d.iterdir():
        if p.suffix in (".json", ".jsonl"):
            assert not any(s in p.read_text() for s in secrets), p.name


async def test_replay_renders_the_same_overlay_sequence_as_the_live_run(recorded):
    d, live, _ = recorded
    run = load_run(d)
    got = [(prof or p["profile_id"], p["overlay_id"]) for t, p, prof in replay_messages(run)
           if t == "overlay"]
    assert got and sorted(got) == sorted(live)
    for prof in ("analyst-en", "fan-es"):
        assert [o for p_, o in got if p_ == prof] == [o for p_, o in live if p_ == prof]
    kinds = {p["kind"] for t, p, _ in replay_messages(run) if t == "overlay"}
    assert "answer" not in kinds and {"factual", "explanation"} <= kinds
    types = [t for t, _, _ in replay_messages(run)]
    assert types[0] == "meta" and types[-1] == "end" and types.count("frame") == len(run["frames"])
    # frames are in feed-clock order, messages never run ahead of their time
    ats = [f["at_ms"] for f in run["frames"]]
    assert ats == sorted(ats)


async def test_export_writes_static_files_and_index(recorded, tmp_path):
    d, _, _ = recorded
    out = export_replay(d, tmp_path / "web-runs")
    for f in ("manifest.json", "frames.jsonl", "stream.jsonl", "askwhy.json", "workflow.jsonl",
              "calls.jsonl"):
        assert (out / f).exists()
    idx = json.loads((tmp_path / "web-runs" / "index.json").read_text())
    assert idx["default"] == out.name and idx["runs"][0]["label"] == "recorded agent run"
    first = json.loads((out / "stream.jsonl").read_text().splitlines()[0])
    assert first["type"] == "meta" and Path(out / "frames.jsonl").stat().st_size > 10_000
