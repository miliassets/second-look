"""Recorder: run a match once, write everything a static replay needs (design §6.3).

runs/<run_id>/
  manifest.json  metadata.json  events.jsonl  frames.jsonl.gz  overlays.jsonl  workflow.jsonl
  calls.jsonl  askwhy.jsonl  metrics.json  stream.jsonl.gz   (stream = every non-frame message
  in publication order with the feed clock, the authoritative source for replay)
"""
from __future__ import annotations

import gzip
import json
import statistics
import subprocess
import time
from pathlib import Path

from ..publish.bus import Bus
from ..runner import RunConfig, Runner

SUGGESTED = {
    "analyst-en": ["Why did Ironvale score?", "What is the score?", "Who has the ball more?"],
    "fan-es": ["¿Por qué marcó Ironvale?", "¿Cuál es el marcador?", "¿Quién tiene más el balón?"],
}


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def percentile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


async def record_run(cfg: RunConfig, out_root: Path, provider_label: str | None = None
                     ) -> Path:
    bus = Bus()
    runner = Runner(cfg, bus)
    q, _ = bus.subscribe(None)  # all messages from now on, frames included
    t0 = time.time()
    await runner.run()
    answers = []
    if runner.services is not None:
        for prof, qs in SUGGESTED.items():
            for question in qs:
                a = await runner.ask(question, prof)
                answers.append({"profile_id": prof, "question": question, "text": a["text"],
                                "supported": a["supported"], "overlay_id": a["overlay_id"]})
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t0))
    run_id = f"{stamp}-{cfg.scenario if cfg.scope == 'full' else 'slice'}-{cfg.seed}"
    d = out_root / run_id
    d.mkdir(parents=True, exist_ok=True)
    md = runner.match.metadata
    provider = provider_label or cfg.llm
    calls = runner.store.calls()
    ok = [c for c in calls if c["outcome"] == "ok"]
    live = provider in ("github", "foundry", "gemini")
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "label": "recorded agent run", "recorded_at": stamp,
        "git_sha": git_sha(), "provider": provider, "model": runner.services.llm.model
        if runner.services else "none", "seed": cfg.seed, "scope": cfg.scope,
        "scenario": cfg.scenario, "faults": cfg.faults,
        "live_inference": live,
        "models_served": sorted({c["model"] for c in ok if c["model"]}) if live else [],
        "calls": {"total": len(calls), "ok": len(ok), "cached": sum(1 for c in ok if c["cached"]),
                  "failed": len(calls) - len(ok)},
        "start_ms": runner.meta_payload()["start_ms"], "end_ms": runner.now_ms,
        "note": ("Rule-based fake model (not recorded model output)"
                 if provider == "fake" else
                 "Real model calls (%s); failed or timed-out calls fell back to factual cards"
                 % provider),
        "correction_at": next((m.payload["clock"] for m in msgs if m.type == "event"
                               and m.payload["type"] == "event_correction"), None),
    }, indent=2) + "\n")
    (d / "metadata.json").write_text(md.model_dump_json(indent=2) + "\n")
    frames = [m for m in msgs if m.type == "frame"]
    rest = [m for m in msgs if m.type != "frame"
            and not (m.type == "overlay" and m.payload["kind"] == "answer")]  # see askwhy.jsonl
    with gzip.open(d / "frames.jsonl.gz", "wt", encoding="utf-8") as f:
        for m in frames:
            f.write(dumps({"at_ms": m.at_ms, **m.payload}) + "\n")
    with gzip.open(d / "stream.jsonl.gz", "wt", encoding="utf-8") as f:
        for i, m in enumerate(rest):
            f.write(dumps({"i": i, "type": m.type, "profile_id": m.profile_id, "at_ms": m.at_ms,
                           "payload": m.payload}) + "\n")
    (d / "events.jsonl").write_text("".join(dumps(m.payload) + "\n" for m in rest
                                            if m.type == "event"))
    ov = [m for m in rest if m.type == "overlay"]
    (d / "overlays.jsonl").write_text("".join(dumps({"at_ms": m.at_ms, **m.payload}) + "\n"
                                              for m in ov))
    wf = []
    for mo in runner.store.list_kind("moment"):
        sid = "st-" + mo.moment_id.removeprefix("mo-")
        wf.append({
            "moment": mo.model_dump(mode="json"),
            "candidates": [c.model_dump(mode="json") for c in
                           runner.store.list_kind("candidate", moment_id=mo.moment_id)],
            "claims": {c.claim_id: c.model_dump(mode="json") for c in runner.store.list_kind(
                "claim") if c.claim_id.startswith(mo.moment_id)},
            "verdicts": [v.model_dump(mode="json") for v in runner.store.list_kind(
                "verdict", moment_id=mo.moment_id)],
            "stories": [s.model_dump(mode="json") for s in runner.store.list_kind(
                "story", moment_id=mo.moment_id)],
            "story_id": sid})
    (d / "workflow.jsonl").write_text("".join(dumps(w) + "\n" for w in wf))
    (d / "calls.jsonl").write_text("".join(dumps(c) + "\n" for c in calls))
    (d / "askwhy.jsonl").write_text("".join(dumps(a) + "\n" for a in answers))
    lat = [m.payload["source"]["latency_ms"] for m in ov
           if m.payload["kind"] == "explanation" and m.payload["source"].get("latency_ms") is not None]
    moments = [m for m in runner.store.list_kind("moment") if m.kind != "question"]
    (d / "metrics.json").write_text(json.dumps({
        "moments": len(moments),
        "fallbacks": sum(1 for m in moments if m.status == "fallback"),
        "explanations": sum(1 for m in ov if m.payload["kind"] == "explanation"),
        "corrections_reflected": sum(1 for m in ov if m.payload["kind"] == "correction_notice"),
        "latency_ms_p50": percentile(lat, 0.5), "latency_ms_p95": percentile(lat, 0.95),
        "latency_ms_mean": statistics.mean(lat) if lat else None,
        "agent_calls": len(calls), "tokens_in": sum(c["tokens_in"] or 0 for c in calls),
        "tokens_out": sum(c["tokens_out"] or 0 for c in calls),
        "wall_seconds": round(time.time() - t0, 1),
    }, indent=2) + "\n")
    return d
