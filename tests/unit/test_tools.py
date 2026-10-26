import pytest

from secondlook.contracts.agents_io import Moment
from secondlook.contracts.claims import Claim
from secondlook.generator.engine import PERIODS
from secondlook.matchstate.tools import MatchTools, ToolError
from tests.conftest import P2, World, build, ms


def goal_moment(w, rev=None):
    return Moment(moment_id="mo-m001-067-goal", kind="goal", trigger_event_id=w.goal.event_id,
                  window_from_ms=ms(w.goal) - 120_000, window_to_ms=ms(w.goal),
                  now_ms=ms(w.goal), as_of_rev=rev or w.goal.seq, team_id="IRV")


@pytest.fixture(scope="module")
def tw(world):
    tools = MatchTools(world.store, world.checker, PERIODS)
    tok = tools.issue_token(goal_moment(world))
    return world, tools, tok


def test_token_clamps_to_now_future_invisible(tw):
    w, tools, tok = tw
    r = tools.get_events(tok, 0, 10**9)
    assert r["events"] and max(e["ms"] for e in r["events"]) <= ms(w.goal)
    assert r["now_ms"] == ms(w.goal)
    later = next(e for e in w.match.events if ms(e) > ms(w.goal))
    with pytest.raises(ToolError):
        tools.get_event(tok, later.event_id)
    assert tools.get_score(tok, at_ms=10**9)["at_ms"] == ms(w.goal)
    s = tools.get_stat(tok, "passes", 0, 10**9, team_id="IRV")
    expect = sum(1 for e in w.match.events if e.type == "pass" and e.team_id == "IRV"
                 and ms(e) <= ms(w.goal))
    assert s["value"] == expect and s["to_ms"] == ms(w.goal)
    assert all(ms(next(x for x in w.match.events if x.event_id == i)) <= ms(w.goal)
               for i in s["evidence_ids"])


def test_unknown_token_and_limits(tw):
    w, tools, tok = tw
    with pytest.raises(ToolError):
        tools.get_events("tok-bogus", 0, 1)
    r = tools.get_events(tok, 0, 10**9, limit=5000)
    assert len(r["events"]) <= 200 and r["truncated"] is True
    r2 = tools.get_events(tok, 0, 10**9, types=["goal"])
    assert [e["event_id"] for e in r2["events"]] == [w.goal.event_id]
    r3 = tools.get_events(tok, 0, 10**9, team_id="KBY", player_id="KBY-06", types=["pass"])
    assert all(e["player_id"] == "KBY-06" for e in r3["events"])


def test_single_event_possession_and_score(tw):
    w, tools, tok = tw
    e = tools.get_event(tok, w.goal.event_id)["event"]
    assert e["type"] == "goal" and e["qualifiers"]["home_score"] == 1
    p = tools.get_possession(tok, w.shot.possession_id)
    assert p["passes"] == 11 and w.goal.event_id in p["evidence_ids"] and p["duration_ms"] > 20000
    sc = tools.get_score(tok)
    assert sc["home"] == {"team_id": "KBY", "goals": 1} and sc["away"]["goals"] == 1


def test_compare_physical_and_unknown_metric(tw):
    w, tools, tok = tw
    now = ms(w.goal)
    c = tools.compare_windows(tok, "passes", "IRV", [now - 240_000, now - 120_000],
                              [now - 120_000, now])
    assert c["delta"] == c["b"] - c["a"] and c["window_b"] == [now - 120_000, now]
    ph = tools.get_physical(tok, "IRV-10", now - 120_000, 10**9)
    assert ph["top_speed_kmh"] <= 36 and ph["distance_m"] > 0 and ph["to_ms"] == now
    with pytest.raises(ToolError):
        tools.get_stat(tok, "vibes", 0, 1)
    assert tools.get_stat(tok, "top_speed_kmh", now - 60_000, now, player_id="IRV-10")[
        "player_id"] == "IRV-10"


def test_dossier_facts_are_ready_claim_objects(tw):
    w, tools, tok = tw
    d = tools.get_dossier(tok)
    types = {f["type"] for f in d["facts"]}
    assert {"score", "phase", "count", "rate", "chain"} <= types
    assert d["moment"]["kind"] == "goal" and d["notable_players"]
    ids = [f["claim_id"] for f in d["facts"]]
    assert len(ids) == len(set(ids))
    now = ms(w.goal)
    for f in d["facts"]:  # copy-paste into the checker: all verify
        rec = w.checker.verify(Claim(claim_id=f["claim_id"], type=f["type"], params=f["params"]),
                               now, rev=w.goal.seq, record=False)
        assert rec.status == "verified", (f, rec.reason)
    chain = next(f for f in d["facts"] if f["type"] == "chain")
    assert chain["params"]["passes"] == 11
    assert d["gaps"] == [] and d["evidence_ids"]


def test_verify_claim_tool(tw):
    w, tools, tok = tw
    ok = tools.verify_claim(tok, {"claim_id": "x", "type": "score",
                                  "params": {"at_ms": ms(w.goal), "home": 1, "away": 1}})
    bad = tools.verify_claim(tok, {"claim_id": "y", "type": "score",
                                   "params": {"at_ms": ms(w.goal), "home": 2, "away": 1}})
    junk = tools.verify_claim(tok, {"nonsense": True})
    assert ok["status"] == "verified" and bad["status"] == "failed" and junk["status"] == "failed"


def test_as_of_rev_pins_the_view_before_a_correction(corrected_world):
    w = corrected_world
    tools = MatchTools(w.store, w.checker, PERIODS)
    tok = tools.issue_token(goal_moment(w, rev=w.goal.seq))
    assert tools.get_score(tok)["away"]["goals"] == 1  # the void arrives later
    assert tools.get_event(tok, w.goal.event_id)["event"]["status"] == "active"
    now_tok = tools.issue_token(Moment(
        moment_id="mo-late", kind="goal", trigger_event_id=None, window_from_ms=0,
        window_to_ms=w.now, now_ms=w.now, as_of_rev=w.store.current_rev()))
    assert tools.get_score(now_tok)["away"]["goals"] == 0


def test_open_gap_is_reported():
    w0 = World()
    mid = next(e for e in w0.match.events if ms(e) > P2 + 1_000_000 and e.type == "pass")
    store, ing, last = build(w0.match, skip_seqs={mid.seq})
    ing.tick(last + 60_000)
    from secondlook.checker.verify import Checker
    tools = MatchTools(store, Checker(store, PERIODS, w0.match.frames), PERIODS)
    tok = tools.issue_token(Moment(moment_id="mo-x", kind="goal", window_from_ms=0,
                                   window_to_ms=last, now_ms=last, as_of_rev=store.current_rev()))
    r = tools.get_events(tok, P2 + 900_000, P2 + 1_200_000)
    assert r["gaps"]
    far = tools.get_events(tok, P2 + 600_000, P2 + 700_000)
    assert far["gaps"] == []
