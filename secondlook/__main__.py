"""CLI: generate | run | serve (more subcommands arrive with later steps)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import config


def cmd_generate(a) -> int:
    from .generator import generate_slice, write
    out = Path(a.out or f"data/{a.seed}")
    hashes = write(generate_slice(a.seed), out)
    for k, v in hashes.items():
        print(f"{k}  {v[:12]}")
    print(f"written to {out}")
    return 0


def cmd_record(a) -> int:
    from .record.recorder import record_run
    from .runner import RunConfig
    cfg = RunConfig(seed=a.seed, speed="max", faults=a.faults, llm=a.llm, sync_agents=True,
                    scope="full" if a.full else "slice", scenario=a.scenario)
    d = asyncio.run(record_run(cfg, Path(a.out)))
    print(f"recorded {d}")
    return 0


def cmd_export_replay(a) -> int:
    from .record.replay import export_replay
    root = Path(a.runs)
    run = Path(a.run) if a.run else sorted(p for p in root.iterdir() if p.is_dir())[-1]
    out = export_replay(run, Path(a.web_runs))
    print(f"exported {run.name} -> {out}")
    return 0


def cmd_harness(a) -> int:
    import sys
    sys.path.insert(0, str(Path.cwd()))
    from harness.report import IncompleteRun, meta_now, write_report
    from harness.run import run_all
    from secondlook.record.recorder import git_sha
    rows, ids = asyncio.run(run_all(a.llm, a.limit))
    meta = meta_now(a.llm, "fake" if a.llm == "fake" else a.llm, len(ids), git_sha())
    try:
        js, md = write_report(rows, ids, meta, Path(a.out))
    except IncompleteRun as e:
        print("refusing to write a report:", e)
        return 2
    print(md.read_text())
    print(f"written {js} and {md}")
    return 0


def cmd_realism(a) -> int:
    from .generator import generate_match
    from .generator.realism import check, measure, report_markdown
    reps = []
    for seed in range(1, a.seeds + 1):
        reps.append(check(seed, measure(generate_match(seed, "random", with_frames=False),
                                        with_distance=False)))
    demo = generate_match(config.DEMO_SEED, "demo")
    demo_rep = check(config.DEMO_SEED, measure(demo))
    text = report_markdown(reps, f"Realism report ({a.seeds} random seeds)")
    text += ("\n## Demo match (seed %d)\n\n| Measure | Value |\n| :- | -: |\n" % config.DEMO_SEED
             + "\n".join(f"| {k} | {v:.1f} |" for k, v in demo_rep.measures.items())
             + "\n\n" + ("All ranges pass.\n" if demo_rep.ok else
                         "Failures: " + "; ".join(demo_rep.failures) + "\n"))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text.splitlines()[2])
    return 0


def cmd_run(a) -> int:
    from .publish.bus import Bus
    from .runner import RunConfig, Runner
    cfg = RunConfig(seed=a.seed, speed=a.speed, faults=a.faults, llm=a.llm,
                    scope="full" if a.full else "slice", scenario=a.scenario)
    if a.headless:
        async def go():
            bus = Bus()
            r = Runner(cfg, bus)
            await r.run()
            ov = [m for m in bus.history if m.type == "overlay"]
            print(f"events={len(r.match.events)} overlays={len(ov)} rev={r.store.current_rev()}")
            for m in ov:
                o = m.payload
                print(f"  [{o['profile_id']}] {o['kind']:<18} {o['text']}")
        asyncio.run(go())
        return 0
    import uvicorn

    from .api.app import create_app
    app = create_app(cfg, autostart=not a.wait)
    print(f"Second Look on http://{a.host}:{a.port}  (synthetic data, llm={a.llm}, speed={a.speed})")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="secondlook")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write the synthetic match files")
    g.add_argument("--seed", type=int, default=config.DEMO_SEED)
    g.add_argument("--out")
    g.set_defaults(fn=cmd_generate)
    rc = sub.add_parser("record", help="record a run for static replay")
    rc.add_argument("--seed", type=int, default=config.DEMO_SEED)
    rc.add_argument("--llm", default="fake", help="fake | github | foundry")
    rc.add_argument("--faults", default="demo", choices=["none", "demo", "heavy"])
    rc.add_argument("--full", action="store_true")
    rc.add_argument("--scenario", default="demo", choices=["demo", "rout", "redcard", "random"])
    rc.add_argument("--out", default="runs")
    rc.set_defaults(fn=cmd_record)
    ex = sub.add_parser("export-replay", help="copy a recorded run into web/runs/")
    ex.add_argument("--run")
    ex.add_argument("--runs", default="runs")
    ex.add_argument("--web-runs", default="web/runs")
    ex.set_defaults(fn=cmd_export_replay)
    hn = sub.add_parser("harness", help="single-agent comparison over the held-out sequences")
    hn.add_argument("--llm", default="fake", help="fake | github | foundry")
    hn.add_argument("--limit", type=int, help="dev subset: the report is marked PARTIAL")
    hn.add_argument("--out", default="harness/results")
    hn.set_defaults(fn=cmd_harness)
    rl = sub.add_parser("realism", help="realism distribution over seeds")
    rl.add_argument("--seeds", type=int, default=100)
    rl.add_argument("--out", default="data/realism_report.md")
    rl.set_defaults(fn=cmd_realism)
    for name in ("run", "serve"):
        r = sub.add_parser(name, help="stream the match to the viewers")
        r.add_argument("--seed", type=int, default=config.DEMO_SEED)
        r.add_argument("--speed", default="10x", help="1x | 4x | 10x | 60x | max")
        r.add_argument("--faults", default="none", choices=["none", "demo", "heavy"])
        r.add_argument("--llm", default="fake", help="none | fake | github | foundry")
        r.add_argument("--host", default="127.0.0.1")
        r.add_argument("--port", type=int, default=8000)
        r.add_argument("--headless", action="store_true", help="no server; print overlays")
        r.add_argument("--wait", action="store_true", help="do not start the feed until asked")
        r.add_argument("--slice", action="store_true", help="the 55'-70' slice (default)")
        r.add_argument("--full", action="store_true", help="the full 90 minutes")
        r.add_argument("--scenario", default="demo", choices=["demo", "rout", "redcard", "random"])
        r.set_defaults(fn=cmd_run)
    a = p.parse_args(argv)
    if a.cmd == "serve":
        a.wait = True
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
