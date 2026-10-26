"""Seeded, deterministic synthetic match generator."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..contracts.events import Event, Frame, MatchMetadata
from .corrections import CorrectionSpec, insert_corrections
from .engine import Engine
from .engine_full import FullEngine
from .frames import build_frames
from .script import SLICE_END_MS, SLICE_START_MS, ground_truth_phases


@dataclass
class GeneratedMatch:
    metadata: MatchMetadata
    events: list[Event]
    frames: list[Frame]
    ground_truth: dict


def generate_slice(seed: int, corrections: list[CorrectionSpec] | None = None) -> GeneratedMatch:
    eng = Engine(seed)
    events = eng.run()
    frames = build_frames(events, eng.players, SLICE_START_MS, SLICE_END_MS, seed)
    truth = {
        "seed": seed, "scope": "slice 55'-70'", "phases": ground_truth_phases(),
        "beats": eng.truth,
        "note": "Never shown to the agents. Used by tests and the harness.",
    }
    if corrections:
        events = insert_corrections(events, corrections, truth)
        truth["corrections"] = [c.__dict__ for c in corrections]
    return GeneratedMatch(eng.metadata(), events, frames, truth)


def write(match: GeneratedMatch, out_dir: Path) -> dict[str, str]:
    """Write the public files; return {filename: sha256}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "match.jsonl": "".join(e.model_dump_json() + "\n" for e in match.events),
        "frames.jsonl": "".join(f.model_dump_json() + "\n" for f in match.frames),
        "metadata.json": match.metadata.model_dump_json(indent=2) + "\n",
        "ground_truth.json": json.dumps(match.ground_truth, indent=2, sort_keys=True) + "\n",
    }
    hashes = {}
    for name, text in files.items():
        (out_dir / name).write_text(text)
        hashes[name] = hashlib.sha256(text.encode()).hexdigest()
    return hashes


def generate_match(seed: int, scenario: str = "demo",
                   corrections: list[CorrectionSpec] | None = None,
                   with_frames: bool = True) -> GeneratedMatch:
    """Full 90 minutes. `demo` includes the 81' offside correction unless corrections=[]."""
    eng = FullEngine(seed, scenario)
    events = eng.run()
    frames: list[Frame] = []
    for period in (1, 2) if with_frames else ():
        frames += build_frames(events, eng.players, 0, eng.periods[period - 1]["length_ms"],
                               seed, period)
    truth = {"seed": seed, "scope": f"full match ({scenario})", "beats": eng.truth,
             "note": "Never shown to the agents. Used by tests and the harness."}
    if corrections is None and scenario == "demo":
        corrections = [CorrectionSpec("void_goal", target_event_id=eng.truth[
            "irv_goal_81_offside"]["event_id"], reason="offside after review")]
    if corrections:
        events = insert_corrections(events, corrections, truth)
        truth["corrections"] = [c.__dict__ for c in corrections]
    score = dict(eng.truth.get("final_score_before_corrections", {}))
    for c in corrections or []:
        if c.kind == "void_goal":
            goal = next(e for e in events if e.event_id == c.target_event_id)
            score[goal.team_id] -= 1
    truth["final_score"] = score
    md = eng.metadata()
    md.seed = seed
    return GeneratedMatch(md, events, frames, truth)
