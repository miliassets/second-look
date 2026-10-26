"""Load a recorded run and iterate it the way the live feed would have (python side, for tests)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def load_run(d: Path) -> dict:
    d = Path(d)

    def gz(name):
        with gzip.open(d / name, "rt", encoding="utf-8") as f:
            return [json.loads(line) for line in f]
    return {"manifest": json.loads((d / "manifest.json").read_text()),
            "frames": gz("frames.jsonl.gz"), "stream": gz("stream.jsonl.gz"),
            "askwhy": [json.loads(x) for x in (d / "askwhy.jsonl").read_text().splitlines()]}


def replay_messages(run: dict):
    """Yield ('frame'|type, payload, profile_id) in the order a viewer saw them: non-frame
    messages first at the same feed time (the runner publishes events before its frame)."""
    items = [(m["at_ms"], 0, m["i"], m["type"], m["payload"], m["profile_id"])
             for m in run["stream"]]
    items += [(f["at_ms"], 1, i, "frame", f, None) for i, f in enumerate(run["frames"])]
    for _, _, _, t, p, prof in sorted(items, key=lambda x: x[:3]):
        yield t, p, prof


def export_replay(run_dir: Path, web_runs: Path) -> Path:
    """Copy a run into web/runs/<id>/ as plain files a static site can fetch."""
    run = load_run(run_dir)
    run_id = run["manifest"]["run_id"]
    out = web_runs / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(run["manifest"], indent=2) + "\n")
    (out / "frames.jsonl").write_text("".join(json.dumps(f, separators=(",", ":")) + "\n"
                                              for f in run["frames"]))
    (out / "stream.jsonl").write_text("".join(json.dumps(m, ensure_ascii=False,
                                                          separators=(",", ":")) + "\n"
                                              for m in run["stream"]))
    for name in ("workflow.jsonl", "calls.jsonl"):
        (out / name).write_text((Path(run_dir) / name).read_text())
    (out / "askwhy.json").write_text(json.dumps(run["askwhy"], ensure_ascii=False) + "\n")
    runs = []
    for p in sorted(web_runs.glob("*/manifest.json")):
        m = json.loads(p.read_text())
        runs.append({k: m[k] for k in ("run_id", "recorded_at", "provider", "label",
                                       "live_inference", "note", "correction_at")})
    (web_runs / "index.json").write_text(json.dumps({"default": run_id, "runs": runs}, indent=2)
                                         + "\n")
    return out
