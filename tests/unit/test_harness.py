import pytest

from harness.metrics import SYSTEMS, Row, bootstrap_ci, compute, summarize
from harness.report import IncompleteRun, check_complete, markdown, write_report
from harness.run import run_one
from harness.sequences import build_specs


def row(system="multi", seq="s01", group="clean", true="pressing_recovery", pub="pressing_recovery",
        proposed=4, failed=0, prof=("analyst-en", "fan-es"), lat=1000, tokens=500, corr=None,
        surv=None):
    return Row(system, seq, group, true, pub, proposed, failed, list(prof), lat, tokens, 3, corr,
               surv)


def test_metrics_on_a_hand_made_results_file():
    rows = [
        row(seq="a", proposed=10, failed=2, lat=1000, tokens=400),
        row(seq="b", pub="sustained_possession", proposed=10, failed=0, lat=2000, tokens=600),
        row(seq="c", pub=None, prof=(), proposed=5, failed=5, lat=None, tokens=100),  # fallback
        row(seq="d", group="correction", corr=True, lat=3000),
        row(seq="e", group="correction", corr=False, lat=9000),
        row(seq="f", group="timeout", prof=("analyst-en",), surv=True, lat=4000),
        row(seq="g", group="timeout", prof=(), pub=None, surv=False, lat=None),
    ]
    m = compute(rows)
    proposed = 10 + 10 + 5 + 4 * 4  # seq d,e,f,g propose 4 each
    assert m["unsupported_claims_per_100"] == pytest.approx(100 * (2 + 0 + 5) / proposed)
    # accuracy: a, d, e, f correct (4 of 7); b wrong; c and g unpublished
    assert m["published_accuracy_pct"] == pytest.approx(100 * 4 / 7)
    assert m["fallback_rate_pct"] == pytest.approx(100 * 2 / 7)
    assert m["corrections_both_5s_pct"] == 50.0
    assert m["survival_under_fault_pct"] == 50.0
    assert m["p95_latency_ms"] == 9000  # of [1000, 2000, 3000, 4000, 9000]
    completed = [r for r in rows if r.explained_profiles]
    assert m["tokens_per_completed_moment"] == pytest.approx(sum(r.tokens for r in rows)
                                                             / len(completed))
    assert m["n"] == 7.0


def test_metrics_handle_empty_denominators():
    m = compute([row(proposed=0, group="clean")])
    assert m["unsupported_claims_per_100"] is None
    assert m["corrections_both_5s_pct"] is None and m["survival_under_fault_pct"] is None


def test_bootstrap_interval_brackets_the_estimate_and_is_deterministic():
    rows = [row(seq=str(i), pub="pressing_recovery" if i % 4 else "other") for i in range(40)]
    v = compute(rows)["published_accuracy_pct"]
    lo, hi = bootstrap_ci(rows, "published_accuracy_pct")
    assert lo <= v <= hi and 0 < hi - lo < 40
    assert bootstrap_ci(rows, "published_accuracy_pct") == (lo, hi)
    all_same = [row(seq=str(i)) for i in range(10)]
    assert bootstrap_ci(all_same, "published_accuracy_pct") == (100.0, 100.0)


def test_harness_refuses_to_write_a_report_if_any_sequence_is_missing(tmp_path):
    rows = [row(system=s, seq=q) for s in SYSTEMS for q in ("a", "b")]
    check_complete(rows, ["a", "b"])
    with pytest.raises(IncompleteRun, match="never reported as complete"):
        check_complete(rows[:-1], ["a", "b"])  # no_editor/b is missing
    meta = {"date": "2026-10-14", "git_sha": "abc", "provider": "fake", "model": "fake",
            "n_sequences": 2}
    with pytest.raises(IncompleteRun):
        write_report(rows[:-1], ["a", "b"], meta, tmp_path)
    assert list(tmp_path.iterdir()) == []  # nothing written
    js, md = write_report(rows, ["a", "b"], meta, tmp_path)
    assert "PARTIAL RUN (2 of 30" in md.read_text()  # fewer than 30 sequences is flagged
    assert "NOT a model comparison" in md.read_text()  # fake provider is flagged
    assert js.name.endswith("-partial.json")


def test_markdown_table_shape():
    rows = {s: [row(system=s, seq=str(i)) for i in range(12)] for s in SYSTEMS}
    md = markdown(summarize(rows), {"date": "d", "git_sha": "x", "provider": "github",
                                    "model": "m", "n_sequences": 30})
    assert "NOT a model comparison" not in md
    assert "| Metric | multi | single | no_editor |" in md
    assert md.count("| ") > 20


def test_sequence_set_matches_the_design():
    specs = build_specs()
    from collections import Counter
    assert len(specs) == 30 and len({s.seq_id for s in specs}) == 30
    assert Counter(s.group for s in specs) == {"clean": 10, "misleading": 10, "correction": 5,
                                                "timeout": 5}
    assert min(s.seed for s in specs) > 7_000_000  # held out: never a demo or fixture seed
    assert all(10_000 <= s.correction_delay_ms <= 40_000 for s in specs if s.group == "correction")
    assert {s.family for s in specs if s.group == "misleading"} == {
        "misleading_press", "misleading_counter"}


def test_sequence_generation_has_ground_truth_and_misleading_possession():
    from secondlook.ledger.store import StoredEvent
    from secondlook.stats.engine import StatsEngine
    from secondlook.timeline import to_match_ms
    specs = {s.group: s for s in build_specs()}
    for grp in ("clean", "misleading", "correction"):
        m = specs[grp].build()
        t = m.ground_truth["beats"]["seq"]
        assert t["true_tag"] and t["event_id"]
        goals = [e for e in m.events if e.type == "goal"]
        assert len(goals) == 1 and 140_000 < goals[0].match_clock_ms < 190_000
        if grp == "correction":
            corr = [e for e in m.events if e.type == "event_correction"][0]
            assert 10_000 <= corr.match_clock_ms - goals[0].match_clock_ms <= 40_000
        if grp == "misleading":
            assert t["true_tag"] != "sustained_possession"
            periods = m.metadata.periods
            st = StatsEngine([StoredEvent(event=e, match_ms=to_match_ms(
                e.period, e.match_clock_ms, periods)) for e in m.events])
            other = "KBY" if t["scorer_team"] == "IRV" else "IRV"
            assert st.rate("possession_share", other, 0, 140_000).value > 58  # the other side


@pytest.mark.parametrize("group,idx", [("clean", 0), ("correction", 20), ("timeout", 25)])
async def test_one_sequence_through_all_three_systems(group, idx):
    spec = build_specs()[idx]
    assert spec.group == group
    rows = {s: await run_one(s, spec) for s in SYSTEMS}
    for r in rows.values():
        assert r.seq_id == spec.seq_id and r.true_tag and r.calls > 0
    if group == "clean":
        assert all(r.published_tag == r.true_tag and r.explained_profiles == [
            "analyst-en", "fan-es"] for r in rows.values())
        assert rows["multi"].proposed_claims > rows["single"].proposed_claims  # competing scouts
    if group == "timeout":
        # multi and no_editor: the analyst survives a fan-es delay; single loses both
        assert rows["multi"].survived_fault and rows["no_editor"].survived_fault
        assert rows["single"].survived_fault is False and rows["single"].explained_profiles == []
    if group == "correction":
        assert rows["multi"].correction_both_5s is True


async def test_run_all_dev_subset_is_complete_for_its_own_sequences():
    from harness.run import run_all
    rows, ids = await run_all("fake", limit=3)
    assert len(ids) == 3 and len(rows) == 9
    check_complete(rows, ids)
