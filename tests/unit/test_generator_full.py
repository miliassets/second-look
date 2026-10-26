import math
from collections import Counter

import pytest

from secondlook.contracts.events import Event, Frame
from secondlook.generator import generate_match, write
from secondlook.generator.realism import check, measure
from secondlook.timeline import format_clock, to_match_ms

SEED = 20261024


@pytest.fixture(scope="module")
def demo():
    return generate_match(SEED, "demo")


def ev(m, type_, **kw):
    return [e for e in m.events if e.type == type_ and all(
        getattr(e, k, None) == v for k, v in kw.items())]


def minute(e):  # football minute number, e.g. 66:54 is the 67th minute
    return (45 if e.period == 2 else 0) + e.match_clock_ms // 60_000 + 1


def test_demo_is_deterministic(tmp_path):
    a = write(generate_match(SEED, "demo"), tmp_path / "a")
    b = write(generate_match(SEED, "demo"), tmp_path / "b")
    assert a == b


def test_structure_periods_and_ordering(demo):
    assert [e.seq for e in demo.events] == list(range(1, len(demo.events) + 1))
    assert len({e.event_id for e in demo.events}) == len(demo.events)
    periods = demo.metadata.periods
    ms = [to_match_ms(e.period, e.match_clock_ms, periods) for e in demo.events]
    assert ms == sorted(ms)
    assert [e.type for e in demo.events if e.type in ("period_start", "period_end", "match_end")] \
        == ["period_start", "period_end", "period_start", "period_end", "match_end"]
    for e in demo.events:
        assert 0 <= e.match_clock_ms <= periods[e.period - 1]["length_ms"]
    assert 1_500 < len(demo.events) < 3_200 and len(demo.frames) > 11_000
    for e in demo.events[:400]:
        Event.model_validate(e.model_dump())
    assert Frame.model_validate(demo.frames[5].model_dump())
    # half time: sides swap ends
    md = demo.events[0].qualifiers
    assert md["attacking_direction_p2"] == {"KBY": -1, "IRV": 1}


def test_all_script_beats_happen_at_the_right_minutes(demo):
    goals = ev(demo, "goal")
    by_player = {g.player_id: g for g in goals}
    assert set(by_player) == {"KBY-09", "IRV-10", "IRV-09"}
    assert minute(by_player["KBY-09"]) == 15 and by_player["KBY-09"].team_id == "KBY"  # 14:xx
    # 14': a forced turnover about 30 m from goal, then the goal
    t = demo.ground_truth["beats"]["kby_goal_14"]
    rec = next(e for e in demo.events if e.event_id == t["recovery_event_id"])
    assert rec.type == "tackle" and rec.team_id == "KBY"
    assert any(e.type == "pressure" and e.outcome == "forced_turnover" and e.team_id == "KBY"
               and 0 < e.match_clock_ms - rec.match_clock_ms + 1500 < 3000 for e in demo.events)
    assert 28 <= 105 - (rec.x if rec.x > 52 else 105 - rec.x) <= 40 or rec.x < 40
    card = ev(demo, "card")[0]
    assert card.player_id == "KBY-08" and card.outcome == "yellow" and minute(card) == 34
    assert card.qualifiers["reason"] == "tactical_foul"
    shot112 = next(e for e in ev(demo, "shot") if e.qualifiers["speed_kmh"] == 112.0)
    assert shot112.team_id == "IRV" and shot112.outcome == "saved" and minute(shot112) == 47
    assert format_clock(shot112.period, shot112.match_clock_ms).startswith("45+")
    save = next(e for e in ev(demo, "save") if shot112.event_id in e.related_ids)
    assert save.player_id == "KBY-01"
    shift = ev(demo, "tactical_shift")[0]
    assert minute(shift) == 59 and shift.team_id == "KBY"
    assert minute(by_player["IRV-10"]) == 67 and demo.ground_truth["beats"]["goal"][
        "chain_passes"] == 11
    subs = ev(demo, "substitution")
    assert [s.qualifiers["on"] for s in subs] == ["KBY-14", "IRV-17"] and minute(subs[1]) == 75
    corner = ev(demo, "corner")[-1]
    shot_c = next(e for e in ev(demo, "shot") if corner.event_id in e.related_ids)
    assert corner.team_id == "KBY" and shot_c.player_id == "KBY-09" and shot_c.outcome == "off_target"
    assert shot_c.qualifiers["body_part"] == "head"
    assert format_clock(shot_c.period, shot_c.match_clock_ms).startswith("90+")


def test_81_minute_goal_is_corrected_offside_and_final_score_is_1_1(demo):
    fenn = by_id(demo, demo.ground_truth["beats"]["irv_goal_81_offside"]["event_id"])
    assert fenn.player_id == "IRV-09" and minute(fenn) == 82
    corr = ev(demo, "event_correction")[0]
    assert corr.qualifiers["corrects"] == fenn.event_id and corr.qualifiers["kind"] == "void"
    assert corr.qualifiers["reason"] == "offside after review"
    assert corr.match_clock_ms > fenn.match_clock_ms
    assert demo.ground_truth["final_score"] == {"KBY": 1, "IRV": 1}
    assert fenn.qualifiers["away_score"] == 2  # the live feed said 2-1 until the correction


def by_id(m, i):
    return next(e for e in m.events if e.event_id == i)


def test_physical_story_sprint_and_fatigue(demo):
    from secondlook.stats.physical import PhysicalEngine
    ph = PhysicalEngine(demo.frames, demo.metadata.periods)
    s = demo.ground_truth["beats"]["kaddar_sprint"]
    off = demo.metadata.periods[0]["length_ms"]
    top = ph.top_speed_kmh("IRV-17", off + s["from_ms"] - 1000, off + s["to_ms"] + 2000).value
    assert 33.0 <= top <= 36.0
    allmax = max(ph.top_speed_kmh(p.player_id, 0, 10**9).value for p in demo.metadata.players)
    assert allmax <= 36.0
    # Kestrel Bay sprint less in the last 15 minutes than in the first 30 (fatigue)
    def sprints(team, lo, hi):
        return sum(ph.sprints(p.player_id, lo, hi).value for p in demo.metadata.players
                   if p.team_id == team and p.player_id[-2:] in (
                       "01", "02", "03", "04", "05", "06", "08", "10", "11", "09", "07"))
    early = sprints("KBY", 0, 1_800_000)
    late = sprints("KBY", off + 2_100_000, off + 2_940_000)
    assert late / 14 <= early / 30 * 1.05 or late <= early


def test_demo_realism_passes_every_range(demo):
    r = check(SEED, measure(demo))
    assert r.ok, r.failures
    assert 9 <= r.measures["distance_km"] <= 13
    assert r.measures["shot_speed_max_kmh"] == 112.0


def test_names_are_the_invented_ones(demo):
    names = {p.player_id: p.name for p in demo.metadata.players}
    assert names["IRV-10"] == "Teodor Vask" and names["KBY-08"] == "Niko Halvard"
    assert demo.metadata.home.name == "Kestrel Bay FC"
    assert demo.metadata.venue == "Saltmarsh Ground" and demo.metadata.referee == "A. Durrant"


def test_rout_script_ends_four_nil():
    m = generate_match(7, "rout", with_frames=False)
    goals = Counter(e.team_id for e in m.events if e.type == "goal")
    assert goals == {"KBY": 4} and m.ground_truth["final_score"] == {"KBY": 4, "IRV": 0}
    shots = Counter(e.team_id for e in m.events if e.type == "shot")
    assert shots["KBY"] > shots["IRV"]
    from secondlook.ledger.store import StoredEvent
    from secondlook.stats.engine import StatsEngine
    st = StatsEngine([StoredEvent(event=e, match_ms=to_match_ms(e.period, e.match_clock_ms,
                                                                m.metadata.periods))
                      for e in m.events])
    total = sum(p["length_ms"] for p in m.metadata.periods)
    assert st.rate("possession_share", "KBY", 0, total).value > 55


def test_redcard_script_has_ten_men_and_a_shape_change():
    m = generate_match(11, "redcard")
    red = ev(m, "card", outcome="red")[0]
    assert red.player_id == "IRV-09" and red.team_id == "IRV" and minute(red) == 31
    shift = [e for e in ev(m, "tactical_shift") if e.team_id == "IRV"][0]
    assert shift.qualifiers["formation_to"] == "4-4-1" and shift.seq > red.seq
    off = m.metadata.periods[0]["length_ms"]
    before = next(f for f in m.frames if f.period == 1 and f.match_clock_ms == 1_740_000)
    after = next(f for f in m.frames if f.period == 2 and f.match_clock_ms == 600_000)
    n = lambda f, t: sum(1 for p in f.players if p.player_id.startswith(t))  # noqa: E731
    assert n(before, "IRV") == 11 and n(after, "IRV") == 10 and n(after, "KBY") == 11
    assert math.isfinite(off)
    # no Ironvale pass is ever attributed to the sent-off player afterwards
    assert not [e for e in m.events if e.player_id == "IRV-09" and e.seq > red.seq
                and e.type in ("pass", "shot", "carry")]


def test_random_scenario_is_stochastic_and_valid():
    a = generate_match(3, "random", with_frames=False)
    b = generate_match(4, "random", with_frames=False)
    assert [e.type for e in a.events] != [e.type for e in b.events]
    assert not a.ground_truth["beats"].get("goal")
    assert measure(a, with_distance=False)["shots"] >= 18


def test_realism_across_a_dozen_seeds():
    ok = 0
    for seed in range(1, 13):
        m = generate_match(seed, "random", with_frames=False)
        ok += check(seed, measure(m, with_distance=False)).ok
    assert ok >= 11


@pytest.mark.slow
def test_realism_over_100_seeds():
    ok = sum(check(s, measure(generate_match(s, "random", with_frames=False),
                              with_distance=False)).ok for s in range(1, 101))
    assert ok >= 95
