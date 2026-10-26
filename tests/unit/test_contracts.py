from pathlib import Path

import pytest

from secondlook.contracts import agents_io, claims, events, overlay, prefs
from secondlook.contracts.export import schemas

ROOT = Path(__file__).resolve().parents[2]

OVERLAY_EXAMPLE = {
    "schema_version": "1.0",
    "overlay_id": "ov-m001-st067goal-r1-fan-es",
    "story_id": "st-m001-067-goal",
    "revision": 1,
    "kind": "explanation",
    "viewer_mode": "fan",
    "profile_id": "fan-es",
    "lang": "es",
    "show_at_match_ms": 4064200,
    "duration_ms": 12000,
    "slot": "lower_third",
    "priority": 2,
    "headline": "¡Gol de Ironvale!",
    "text": "Vask remató tras 11 pases seguidos.",
    "spans": [{"start": 0, "end": 4, "ref": "p:IRV-10"}],
    "badges": [],
    "layers": [
        {"type": "chain", "points": [[38.1, 30.2], [44.0, 21.7]],
         "event_ids": ["m001-e01391", "m001-e01392"]},
        {"type": "marker", "at": [94.0, 36.5], "label": "1", "event_id": "m001-e01402"},
        {"type": "zone", "rect": [70, 0, 35, 68], "label_key": "attacking_third"},
        {"type": "player_halo", "player_id": "IRV-10"},
    ],
    "evidence_ids": ["m001-e01391", "m001-e01392", "m001-e01402"],
    "claims": [{"claim_id": "c3", "type": "chain", "value": 11}],
    "supersedes": "ov-m001-st067goal-factual-fan-es",
    "source": {"kind": "live", "trace_id": "t1", "latency_ms": 9400},
}


def test_overlay_example_validates_and_round_trips():
    o = overlay.Overlay.model_validate(OVERLAY_EXAMPLE)
    again = overlay.Overlay.model_validate_json(o.model_dump_json())
    assert again == o
    assert [layer.type for layer in o.layers] == ["chain", "marker", "zone", "player_halo"]


def test_overlay_rejects_unknown_field():
    with pytest.raises(ValueError):
        overlay.Overlay.model_validate({**OVERLAY_EXAMPLE, "bogus": 1})


def test_event_round_trip():
    e = events.Event(event_id="m001-e1", match_id="m001", seq=1, type="pass", period=1,
                     match_clock_ms=1000, team_id="KBY", player_id="KBY-08", x=1.0, y=2.0)
    assert events.Event.model_validate_json(e.model_dump_json()) == e


def test_event_rejects_bad_type():
    with pytest.raises(ValueError):
        events.Event(event_id="x", match_id="m", seq=1, type="bogus", period=1, match_clock_ms=0)


def test_claim_and_agent_models_round_trip():
    c = claims.Claim(claim_id="c1", type="count",
                     params={"metric": "passes", "team_id": "IRV", "from_ms": 0, "to_ms": 10},
                     value=3)
    cand = agents_io.Candidate(scout="pressure", thesis_tag="pressing_recovery",
                               thesis="They won it high.", claims=[c])
    out = agents_io.ScoutOutput(candidates=[cand])
    assert agents_io.ScoutOutput.model_validate_json(out.model_dump_json()) == out
    v = agents_io.Verdict(chosen_candidate_id="x", notes=["pressure: timed out"])
    assert agents_io.Verdict.model_validate_json(v.model_dump_json()) == v
    s = agents_io.Story(story_id="st-1", moment_id="m1", text_en="{p:IRV-10} scored.")
    assert agents_io.Story.model_validate_json(s.model_dump_json()) == s
    rec = claims.ClaimRecord(claim_id="c1", type="count")
    assert rec.status == "proposed"


def test_scout_output_max_two():
    cand = agents_io.Candidate(scout="rhythm", thesis_tag="even_rhythm", thesis="t")
    with pytest.raises(ValueError):
        agents_io.ScoutOutput(candidates=[cand, cand, cand])


def test_default_profiles():
    ids = {p.profile_id: p for p in prefs.DEFAULT_PROFILES}
    assert ids["fan-es"].followed_player_id == "IRV-10"
    assert ids["analyst-en"].lang == "en"


def test_schema_export_matches_committed_snapshot():
    for name, text in schemas().items():
        p = ROOT / "schemas" / f"{name}.schema.json"
        assert p.exists(), f"run python -m secondlook.contracts.export ({name})"
        assert p.read_text() == text, f"schema {name} changed; re-export and review"
