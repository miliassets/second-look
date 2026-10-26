from secondlook.contracts.events import Delivery, Event
from secondlook.generator import generate_slice
from secondlook.generator.corrections import CorrectionSpec
from secondlook.generator.delivery import PRESETS, FaultConfig, apply_faults
from secondlook.generator.engine import PERIODS
from secondlook.ledger.ingest import GAP_GRACE_MS, Ingestor
from secondlook.ledger.sqlite_store import SQLiteStore
from secondlook.timeline import to_match_ms

SEED = 20261024


def mk(seq, ms=None, type_="pass", team="IRV"):
    ms = ms if ms is not None else 600_000 + seq * 1000
    return Event(event_id=f"e{seq}", match_id="m001", seq=seq, type=type_, period=2,
                 match_clock_ms=ms, team_id=team, player_id="IRV-08")


def dl(ev, arrive=None, order=0, kind="normal"):
    base = to_match_ms(ev.period, ev.match_clock_ms, PERIODS)
    return Delivery(event=ev, arrival_order=order, arrival_match_ms=arrive or base, kind=kind)


def new():
    s = SQLiteStore(":memory:", "m001")
    return s, Ingestor(s, PERIODS)


def test_match_ms_uses_period_lengths():
    s, ing = new()
    r = ing.ingest(dl(mk(1, ms=600_000)))
    se = s.get_event("e1")
    assert se.match_ms == 2_820_000 + 600_000 and r.kind == "append" and r.rev == 1


def test_duplicate_gives_no_new_rev():
    s, ing = new()
    d = dl(mk(1))
    ing.ingest(d)
    r = ing.ingest(dl(mk(1), arrive=d.arrival_match_ms + 1500, kind="duplicate"))
    assert r.duplicate and r.rev is None
    assert s.current_rev() == 1 and s.stat("duplicates") == 1
    assert len(s.get_events()) == 1


def test_late_event_gets_kind_late_within_grace():
    s, ing = new()
    ing.ingest(dl(mk(1)))
    ing.ingest(dl(mk(3)))  # seq 2 missing, pending
    r = ing.ingest(dl(mk(2), arrive=to_match_ms(2, 603_000, PERIODS)))
    assert r.kind == "late" and r.touched_ids == ["e2"]
    assert r.touched_points == [("IRV", "pass", 2_820_000 + 602_000)]
    assert s.gaps() == []
    assert [x.kind for x in s.revisions()] == ["append", "append", "late"]


def test_gap_opens_after_grace_and_closes_on_arrival():
    s, ing = new()
    ing.ingest(dl(mk(1)))
    ing.ingest(dl(mk(3)))
    far = dl(mk(4), arrive=to_match_ms(2, 603_000 + GAP_GRACE_MS + 1000, PERIODS))
    r = ing.ingest(far)
    assert len(r.gap_revs) == 1
    assert len(s.gaps(open_only=True)) == 1  # seq 2 is open
    r2 = ing.ingest(dl(mk(2), arrive=far.arrival_match_ms + 2000))
    assert r2.kind == "gap"
    g = s.gaps()[0]
    assert g.closed_rev == r2.rev and s.gaps(open_only=True) == []
    kinds = [x.kind for x in s.revisions()]
    assert kinds == ["append", "append", "gap", "append", "gap"]


def test_void_correction_sets_status_and_touches_goal_point():
    m = generate_slice(SEED, corrections=[CorrectionSpec("void_goal")])
    s, ing = new()
    results = [ing.ingest(dl(e, order=i)) for i, e in enumerate(m.events)]
    goal = next(e for e in m.events if e.type == "goal")
    corr = next(r for r in results if r.kind == "correction")
    assert s.get_event(goal.event_id).status == "void"
    assert corr.touched_ids == [goal.event_id]
    assert corr.touched_points[0][:2] == ("IRV", "goal")
    assert not any(x.event.type == "goal" for x in s.get_events())
    assert s.get_events(types=["goal"], statuses=("void",))


def test_replace_correction_inserts_new_version():
    m = generate_slice(SEED, corrections=[CorrectionSpec("reattribute", new_player_id="IRV-06")])
    s, ing = new()
    for i, e in enumerate(m.events):
        ing.ingest(dl(e, order=i))
    c = next(e for e in m.events if e.type == "event_correction")
    old = s.get_event(c.qualifiers["corrects"])
    assert old.status == "corrected" and old.corrected_by == c.qualifiers["replacement"]["event_id"]
    new_ev = s.get_event(old.corrected_by)
    assert new_ev.status == "active" and new_ev.event.player_id == "IRV-06"
    active_tackles = [x for x in s.get_events(types=["tackle"]) if x.event.team_id == "IRV"]
    assert any(x.event.player_id == "IRV-06" for x in active_tackles)
    assert old.event.event_id not in {x.event.event_id for x in s.get_events()}


def test_full_slice_with_demo_faults_ingests_everything_once():
    m = generate_slice(SEED, corrections=[CorrectionSpec("void_goal")])
    ds = apply_faults(m.events, PERIODS, PRESETS["heavy"], SEED)
    s, ing = new()
    for d in ds:
        ing.ingest(d)
    ing.tick(ds[-1].arrival_match_ms + 60_000)
    ids = {x.event.event_id for x in s.get_events(statuses=("active", "void", "corrected"))}
    assert ids == {e.event_id for e in m.events}
    assert s.stat("duplicates") == sum(1 for d in ds if d.kind == "duplicate")
    assert s.gaps(open_only=True) == []
    # every late/gap revision has an event id; revisions are contiguous
    revs = [r.rev for r in s.revisions()]
    assert revs == list(range(1, len(revs) + 1))


def test_open_gap_visible_before_arrival():
    s, ing = new()
    ing.ingest(dl(mk(1)))
    ing.ingest(dl(mk(5)))
    assert s.gaps() == []  # still within grace
    ing.tick(to_match_ms(2, 605_000 + GAP_GRACE_MS, PERIODS))
    assert [(g.from_seq, g.to_seq) for g in s.gaps(open_only=True)] == [(2, 2), (3, 3), (4, 4)]


def test_faultconfig_default_is_clean_pass_through():
    m = generate_slice(SEED)
    ds = apply_faults(m.events, PERIODS, FaultConfig(), SEED)
    s, ing = new()
    for d in ds:
        r = ing.ingest(d)
        assert r.kind == "append"
    assert s.gaps() == [] and s.stat("duplicates") == 0
