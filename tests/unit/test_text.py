import pytest

from secondlook.checker.text import check_text
from secondlook.contracts.claims import Claim
from secondlook.generator.engine import PERIODS
from tests.conftest import P2, ms


@pytest.fixture(scope="module")
def claims(world):
    poss = world.shot.possession_id
    evs = [e for e in world.match.events if e.type == "pass" and e.team_id == "IRV"
           and P2 + 1_000_000 <= ms(e) <= P2 + 1_260_000]
    pct = 100 * sum(1 for e in evs if e.outcome == "complete") / len(evs)
    cs = [
        Claim(claim_id="c1", type="chain", params={"possession_id": poss, "passes": 11}),
        Claim(claim_id="c2", type="involved", params={
            "player_id": "IRV-10", "event_ids": [world.shot.event_id, world.goal.event_id]}),
        Claim(claim_id="c3", type="score", params={"at_ms": ms(world.goal) + 1000, "home": 1,
                                                   "away": 1}),
        Claim(claim_id="c4", type="event_fact", params={
            "event_id": world.goal.event_id, "field": "team_id", "value": "IRV"}),
        Claim(claim_id="c5", type="rate", params={
            "metric": "pass_completion", "team_id": "IRV", "from_ms": P2 + 1_000_000,
            "to_ms": P2 + 1_260_000, "value_pct": pct}),
        Claim(claim_id="bad", type="score", params={"at_ms": ms(world.goal) + 1000, "home": 2,
                                                    "away": 1}),
    ]
    return {c.claim_id: world.checker.verify(c, world.now) for c in cs}


def run(world, claims, tpl, ids=("c1", "c2", "c3", "c4"), lang="en", mode="analyst"):
    return check_text(tpl, [claims[i] for i in ids], world.roster, lang, mode,
                      store=world.store, periods=PERIODS)


def test_good_template_renders_verified_values(world, claims):
    r = run(world, claims, "{p:IRV-10} scored after a move of {c1} passes. Score {c3}.")
    assert r.ok, r.errors
    assert r.text == "Vask scored after a move of 11 passes. Score 1–1."
    assert r.spans[0].ref == "p:IRV-10" and r.text[r.spans[0].start:r.spans[0].end] == "Vask"
    assert set(r.claims_used) == {"c1", "c3"}


def test_time_and_team_placeholders(world, claims):
    r = run(world, claims, "{t:IRV} scored at {time:" + world.goal.event_id + "}.")
    assert r.ok, r.errors
    assert r.text.startswith("Ironvale scored at 66:5") or r.text.startswith("Ironvale scored at 67:")


def test_spanish_number_format_and_names(world, claims):
    r = run(world, claims, "Tras {c1} pases, {p:IRV-10} marcó. Con {c5}% de acierto.", lang="es",
            mode="fan", ids=("c1", "c2", "c5"))
    assert r.ok, r.errors
    assert "11 pases" in r.text and "Vask" in r.text
    import re
    assert re.search(r"Con \d+(,\d)?% de acierto", r.text)


def test_raw_digit_rejected(world, claims):
    r = run(world, claims, "Vask scored after 11 passes.")
    assert not r.ok and any("digit" in e for e in r.errors)


def test_untagged_name_rejected(world, claims):
    for tpl in ("Vask scored after {c1} passes.", "The move involved Teodor Vask and {c1} passes.",
                "ironvale kept the ball for {c1} passes"):
        r = run(world, claims, tpl)
        assert not r.ok and any("plain text" in e for e in r.errors), tpl


def test_unlisted_capitalised_word_rejected(world, claims):
    r = run(world, claims, "A Wonderful move of {c1} passes.")
    assert not r.ok and any("Wonderful" in e for e in r.errors)


def test_causal_words_rejected_in_both_languages(world, claims):
    r = run(world, claims, "Ironvale scored because of {c1} passes.")
    assert not r.ok and any("causal" in e for e in r.errors)
    r2 = run(world, claims, "The goal was due to a move of {c1} passes.")
    assert not r2.ok
    r3 = run(world, claims, "Gol porque hubo {c1} pases", lang="es", mode="fan")
    assert not r3.ok and any("causal" in e for e in r3.errors)
    ok = run(world, claims, "Then, after a move of {c1} passes, the goal came.")
    assert ok.ok, ok.errors  # sequence words are fine


def test_unverified_claim_blocks_the_output(world, claims):
    r = run(world, claims, "Score {bad}.", ids=("c1", "bad"))
    assert not r.ok and any("bad is failed" in e for e in r.errors)


def test_placeholder_must_reference_a_claim_of_the_output(world, claims):
    r = run(world, claims, "Move of {c5} percent.", ids=("c1",))
    assert not r.ok and any("does not reference" in e for e in r.errors)
    r2 = run(world, claims, "Move of {nonsense} passes.")
    assert not r2.ok


def test_player_must_be_backed_by_a_claim(world, claims):
    r = run(world, claims, "{p:KBY-09} scored.", ids=("c1", "c2"))
    assert not r.ok and any("not backed" in e for e in r.errors)
    r2 = run(world, claims, "{p:ZZZ-99} scored.")
    assert not r2.ok and any("unknown player" in e for e in r2.errors)


def test_length_limits(world, claims):
    long_tpl = "The move of {c1} passes " + "kept going and going " * 12
    assert not run(world, claims, long_tpl).ok
    r = run(world, claims, "Goal. " + "The ball moved on and on " * 6 + "{c1}.", mode="fan")
    assert not r.ok and any("too long" in e for e in r.errors)


def test_markup_and_braces_rejected(world, claims):
    assert not run(world, claims, "<b>{c1}</b> passes").ok
    assert not run(world, claims, "Move of {c1 passes").ok
