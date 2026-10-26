import math
from collections import Counter, defaultdict

import pytest

from secondlook.contracts.events import Event, Frame
from secondlook.generator import generate_slice, write
from secondlook.generator.roster import build_players

SEED = 20261024


@pytest.fixture(scope="module")
def match():
    return generate_slice(SEED)


def test_same_seed_identical_files(tmp_path):
    h1 = write(generate_slice(SEED), tmp_path / "a")
    h2 = write(generate_slice(SEED), tmp_path / "b")
    assert h1 == h2


def test_different_seed_differs():
    a = [e.model_dump() for e in generate_slice(SEED).events]
    b = [e.model_dump() for e in generate_slice(SEED + 1).events]
    assert a != b


def test_seq_strictly_increasing_and_clock_monotonic(match):
    seqs = [e.seq for e in match.events]
    assert seqs == sorted(set(seqs))
    assert seqs == list(range(1, len(seqs) + 1))
    clocks = [e.match_clock_ms for e in match.events]
    assert clocks == sorted(clocks)
    assert len({e.event_id for e in match.events}) == len(match.events)


def test_every_event_validates_after_round_trip(match, tmp_path):
    write(match, tmp_path)
    for line in (tmp_path / "match.jsonl").read_text().splitlines():
        Event.model_validate_json(line)
    for line in (tmp_path / "frames.jsonl").read_text().splitlines():
        Frame.model_validate_json(line)


def test_goal_between_66_and_68_with_long_chain(match):
    goals = [e for e in match.events if e.type == "goal"]
    assert len(goals) == 1
    g = goals[0]
    minute = 45 + g.match_clock_ms / 60000
    assert 66 <= minute <= 68 and g.team_id == "IRV" and g.player_id == "IRV-10"
    shot = next(e for e in match.events if e.event_id == g.related_ids[0])
    chain = [e for e in match.events
             if e.possession_id == shot.possession_id and e.type == "pass"
             and e.outcome == "complete"]
    assert len(chain) >= 8
    assert match.ground_truth["beats"]["goal"]["chain_passes"] == len(chain)
    assert g.qualifiers["home_score"] == 1 and g.qualifiers["away_score"] == 1


def test_58_substitution_and_tactical_shift(match):
    sub = next(e for e in match.events if e.type == "substitution")
    shift = next(e for e in match.events if e.type == "tactical_shift")
    assert 57.9 <= 45 + sub.match_clock_ms / 60000 <= 59
    assert sub.player_id == "KBY-07" and sub.qualifiers["on"] == "KBY-14"
    assert shift.team_id == "KBY" and shift.seq > sub.seq


def test_possession_change_follows_every_flip(match):
    last_team = None
    last_poss = None
    for e in match.events:
        if e.type in ("match_metadata", "lineup"):
            continue
        if last_poss is not None and e.possession_id != last_poss:
            assert e.type in ("possession_change", "kickoff"), e
        last_poss = e.possession_id
        last_team = e.team_id
    assert last_team


def test_irv_dominates_midfield_phase(match):
    def passes(team, lo, hi):
        return sum(1 for e in match.events if e.type == "pass" and e.team_id == team
                   and lo <= e.match_clock_ms < hi)
    # 60:00-66:00 is 900_000..1_260_000 into period 2
    assert passes("IRV", 900_000, 1_260_000) > passes("KBY", 900_000, 1_260_000)


def test_frames_shape_and_speed(match):
    f = match.frames
    assert [x.match_clock_ms for x in f[:3]] == [600_000, 600_500, 601_000]
    assert all(len(x.players) == 22 for x in f)
    prev = None
    top = 0.0
    for x in f:
        cur = {p.player_id: (p.x, p.y) for p in x.players}
        if prev:
            for pid, (px, py) in cur.items():
                if pid in prev:
                    v = math.hypot(px - prev[pid][0], py - prev[pid][1]) / 0.5 * 3.6
                    top = max(top, v)
        prev = cur
        assert 0 <= x.ball[0] <= 105 and 0 <= x.ball[1] <= 68
    assert top <= 36.0


def test_frames_follow_substitution(match):
    before = {p.player_id for p in match.frames[0].players}
    after = {p.player_id for p in match.frames[-1].players}
    assert "KBY-07" in before and "KBY-14" not in before
    assert "KBY-14" in after and "KBY-07" not in after


def test_roster_is_invented_and_complete():
    ps = build_players()
    assert len(ps) == 36
    assert Counter(p.team_id for p in ps) == {"KBY": 18, "IRV": 18}
    names = {p.player_id: p.name for p in ps}
    assert names["IRV-10"] == "Teodor Vask" and names["KBY-09"] == "Emre Tazlan"
    assert names["IRV-05"] == "Ruben Kaltz" and names["KBY-06"] == "Callum Rook"


def test_no_scoring_outside_script(match):
    scored = defaultdict(int)
    for e in match.events:
        if e.type == "shot" and e.outcome == "scored":
            scored[e.team_id] += 1
    assert dict(scored) == {"IRV": 1}
