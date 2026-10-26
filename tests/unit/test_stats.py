import pytest

from secondlook.contracts.events import Event, Frame, PlayerPos
from secondlook.generator import generate_slice
from secondlook.generator.engine import PERIODS
from secondlook.ledger.store import StoredEvent
from secondlook.stats.engine import StatsEngine
from secondlook.stats.phases import phase_changes, phase_label, phase_timeline
from secondlook.stats.physical import PhysicalEngine
from secondlook.timeline import to_match_ms

P2 = PERIODS[0]["length_ms"]


def E(i, t, type_, team, ms, outcome=None, poss=1, x=50.0, player="p", q=None):
    return StoredEvent(event=Event(
        event_id=f"e{i}", match_id="m", seq=i, type=type_, period=2, match_clock_ms=ms,
        team_id=team, player_id=player, possession_id=poss, x=x, y=34.0, outcome=outcome,
        qualifiers=q or {}), match_ms=P2 + ms, rev=i)


META = StoredEvent(event=Event(
    event_id="e0", match_id="m", seq=0, type="match_metadata", period=2, match_clock_ms=0,
    qualifiers={"score": {"A": 1, "B": 0}, "home": "A", "away": "B",
                "attacking_direction_p2": {"A": -1, "B": 1}}), match_ms=P2, rev=0)


def fixture():
    return StatsEngine([
        META,
        E(1, 0, "pass", "A", 1000, "complete", poss=1),
        E(2, 0, "pass", "A", 2000, "complete", poss=1),
        E(3, 0, "pass", "A", 3000, "incomplete", poss=1),
        E(4, 0, "interception", "B", 4000, "won", poss=1, x=20.0),   # B own half
        E(5, 0, "possession_change", "B", 4100, poss=2),
        E(6, 0, "pass", "B", 5000, "complete", poss=2),
        E(7, 0, "pressure", "A", 5500, "forced_turnover", poss=2),
        E(8, 0, "tackle", "A", 6000, "won", poss=2, x=30.0),  # A attacks -x: x=30 -> norm 75
        E(9, 0, "possession_change", "A", 6100, poss=3),
        E(10, 0, "pass", "A", 7000, "complete", poss=3, player="a9"),
        E(11, 0, "shot", "A", 8000, "saved", poss=3),
        E(12, 0, "pressure", "B", 8500, "none", poss=3),
        E(13, 0, "foul", "B", 9000, poss=3),
        E(14, 0, "corner", "A", 12000, "complete", poss=3),
    ])


def test_counts_exact():
    s = fixture()
    w = (P2, P2 + 20000)
    assert s.count("passes", "A", *w).value == 4
    assert s.count("completed_passes", "A", *w).value == 3
    assert s.count("passes", "B", *w).value == 1
    assert s.count("passes", "A", *w, player="a9").value == 1
    assert s.count("recoveries", "B", *w).value == 1
    assert s.count("recoveries", "A", *w).value == 1
    assert s.count("recoveries_att_third", "A", *w).value == 1  # norm x = 75
    assert s.count("recoveries_att_third", "B", *w).value == 0  # norm x = 20
    assert s.count("pressures", "A", *w).value == 1
    assert s.count("forced_turnovers", "A", *w).value == 1
    assert s.count("forced_turnovers", "B", *w).value == 0
    assert s.count("shots", "A", *w).value == 1
    assert s.count("possession_changes", "A", *w).value == 1
    assert s.count("possession_changes", None, *w).value == 2
    assert s.count("fouls", "B", *w).value == 1 and s.count("corners", "A", *w).value == 1


def test_counts_window_is_inclusive_and_reports_evidence():
    s = fixture()
    st = s.count("passes", "A", P2 + 2000, P2 + 3000)
    assert st.value == 2 and st.event_ids == ["e2", "e3"] and st.event_types == ["pass"]
    with pytest.raises(ValueError):
        s.count("bogus", "A", 0, 1)


def test_rates_exact():
    s = fixture()
    w = (P2, P2 + 20000)
    assert s.rate("pass_completion", "A", *w).value == pytest.approx(75.0)
    assert s.rate("pass_completion", "B", *w).value == pytest.approx(100.0)
    assert s.rate("press_success", "A", *w).value == pytest.approx(100.0)
    assert s.rate("press_success", "B", *w).value == pytest.approx(0.0)
    assert s.rate("pass_completion", "A", P2 + 50000, P2 + 60000).value is None


def test_possession_share_by_time():
    s = fixture()
    # window 1000..9000: A owns poss 1 (1000-4000 =3000 incl. event 4 at 4000) ...
    a = s.rate("possession_share", "A", P2 + 1000, P2 + 9000).value
    b = s.rate("possession_share", "B", P2 + 1000, P2 + 9000).value
    assert a + b == pytest.approx(100.0)
    # A: 1000->2000,2000->3000,3000->4000 (3000) + 4000(e4,poss1)->4100 (100) + poss3 6100->9000 (2900)
    # B: 4100->5000 (900) + 5000->5500 poss2 (500) + 5500->6000 (500) + 6000->6100 (100)
    assert a == pytest.approx(100 * 6000 / 8000)


def test_chain_gap_seconds_score_compare():
    s = fixture()
    c = s.chain(1)
    assert c.value == 2 and c.event_types == ["e4"]  # ended with the interception
    assert s.gap_seconds("e2", "e6").value == 3.0
    assert s.gap_seconds("e6", "e2").value is None
    sc, _ = s.score(P2 + 20000)
    assert sc == {"A": 1, "B": 0}
    cmp = s.compare("passes", "A", (P2, P2 + 3000), (P2 + 6000, P2 + 9000))
    assert cmp["a"] == 3 and cmp["b"] == 1 and cmp["delta"] == -2


def test_score_counts_goals_and_own_goals():
    evs = [META, E(1, 0, "goal", "B", 1000), E(2, 0, "own_goal", "B", 2000),
           E(3, 0, "goal", "A", 3000)]
    s = StatsEngine(evs)
    assert s.score(P2 + 500)[0] == {"A": 1, "B": 0}
    assert s.score(P2 + 1500)[0] == {"A": 1, "B": 1}
    sc, ids = s.score(P2 + 5000)
    assert sc == {"A": 3, "B": 1} and ids == ["e1", "e2", "e3"]


def _passes(team, start_ms, n, ok, poss0):
    out = []
    for i in range(n):
        out.append(E(100 + poss0 * 100 + i, 0, "pass", team, start_ms + i * 1000,
                     "complete" if i < ok else "incomplete", poss=poss0))
    return out


def test_phase_labels_on_two_crafted_windows():
    # control: A holds the ball for 100 s with 90 % completion; B briefly
    ctrl = _passes("A", 0, 100, 90, 1) + _passes("B", 100_000, 10, 8, 2)
    sc = StatsEngine([META, *ctrl])
    assert phase_label(sc, "A", P2, P2 + 110_000) == "control"
    assert phase_label(sc, "B", P2, P2 + 110_000) == "even"
    # chaos: many turnovers, low completion
    evs = [META]
    t = 0
    for k in range(12):  # 12 changes in ~2 minutes -> 6/min
        team = "A" if k % 2 == 0 else "B"
        evs.append(E(1000 + 10 * k, 0, "possession_change", team, t, poss=10 + k))
        evs.append(E(1001 + 10 * k, 0, "pass", team, t + 1000, "incomplete" if k % 3 else
                     "complete", poss=10 + k))
        t += 10_000
    chaos = StatsEngine(evs)
    assert phase_label(chaos, "A", P2, P2 + 120_000) == "chaos"


def test_phase_changes_need_hold():
    steps = 30_000
    tl = [(i * steps, lbl) for i, lbl in enumerate(
        ["even"] * 3 + ["control"] * 7 + ["even", "control"] + ["even"] * 7)]
    ch = phase_changes(tl)
    assert [lbl for _, lbl in ch] == ["control", "even"]  # the 1-step blips don't count


def test_phase_timeline_on_generated_slice_sees_ironvale_control():
    m = generate_slice(20261024)
    ev = [StoredEvent(event=e, match_ms=to_match_ms(e.period, e.match_clock_ms, PERIODS), rev=i)
          for i, e in enumerate(m.events)]
    s = StatsEngine(ev)
    lo, hi = P2 + 600_000, P2 + 1_380_000
    tl = phase_timeline(s, "IRV", lo, hi)
    assert tl and all(lbl in ("control", "chaos", "even") for _, lbl in tl)
    share = s.rate("possession_share", "IRV", P2 + 960_000, P2 + 1_260_000).value
    assert share > 50


def frames(speeds_kmh_by_step, pid="p1", start_ms=0):
    out, x, ms = [], 10.0, start_ms
    out.append(Frame(match_id="m", period=2, match_clock_ms=ms,
                     players=[PlayerPos(player_id=pid, x=x, y=10.0)], ball=(0, 0)))
    for v in speeds_kmh_by_step:
        x += v / 3.6 * 0.5
        ms += 500
        out.append(Frame(match_id="m", period=2, match_clock_ms=ms,
                         players=[PlayerPos(player_id=pid, x=x, y=10.0)], ball=(0, 0)))
    return out


def test_physical_from_crafted_track():
    steps = [10, 20, 30, 36, 28, 10, 26, 10]  # km/h per half-second leg
    ph = PhysicalEngine(frames(steps), PERIODS)
    lo, hi = P2, P2 + 10_000
    assert ph.top_speed_kmh("p1", lo, hi).value == pytest.approx(36.0, abs=0.01)
    dist = sum(v / 3.6 * 0.5 for v in steps)
    assert ph.distance_m("p1", lo, hi).value == pytest.approx(dist, abs=0.05)
    # runs >= 25 km/h for >= 1 s: legs 30,36,28 (1.5 s) counts; the lone 26 (0.5 s) does not
    assert ph.sprints("p1", lo, hi).value == 1
    assert ph.top_speed_kmh("p1", P2, P2 + 1000).value == pytest.approx(20.0, abs=0.01)
    assert ph.top_speed_kmh("nobody", lo, hi).value == 0


def test_generated_frames_have_plausible_physical_numbers():
    m = generate_slice(20261024)
    ph = PhysicalEngine(m.frames, PERIODS)
    lo, hi = P2 + 600_000, P2 + 1_500_000
    d = ph.distance_m("IRV-08", lo, hi).value
    assert 500 < d < 4500  # 15 minutes, outfield player
    assert ph.top_speed_kmh("IRV-08", lo, hi).value <= 36
