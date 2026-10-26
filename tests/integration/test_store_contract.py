"""LedgerStore contract suite. Runs on SQLite always; on Cosmos when COSMOS_ENDPOINT is set."""
import os

import pytest

from secondlook.contracts.agents_io import Candidate, Moment, Story
from secondlook.contracts.claims import ClaimRecord
from secondlook.contracts.events import Event
from secondlook.contracts.overlay import Overlay
from secondlook.ledger.sqlite_store import SQLiteStore
from secondlook.ledger.store import StoredEvent

STORES = ["sqlite"] + (["cosmos"] if os.environ.get("COSMOS_ENDPOINT") else [])


@pytest.fixture(params=STORES)
def store(request):
    if request.param == "sqlite":
        return SQLiteStore(":memory:", "m001")
    pytest.skip("cosmos store lands in S23")


def ev(i, ms, type_="pass", team="IRV", player="IRV-08"):
    return StoredEvent(event=Event(event_id=f"e{i}", match_id="m001", seq=i, type=type_, period=2,
                                   match_clock_ms=ms, team_id=team, player_id=player),
                       match_ms=ms, rev=0)


def test_revisions_increment(store):
    assert store.current_rev() == 0
    assert store.next_rev("append", "e1", 10) == 1
    assert store.next_rev("late", "e2", 20) == 2
    assert [r.kind for r in store.revisions()] == ["append", "late"]
    assert store.current_rev() == 2


def test_events_query_filters_and_order(store):
    for i, (ms, t, tm) in enumerate([(300, "pass", "IRV"), (100, "shot", "KBY"),
                                     (200, "pass", "KBY")], start=1):
        store.append_event(ev(i, ms, t, tm))
    assert [s.event.event_id for s in store.get_events()] == ["e2", "e3", "e1"]
    assert [s.event.event_id for s in store.get_events(from_ms=150, to_ms=250)] == ["e3"]
    assert [s.event.event_id for s in store.get_events(types=["pass"], team_id="KBY")] == ["e3"]
    assert store.has_event("e1") and not store.has_event("zz")


def test_as_of_rev_hides_later_events_and_status(store):
    a = ev(1, 100)
    a.rev = 1
    b = ev(2, 200)
    b.rev = 2
    store.append_event(a)
    store.append_event(b)
    store.apply_correction("e1", "void", "x1", rev=3)
    assert store.get_events(as_of_rev=2) and len(store.get_events(as_of_rev=2)) == 2
    assert [s.event.event_id for s in store.get_events(as_of_rev=1)] == ["e1"]
    assert [s.event.event_id for s in store.get_events()] == ["e2"]  # e1 void now
    assert store.get_event("e1").status == "void"
    assert store.get_event("e1", as_of_rev=2).status == "active"
    voided = store.get_events(statuses=("void",))
    assert [s.event.event_id for s in voided] == ["e1"]


def test_derived_rows_put_get_status(store):
    m = Moment(moment_id="mo1", kind="goal", window_from_ms=0, window_to_ms=10, now_ms=10,
               as_of_rev=3)
    store.put("moment", m)
    assert store.get("moment", "mo1") == m
    store.set_status("moment", "mo1", "published")
    assert store.get("moment", "mo1").status == "published"
    c = ClaimRecord(claim_id="c1", type="count", status="verified", checked_rev=2)
    store.put("claim", c)
    store.set_status("claim", "c1", "invalidated", reason="count was 3, now 2", checked_rev=5)
    got = store.get("claim", "c1")
    assert got.status == "invalidated" and got.reason == "count was 3, now 2"
    assert got.checked_rev == 5
    s = Story(story_id="st1", revision=2, moment_id="mo1", text_en="x")
    store.put("story", s)
    assert store.get("story", "st1:2").revision == 2
    cand = Candidate(candidate_id="k1", moment_id="mo1", scout="rhythm", thesis_tag="even_rhythm",
                     thesis="t")
    store.put("candidate", cand)
    assert [x.candidate_id for x in store.list_kind("candidate", moment_id="mo1")] == ["k1"]
    assert store.get("claim", "nope") is None


def test_deps_graph(store):
    store.add_deps([("claim", "c1", "candidate", "k1"), ("candidate", "k1", "story", "st1:1"),
                    ("claim", "c1", "story", "st1:1")])
    store.add_deps([("claim", "c1", "candidate", "k1")])  # idempotent
    assert sorted(store.children("claim", "c1")) == [("candidate", "k1"), ("story", "st1:1")]
    assert store.parents("story", "st1:1") == [("candidate", "k1"), ("claim", "c1")] or \
        sorted(store.parents("story", "st1:1")) == [("candidate", "k1"), ("claim", "c1")]


def test_claims_touching_event_and_window_deps(store):
    store.add_claim_event_deps("c1", ["e1", "e2"])
    store.add_claim_window_dep("c2", "IRV", ["pass"], 100, 200)
    store.add_claim_window_dep("c3", None, ["goal"], 0, 500)
    store.add_claim_window_dep("c4", "KBY", [], 100, 200)
    assert store.claims_touching(["e2"], []) == ["c1"]
    assert store.claims_touching([], [("IRV", "pass", 150)]) == ["c2"]
    assert store.claims_touching([], [("KBY", "pass", 150)]) == ["c4"]  # c2 is IRV-only
    assert store.claims_touching([], [("IRV", "pass", 250)]) == []
    assert store.claims_touching([], [("IRV", "goal", 400)]) == ["c3"]
    assert sorted(store.claims_touching(["e1"], [("IRV", "pass", 150)])) == ["c1", "c2"]


def test_gaps_and_stats(store):
    store.open_gap(5, 5, rev=3, at_ms=999)
    assert len(store.gaps(open_only=True)) == 1
    assert store.close_gap(5, rev=7) is True
    assert store.close_gap(5, rev=8) is False
    g = store.gaps()[0]
    assert (g.opened_rev, g.closed_rev) == (3, 7) and store.gaps(open_only=True) == []
    store.bump_stat("duplicates")
    store.bump_stat("duplicates", 2)
    assert store.stat("duplicates") == 3 and store.stat("nothing") == 0


def test_overlays_since_cursor(store):
    def o(i, profile):
        return Overlay(overlay_id=f"ov{i}", story_id="st", kind="factual",
                       viewer_mode="analyst" if profile == "analyst-en" else "fan",
                       profile_id=profile, lang="en", show_at_match_ms=i, duration_ms=1000)
    store.put("overlay", o(1, "analyst-en"))
    store.put("overlay", o(2, "fan-es"))
    store.put("overlay", o(3, "analyst-en"))
    got = store.overlays_since(0)
    assert [x.overlay_id for _, x in got] == ["ov1", "ov2", "ov3"]
    cur = got[1][0]
    assert [x.overlay_id for _, x in store.overlays_since(cur)] == ["ov3"]
    assert [x.overlay_id for _, x in store.overlays_since(0, "fan-es")] == ["ov2"]
    store.set_status("overlay", "ov1", "superseded")
    assert store.get_status("overlay", "ov1") == "superseded"
    store.put("overlay", o(1, "analyst-en"))  # re-put keeps its cursor
    assert [x.overlay_id for _, x in store.overlays_since(0)] == ["ov1", "ov2", "ov3"]
