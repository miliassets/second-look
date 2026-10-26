
from secondlook.contracts.claims import Claim
from secondlook.generator.engine import PERIODS
from secondlook.stats.physical import PhysicalEngine
from tests.conftest import P2, World, build, ms


def C(cid, type_, **params):
    value = params.pop("value", None)
    return Claim(claim_id=cid, type=type_, params=params, value=value)


def check(w, claim, now=None):
    return w.checker.verify(claim, now if now is not None else w.now)


def test_event_fact_pass_and_fail(world):
    g = world.goal
    ok = check(world, C("c1", "event_fact", event_id=g.event_id, field="team_id", value="IRV"))
    assert ok.status == "verified" and ok.value == "IRV"
    bad = check(world, C("c2", "event_fact", event_id=g.event_id, field="player_id",
                         value="IRV-09"))
    assert bad.status == "failed" and "IRV-10" in bad.reason
    sp = world.shot.qualifiers["speed_kmh"]
    near = check(world, C("c3", "event_fact", event_id=world.shot.event_id,
                          field="qualifiers.speed_kmh", value=sp + 1.5))
    far = check(world, C("c4", "event_fact", event_id=world.shot.event_id,
                         field="qualifiers.speed_kmh", value=sp + 5))
    assert near.status == "verified" and far.status == "failed"
    missing = check(world, C("c5", "event_fact", event_id="nope", field="type", value="pass"))
    assert missing.status == "failed"


def test_count_pass_and_fail(world):
    lo, hi = P2 + 960_000, P2 + 1_260_000
    expect = sum(1 for e in world.match.events if e.type == "pass" and e.team_id == "IRV"
                 and lo <= ms(e) <= hi)
    ok = check(world, C("c1", "count", metric="passes", team_id="IRV", from_ms=lo, to_ms=hi,
                        value=expect))
    bad = check(world, C("c2", "count", metric="passes", team_id="IRV", from_ms=lo, to_ms=hi,
                         value=expect + 1))
    assert ok.status == "verified" and ok.value == expect and ok.evidence_ids
    assert bad.status == "failed" and "count is" in bad.reason


def test_rate_pass_and_fail(world):
    lo, hi = P2 + 960_000, P2 + 1_260_000
    evs = [e for e in world.match.events if e.type == "pass" and e.team_id == "IRV"
           and lo <= ms(e) <= hi]
    pct = 100 * sum(1 for e in evs if e.outcome == "complete") / len(evs)
    ok = check(world, C("c1", "rate", metric="pass_completion", team_id="IRV", from_ms=lo,
                        to_ms=hi, value_pct=pct + 1.5))
    bad = check(world, C("c2", "rate", metric="pass_completion", team_id="IRV", from_ms=lo,
                         to_ms=hi, value_pct=pct + 10))
    assert ok.status == "verified" and bad.status == "failed"


def test_chain_pass_and_fail(world):
    poss = world.shot.possession_id
    ok = check(world, C("c1", "chain", possession_id=poss, passes=11,
                        ended_with_event_id=world.goal.event_id))
    assert ok.status == "verified" and ok.value["passes"] == 11
    bad = check(world, C("c2", "chain", possession_id=poss, passes=10))
    assert bad.status == "failed"
    wrong_end = check(world, C("c3", "chain", possession_id=poss, passes=11,
                               ended_with_event_id=world.shot.event_id))
    assert wrong_end.status == "failed"


def test_gap_seconds_pass_and_fail(world):
    rec = world.match.ground_truth["beats"]["recovery_event_id"]
    secs = (ms(world.goal) - ms(next(e for e in world.match.events if e.event_id == rec))) / 1000
    ok = check(world, C("c1", "gap_seconds", from_event_id=rec, to_event_id=world.goal.event_id,
                        seconds=round(secs)))
    bad = check(world, C("c2", "gap_seconds", from_event_id=rec,
                         to_event_id=world.goal.event_id, seconds=secs + 5))
    rev = check(world, C("c3", "gap_seconds", from_event_id=world.goal.event_id,
                         to_event_id=rec, seconds=secs))
    assert ok.status == "verified" and bad.status == "failed" and rev.status == "failed"


def test_physical_pass_and_fail(world):
    lo, hi = P2 + 1_200_000, P2 + 1_300_000
    true = PhysicalEngine(world.match.frames, PERIODS).top_speed_kmh("IRV-10", lo, hi).value
    ok = check(world, C("c1", "physical", player_id="IRV-10", metric="top_speed_kmh",
                        from_ms=lo, to_ms=hi, value=true + 0.5))
    bad = check(world, C("c2", "physical", player_id="IRV-10", metric="top_speed_kmh",
                         from_ms=lo, to_ms=hi, value=true + 3))
    assert ok.status == "verified" and bad.status == "failed"
    dist = PhysicalEngine(world.match.frames, PERIODS).distance_m("IRV-10", lo, hi).value
    okd = check(world, C("c3", "physical", player_id="IRV-10", metric="distance_m",
                         from_ms=lo, to_ms=hi, value=dist * 1.04))
    badd = check(world, C("c4", "physical", player_id="IRV-10", metric="distance_m",
                          from_ms=lo, to_ms=hi, value=dist * 1.2))
    assert okd.status == "verified" and badd.status == "failed"


def test_compare_pass_and_fail(world):
    a, b = [P2 + 600_000, P2 + 780_000], [P2 + 1_000_000, P2 + 1_180_000]

    def passes(w):
        return sum(1 for e in world.match.events if e.type == "pass" and e.team_id == "IRV"
                   and w[0] <= ms(e) <= w[1])
    d = passes(b) - passes(a)
    assert d > 0
    ok = check(world, C("c1", "compare", metric="passes", team_id="IRV", window_a=a, window_b=b,
                        direction="up", min_delta=max(1, d - 1)))
    bad_dir = check(world, C("c2", "compare", metric="passes", team_id="IRV", window_a=a,
                             window_b=b, direction="down", min_delta=1))
    too_big = check(world, C("c3", "compare", metric="passes", team_id="IRV", window_a=a,
                             window_b=b, direction="up", min_delta=d + 5))
    assert ok.status == "verified" and ok.value["delta"] == d
    assert bad_dir.status == "failed" and too_big.status == "failed"


def test_phase_pass_and_fail(world):
    from secondlook.stats.phases import phase_label
    lo, hi = P2 + 1_000_000, P2 + 1_180_000
    label = phase_label(world.checker.stats_at(world.now), "IRV", lo, hi)
    other = next(x for x in ("control", "chaos", "even") if x != label)
    ok = check(world, C("c1", "phase", team_id="IRV", from_ms=lo, to_ms=hi, label=label))
    bad = check(world, C("c2", "phase", team_id="IRV", from_ms=lo, to_ms=hi, label=other))
    assert ok.status == "verified" and bad.status == "failed"


def test_score_pass_and_fail(world):
    after, before = ms(world.goal) + 1000, ms(world.goal) - 1000
    ok = check(world, C("c1", "score", at_ms=after, home=1, away=1))
    bad = check(world, C("c2", "score", at_ms=after, home=1, away=0))
    early = check(world, C("c3", "score", at_ms=before, home=1, away=0))
    assert ok.status == "verified" and early.status == "verified" and bad.status == "failed"
    assert ok.value == {"home": 1, "away": 1}


def test_involved_pass_and_fail(world):
    ok = check(world, C("c1", "involved", player_id="IRV-10",
                        event_ids=[world.shot.event_id, world.goal.event_id]))
    bad = check(world, C("c2", "involved", player_id="IRV-09",
                         event_ids=[world.shot.event_id]))
    assert ok.status == "verified" and bad.status == "failed"


def test_fail_closed_cases(world):
    lo, hi = P2 + 960_000, P2 + 1_260_000
    # window past now
    past = check(world, C("c1", "count", metric="passes", team_id="IRV", from_ms=lo, to_ms=hi,
                          value=0), now=lo + 1000)
    assert past.status == "unknown"
    # unknown claim type (bypass the Literal)
    weird = Claim.model_construct(claim_id="c2", type="telepathy", params={}, value=1)
    assert world.checker.verify(weird, world.now).status == "unknown"
    # bad params -> error -> unknown, never verified
    broken = check(world, C("c3", "count", metric="passes", team_id="IRV", value=1))
    assert broken.status == "unknown" and "error" in broken.reason
    # unknown metric
    bad_metric = check(world, C("c4", "count", metric="vibes", team_id="IRV", from_ms=lo,
                                to_ms=hi, value=1))
    assert bad_metric.status == "unknown"
    # event after now
    ev_after = check(world, C("c5", "event_fact", event_id=world.goal.event_id, field="type",
                              value="goal"), now=ms(world.goal) - 5000)
    assert ev_after.status == "unknown"
    # no frames -> physical unknown
    from secondlook.checker.verify import Checker
    nof = Checker(world.store, PERIODS, None).verify(
        C("c6", "physical", player_id="IRV-10", metric="sprints", from_ms=lo, to_ms=hi, value=0),
        world.now)
    assert nof.status == "unknown"
    # missing evidence is never zero: rate over an empty window is unknown, not 0 %
    empty = check(world, C("c7", "rate", metric="pass_completion", team_id="IRV", from_ms=0,
                           to_ms=10, value_pct=0))
    assert empty.status == "unknown"


def test_open_gap_makes_window_claims_unknown():
    w0 = World()
    mid = next(e for e in w0.match.events if ms(e) > P2 + 1_000_000 and e.type == "pass")
    store, ing, last = build(w0.match, skip_seqs={mid.seq})
    ing.tick(last + 60_000)
    assert store.gaps(open_only=True)
    from secondlook.checker.verify import Checker
    ck = Checker(store, PERIODS, w0.match.frames)
    lo, hi = P2 + 960_000, P2 + 1_100_000
    rec = ck.verify(C("c1", "count", metric="passes", team_id="IRV", from_ms=lo, to_ms=hi,
                      value=3), last)
    assert rec.status == "unknown" and "gap" in rec.reason
    # a window clear of the gap is still checkable
    lo2, hi2 = P2 + 600_000, P2 + 700_000
    exp = sum(1 for e in w0.match.events if e.type == "pass" and e.team_id == "IRV"
              and lo2 <= ms(e) <= hi2)
    rec2 = ck.verify(C("c2", "count", metric="passes", team_id="IRV", from_ms=lo2, to_ms=hi2,
                       value=exp), last)
    assert rec2.status == "verified"


def test_deps_are_recorded(fresh_world):
    w = fresh_world
    lo, hi = P2 + 1_200_000, P2 + 1_300_000
    rec = check(w, C("cnt", "count", metric="passes", team_id="IRV", from_ms=lo, to_ms=hi,
                     value=sum(1 for e in w.match.events if e.type == "pass" and
                               e.team_id == "IRV" and lo <= ms(e) <= hi)))
    assert rec.status == "verified"
    eid = rec.evidence_ids[0]
    assert "cnt" in w.store.claims_touching([eid], [])
    # a late pass by IRV inside the window touches it; by KBY does not; outside does not
    assert "cnt" in w.store.claims_touching([], [("IRV", "pass", lo + 5000)])
    assert "cnt" not in w.store.claims_touching([], [("KBY", "pass", lo + 5000)])
    assert "cnt" not in w.store.claims_touching([], [("IRV", "pass", hi + 5000)])
    assert "cnt" not in w.store.claims_touching([], [("IRV", "shot", lo + 5000)])
    sc = check(w, C("sc", "score", at_ms=ms(w.goal) + 1000, home=1, away=1))
    assert sc.status == "verified"
    assert "sc" in w.store.claims_touching([], [("IRV", "goal", ms(w.goal))])
    assert w.store.get("claim", "cnt").status == "verified"


def test_voided_goal_fails_the_score_claim_on_reverify(corrected_world):
    w = corrected_world
    claim = C("sc", "score", at_ms=ms(w.goal) + 1000, home=1, away=1)
    # the correction arrives 25 s after the goal; before it, the claim held
    store, ing, _ = build(w.match, until_seq=w.goal.seq)
    from secondlook.checker.verify import Checker
    before = Checker(store, PERIODS).verify(claim, ms(w.goal) + 1000)
    assert before.status == "verified"
    after = w.checker.verify(claim, w.now)
    assert after.status == "failed" and after.value == {"home": 1, "away": 0}
