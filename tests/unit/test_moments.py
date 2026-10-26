import pytest

from secondlook.checker.roster import Roster
from secondlook.checker.verify import Checker
from secondlook.contracts.events import Delivery, Event
from secondlook.contracts.prefs import DEFAULT_PROFILES
from secondlook.generator import generate_slice
from secondlook.generator.corrections import CorrectionSpec
from secondlook.generator.engine import PERIODS
from secondlook.ledger.ingest import Ingestor
from secondlook.ledger.sqlite_store import SQLiteStore
from secondlook.moments.detector import MomentDetector
from secondlook.moments.factual import build_factual_card, factual_overlay_id, story_id_for
from tests.conftest import SEED, ms


def run_detector(events):
    store = SQLiteStore(":memory:", "m001")
    ing = Ingestor(store, PERIODS)
    det = MomentDetector(store, PERIODS)
    for i, e in enumerate(events):
        det.process(ing.ingest(Delivery(event=e, arrival_order=i, arrival_match_ms=ms(e))))
    return store, det


def test_demo_slice_opens_expected_moments():
    m = generate_slice(SEED)
    store, det = run_detector(m.events)
    kinds = [x.kind for x in det.moments]
    assert kinds.count("goal") == 1 and kinds.count("tactical_shift") == 1
    assert "phase_change" in kinds
    goal = next(x for x in det.moments if x.kind == "goal")
    assert goal.moment_id == "mo-m001-067-goal" and goal.team_id == "IRV"
    shift = next(x for x in det.moments if x.kind == "tactical_shift")
    assert shift.team_id == "KBY" and shift.moment_id == "mo-m001-059-tactical_shift"
    ph = next(x for x in det.moments if x.kind == "phase_change")
    assert ph.team_id == "IRV" and ph.detail["label"] == "control"
    assert ph.now_ms < goal.now_ms
    # window rules: <= 120 s lookback, never beyond now, pinned rev
    for x in det.moments:
        assert x.window_to_ms == x.now_ms and x.window_from_ms <= x.now_ms
        assert x.now_ms - x.window_from_ms <= 120_000 or x.kind == "phase_change"
        assert x.as_of_rev > 0
    assert store.get("moment", goal.moment_id).kind == "goal"
    # ordered by time, no duplicates
    assert [x.now_ms for x in det.moments] == sorted(x.now_ms for x in det.moments)
    assert len({x.moment_id for x in det.moments}) == len(det.moments)


def test_window_starts_after_last_phase_change():
    m = generate_slice(SEED)
    _, det = run_detector(m.events)
    ph = next(x for x in det.moments if x.kind == "phase_change")
    goal = next(x for x in det.moments if x.kind == "goal")
    assert goal.window_from_ms >= ph.detail["since_ms"]


def test_disallowed_goal_moment_on_void_correction():
    m = generate_slice(SEED, corrections=[CorrectionSpec("void_goal")])
    _, det = run_detector(m.events)
    kinds = [x.kind for x in det.moments]
    assert "disallowed_goal" in kinds and "goal" in kinds
    d = next(x for x in det.moments if x.kind == "disallowed_goal")
    assert d.team_id == "IRV"


def test_late_goal_still_opens_a_moment_once():
    m = generate_slice(SEED)
    goal = next(e for e in m.events if e.type == "goal")
    rest = [e for e in m.events if e.event_id != goal.event_id]
    store = SQLiteStore(":memory:", "m001")
    ing = Ingestor(store, PERIODS)
    det = MomentDetector(store, PERIODS)
    for i, e in enumerate(rest):
        det.process(ing.ingest(Delivery(event=e, arrival_order=i, arrival_match_ms=ms(e))))
    # the goal arrives 40 s late
    late = Delivery(event=goal, arrival_order=999, arrival_match_ms=ms(goal) + 40_000)
    r = ing.ingest(late)
    got = det.process(r)
    assert [x.kind for x in got] == ["goal"]
    again = ing.ingest(Delivery(event=goal, arrival_order=1000,
                                arrival_match_ms=ms(goal) + 41_000, kind="duplicate"))
    assert det.process(again) == []


def crafted():
    def ev(seq, type_, clock, **kw):
        base = dict(event_id=f"x{seq}", match_id="m001", seq=seq, type=type_, period=2,
                    match_clock_ms=clock, team_id="KBY", player_id="KBY-09", possession_id=1,
                    x=60.0, y=30.0)
        base.update(kw)
        return Event(**base)
    return [
        ev(1, "match_metadata", 600_000, team_id=None, player_id=None, possession_id=None,
           x=None, y=None, qualifiers={"home": "KBY", "away": "IRV", "score": {"KBY": 1, "IRV": 0},
                                       "attacking_direction_p2": {"KBY": -1, "IRV": 1}}),
        ev(2, "shot", 610_000, outcome="saved", end_x=0.0, end_y=34.0,
           qualifiers={"speed_kmh": 108.4, "body_part": "right_foot", "on_target": True}),
        ev(3, "shot", 620_000, outcome="saved", qualifiers={"speed_kmh": 70.0}),
        ev(4, "shot", 625_000, outcome="scored", qualifiers={"speed_kmh": 110.0}),
        ev(5, "card", 630_000, team_id="KBY", player_id="KBY-08", outcome="yellow"),
        ev(6, "period_end", 2_700_000, team_id=None, player_id=None, possession_id=None,
           x=None, y=None),
        ev(7, "match_end", 2_880_000, team_id=None, player_id=None, possession_id=None,
           x=None, y=None),
    ]


def test_fast_shot_card_and_time_moments():
    store, det = run_detector(crafted())
    assert [x.kind for x in det.moments] == ["fast_shot", "card", "full_time"]
    assert det.moments[0].moment_id == "mo-m001-056-fast_shot"  # the 56th minute
    # period_end in period 2 is not a half time


@pytest.fixture(scope="module")
def card_world():
    m = generate_slice(SEED)
    store, det = run_detector(m.events)
    ck = Checker(store, PERIODS, m.frames)
    return m, store, det, ck, Roster.from_metadata(m.metadata)


def test_goal_factual_cards_pass_the_checker_in_both_languages(card_world):
    m, store, det, ck, roster = card_world
    goal = next(x for x in det.moments if x.kind == "goal")
    cards = {p.profile_id: build_factual_card(goal, p, store, ck, roster, PERIODS)
             for p in DEFAULT_PROFILES}
    assert all(cards.values())
    en, es = cards["analyst-en"].overlay, cards["fan-es"].overlay
    assert en.lang == "en" and es.lang == "es" and en.text != es.text
    assert en.text.startswith("Goal for Ironvale: Vask, 66:5")
    assert en.text.endswith("Score 1–1.")
    assert es.text == "¡Gol de Ironvale! Marcó Vask. 1–1."
    assert es.headline == "¡Gol!" and en.headline == "Goal"
    assert all(c.status == "verified" for c in cards["analyst-en"].claims)
    assert en.overlay_id == factual_overlay_id(goal.moment_id, "analyst-en")
    assert en.story_id == story_id_for(goal.moment_id) == "st-m001-067-goal"
    assert en.kind == "factual" and en.show_at_match_ms == goal.now_ms
    assert {s.ref for s in en.spans} == {"p:IRV-10", "t:IRV"}
    assert en.layers and en.evidence_ids
    for card in cards.values():  # independent re-check of each claim gives the same verdict
        for rec in card.claims:
            again = ck.verify(next(c for c in [rec] if True) and
                              __import__("secondlook.contracts.claims", fromlist=["Claim"]).Claim(
                                  claim_id=rec.claim_id, type=rec.type, params=rec.params),
                              goal.now_ms, record=False)
            assert again.status == "verified" and again.value == rec.value


def test_every_moment_kind_has_a_valid_card_in_both_languages():
    events = crafted()
    store, det = run_detector(events)
    ck = Checker(store, PERIODS)
    roster = Roster.from_metadata(generate_slice(SEED).metadata)
    kinds_seen = set()
    for x in det.moments:
        for p in DEFAULT_PROFILES:
            card = build_factual_card(x, p, store, ck, roster, PERIODS)
            assert card is not None, (x.kind, p.profile_id)
            assert card.overlay.text and not any(ch in card.overlay.text for ch in "{}")
            kinds_seen.add(x.kind)
    assert kinds_seen == {"fast_shot", "card", "full_time"}


def test_cards_for_shift_phase_and_disallowed():
    m = generate_slice(SEED, corrections=[CorrectionSpec("void_goal")])
    store, det = run_detector(m.events)
    ck = Checker(store, PERIODS, m.frames)
    roster = Roster.from_metadata(m.metadata)
    by_kind = {x.kind: x for x in det.moments}
    out = {}
    for kind in ("tactical_shift", "phase_change", "disallowed_goal"):
        for p in DEFAULT_PROFILES:
            card = build_factual_card(by_kind[kind], p, store, ck, roster, PERIODS)
            assert card is not None, (kind, p.profile_id)
            out[(kind, p.lang)] = card.overlay.text
    assert out[("disallowed_goal", "en")] == "Updated: Ironvale goal ruled out after review. Score 1–0."
    assert out[("disallowed_goal", "es")] == "Actualizado: gol de Ironvale anulado. 1–0."
    assert out[("phase_change", "en")] == "Rhythm: Ironvale in control."
    assert out[("phase_change", "es")] == "Ritmo: Ironvale en control."
    assert out[("tactical_shift", "es")] == "Cambio táctico de Kestrel Bay."
