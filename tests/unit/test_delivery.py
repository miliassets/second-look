import asyncio
from collections import Counter

from secondlook.contracts.events import Event
from secondlook.generator import generate_slice
from secondlook.generator.corrections import CorrectionSpec
from secondlook.generator.delivery import PRESETS, FaultConfig, apply_faults
from secondlook.generator.engine import PERIODS
from secondlook.generator.player import merge, play
from secondlook.timeline import to_match_ms

SEED = 20261024


def fake_events(n):
    return [Event(event_id=f"x{i}", match_id="m", seq=i + 1, type="pass", period=2,
                  match_clock_ms=600_000 + i * 1000) for i in range(n)]


def test_fault_rates_honoured():
    cfg = FaultConfig(late_rate=0.10, dup_rate=0.05, gap_rate=0.02)
    d = apply_faults(fake_events(20_000), PERIODS, cfg, seed=7)
    c = Counter(x.kind for x in d)
    n = 20_000
    assert abs(c["duplicate"] / n - 0.05) < 0.01
    assert abs((c["late"]) / n - 0.12) < 0.012
    assert len(d) == n + c["duplicate"]


def test_faults_are_deterministic_and_none_is_identity():
    ev = fake_events(500)
    a = apply_faults(ev, PERIODS, PRESETS["demo"], 3)
    b = apply_faults(ev, PERIODS, PRESETS["demo"], 3)
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]
    plain = apply_faults(ev, PERIODS, PRESETS["none"], 3)
    assert [x.event.seq for x in plain] == list(range(1, 501))
    assert {x.kind for x in plain} == {"normal"}


def test_late_and_gapped_arrive_after_later_seqs():
    ev = fake_events(2000)
    d = apply_faults(ev, PERIODS, FaultConfig(late_rate=0.2), 5)
    late = [x for x in d if x.kind == "late"]
    assert late
    for x in late:
        base = to_match_ms(x.event.period, x.event.match_clock_ms, PERIODS)
        assert 1000 <= x.arrival_match_ms - base < 4000
    # at least one late event is overtaken by a higher seq
    seen_max = 0
    overtaken = 0
    for x in d:
        if x.event.seq < seen_max:
            overtaken += 1
        seen_max = max(seen_max, x.event.seq)
    assert overtaken > 0
    assert [x.arrival_order for x in d] == list(range(len(d)))


def test_duplicates_are_identical_events():
    d = apply_faults(fake_events(1000), PERIODS, FaultConfig(dup_rate=0.2), 1)
    firsts = {}
    for x in d:
        if x.kind == "duplicate":
            assert firsts[x.event.event_id] == x.event
        else:
            firsts[x.event.event_id] = x.event


def test_void_goal_correction_references_real_event():
    m = generate_slice(SEED, corrections=[CorrectionSpec("void_goal")])
    ids = {e.event_id for e in m.events}
    corr = [e for e in m.events if e.type == "event_correction"]
    assert len(corr) == 1
    c = corr[0]
    target = next(e for e in m.events if e.event_id == c.qualifiers["corrects"])
    assert target.type == "goal" and c.related_ids == [target.event_id]
    assert c.seq > target.seq and c.match_clock_ms > target.match_clock_ms
    assert c.qualifiers["kind"] == "void" and c.qualifiers["replacement"] is None
    assert [e.seq for e in m.events] == list(range(1, len(m.events) + 1))
    assert len(ids) == len(m.events)


def test_reattribute_correction_has_valid_replacement():
    m = generate_slice(SEED, corrections=[CorrectionSpec("reattribute", new_player_id="IRV-06")])
    c = next(e for e in m.events if e.type == "event_correction")
    repl = Event.model_validate(c.qualifiers["replacement"])
    old = next(e for e in m.events if e.event_id == c.qualifiers["corrects"])
    assert old.player_id == "IRV-08" and repl.player_id == "IRV-06"
    assert repl.event_id != old.event_id and repl.qualifiers["corrects"] == old.event_id
    assert repl.possession_id == old.possession_id


def test_max_speed_emits_in_arrival_order_without_sleeping():
    m = generate_slice(SEED)
    d = apply_faults(m.events, PERIODS, PRESETS["demo"], SEED)
    items = merge(d, m.frames, PERIODS)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    async def run(speed):
        return [it async for it in play(items, speed, sleep=fake_sleep)]

    out = asyncio.run(run("max"))
    assert slept == []
    ev = [it.delivery.arrival_order for it in out if it.kind == "event"]
    assert ev == sorted(ev)
    times = [it.at_ms for it in out]
    assert times == sorted(times)


def test_speed_scales_sleep():
    items = merge([], generate_slice(SEED).frames[:5], PERIODS)  # 0.5 s apart
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    async def run(speed):
        return [it async for it in play(items, speed, sleep=fake_sleep)]

    asyncio.run(run("10x"))
    assert len(slept) == 4 and all(abs(s - 0.05) < 1e-9 for s in slept)
