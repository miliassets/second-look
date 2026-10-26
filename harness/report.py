"""Write the results JSON and a Markdown table. Refuses to write if any sequence is missing."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from .metrics import SYSTEMS, Row, summarize

METRIC_LABELS = {
    "unsupported_claims_per_100": "Unsupported claims / 100 proposed (lower is better)",
    "published_accuracy_pct": "Published accuracy % (higher)",
    "fallback_rate_pct": "Fallback rate % (lower)",
    "corrections_both_5s_pct": "Corrections in both views <= 5 s % (higher)",
    "p95_latency_ms": "p95 latency ms (lower)",
    "survival_under_fault_pct": "Survival under fault % (higher)",
    "tokens_per_completed_moment": "Tokens per completed moment (lower)",
}


class IncompleteRun(Exception):
    pass


def check_complete(rows: list[Row], seq_ids: list[str], systems=SYSTEMS) -> None:
    have = {(r.system, r.seq_id) for r in rows}
    missing = [(s, q) for s in systems for q in seq_ids if (s, q) not in have]
    if missing:
        raise IncompleteRun(f"{len(missing)} (system, sequence) results missing, e.g. {missing[:3]}"
                            "; a partial run is never reported as complete")


def fmt(v, digits=1):
    return "n/a" if v is None else f"{v:.{digits}f}"


def markdown(summary: dict, meta: dict) -> str:
    lines = [f"# Single-agent comparison ({meta['date']}, {meta['git_sha']})", ""]
    if meta.get("partial"):
        lines += [f"> **PARTIAL RUN ({meta['n_sequences']} of 30 sequences).** Not a complete "
                  "result and not to be reported as one.", ""]
    if meta["provider"] == "fake":
        lines += ["> **NOT a model comparison.** Provider is the rule-based `fake` stand-in: these "
                  "numbers only test the harness plumbing. Run `python -m secondlook harness "
                  "--llm github|foundry` with a token for real results.", ""]
    lines += [f"Provider `{meta['provider']}`, model `{meta['model']}`, {meta['n_sequences']} "
              "held-out sequences (10 clean, 10 misleading possession, 5 late corrections, 5 "
              "injected timeouts). 95 % bootstrap intervals over sequences in brackets.", "",
              "| Metric | multi | single | no_editor |", "| :- | -: | -: | -: |"]
    for k, label in METRIC_LABELS.items():
        cells = []
        for s in SYSTEMS:
            e = summary[s][k]
            ci = e["ci95"]
            cells.append(f"{fmt(e['value'])}" + (f" [{fmt(ci[0])}, {fmt(ci[1])}]"
                                                 if ci and ci[0] is not None else ""))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "Publish what comes out, including losses. If `multi` is not better on any "
              "metric, tell the Champion before the video is scripted.", ""]
    return "\n".join(lines)


def write_report(rows: list[Row], seq_ids: list[str], meta: dict, out_dir: Path) -> tuple[Path, Path]:
    check_complete(rows, seq_ids)
    by = {s: [r for r in rows if r.system == s] for s in SYSTEMS}
    summary = summarize(by)
    meta = {**meta, "partial": len(seq_ids) < 30}
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {**meta, "partial": len(seq_ids) < 30}
    stem = f"{meta['date']}-{meta['git_sha']}-{meta['provider']}" + ("-partial" if meta["partial"] else "")
    js = out_dir / f"{stem}.json"
    js.write_text(json.dumps({"meta": meta, "summary": summary,
                              "rows": [asdict(r) for r in rows]}, indent=2) + "\n")
    md = out_dir / f"{stem}.md"
    md.write_text(markdown(summary, meta))
    return js, md


def meta_now(provider: str, model: str, n: int, sha: str) -> dict:
    return {"date": time.strftime("%Y-%m-%d"), "git_sha": sha, "provider": provider,
            "model": model, "n_sequences": n}
