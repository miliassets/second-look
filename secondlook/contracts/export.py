"""Export JSON Schemas to schemas/ (stable, sorted)."""
import json
from pathlib import Path

from pydantic import BaseModel

from . import agents_io, claims, events, overlay, prefs

MODELS: dict[str, type[BaseModel]] = {
    "overlay": overlay.Overlay,
    "event": events.Event,
    "frame": events.Frame,
    "claim": claims.Claim,
    "candidate": agents_io.Candidate,
    "verdict": agents_io.Verdict,
    "story": agents_io.Story,
    "viewer_output": agents_io.ViewerOutput,
    "viewer_profile": prefs.ViewerProfile,
}


def schemas() -> dict[str, str]:
    return {
        n: json.dumps(m.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for n, m in MODELS.items()
    }


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in schemas().items():
        p = out_dir / f"{name}.schema.json"
        p.write_text(text)
        paths.append(p)
    return paths


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[2] / "schemas")
