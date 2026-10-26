"""Run the three systems over the held-out sequences and compute per-sequence rows."""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from harness.metrics import SYSTEMS, Row
from harness.sequences import SeqSpec, build_specs
from secondlook import config
from secondlook.agents import fake_model
from secondlook.agents.no_editor import run_no_editor
from secondlook.agents.single_agent import run_single
from secondlook.agents.workflow import run_moment
from secondlook.contracts.agents_io import Moment
from secondlook.publish.bus import Bus
from secondlook.runner import RunConfig, Runner

DELAY_S = 1.0
INJECTED_TIMEOUT_S = 0.25


class TimedBus(Bus):
    def __init__(self):
        super().__init__()
        self.stamps: list[tuple[float, str, dict]] = []

    def publish(self, type_, payload, profile_id=None):
        if type_ in ("overlay", "event"):
            self.stamps.append((time.time(), type_, payload))
        super().publish(type_, payload, profile_id)


def _delayed(fn):
    async def inner(req):
        await asyncio.sleep(DELAY_S)
        return fn(req)
    return inner


def responders_for(system: str, spec: SeqSpec, base: dict | None) -> dict:
    r = dict(base or fake_model.responders())
    if spec.group == "timeout":
        # the matching part of each system's job is delayed past its deadline
        key = "single" if system == "single" else "producer-fan-es"
        r[key] = _delayed(r[key])
    return r


async def run_one(system: str, spec: SeqSpec, provider: str = "fake",
                  base_responders: dict | None = None) -> Row:
    match = spec.build()
    truth = match.ground_truth["beats"]["seq"]
    bus = TimedBus()
    state = tempfile.mkdtemp(prefix="sl-h-")
    cfg = RunConfig(speed="max", llm=provider, sync_agents=True, state_dir=state)
    old = (config.TIMEOUT_PRODUCER, config.TIMEOUT_SINGLE)
    if spec.group == "timeout":
        config.TIMEOUT_PRODUCER = config.TIMEOUT_SINGLE = INJECTED_TIMEOUT_S
    try:
        runner = Runner(cfg, bus, match=match,
                        responders=responders_for(system, spec, base_responders))
        svc = runner.services

        async def handle(rn: Runner, m: Moment) -> None:
            if m.kind != "goal":
                return
            tok = rn.tools.by_moment[m.moment_id]
            if system == "multi":
                await run_moment(svc, m, tok, Path(state) / "cp" / m.moment_id)
            elif system == "single":
                await run_single(svc, m, tok)
            else:
                await run_no_editor(svc, m, tok)
        runner.handlers = [handle]
        await runner.run()
    finally:
        config.TIMEOUT_PRODUCER, config.TIMEOUT_SINGLE = old
    return row_from(system, spec, truth["true_tag"], runner, bus)


def row_from(system: str, spec: SeqSpec, true_tag: str, runner: Runner, bus: TimedBus) -> Row:
    st = runner.store
    moments = [m for m in st.list_kind("moment") if m.kind == "goal"]
    row = Row(system=system, seq_id=spec.seq_id, group=spec.group, true_tag=true_tag)
    if not moments:
        return row
    m = moments[0]
    sid = "st-" + m.moment_id.removeprefix("mo-")
    claims = [c for c in st.list_kind("claim") if c.owner_kind == "candidate"
              and c.claim_id.startswith(m.moment_id)]
    row.proposed_claims = len(claims)
    row.failed_claims = sum(1 for c in claims if c.status in ("failed", "unknown"))
    s1 = st.get("story", f"{sid}:1")
    ovs = [o for o in st.list_kind("overlay", story_id=sid, kind="explanation") if o.revision == 1]
    row.explained_profiles = sorted({o.profile_id for o in ovs})
    if s1 is not None and ovs:
        row.published_tag = s1.thesis_tag
    lats = [o.source.latency_ms for o in ovs if o.source.latency_ms is not None]
    row.latency_ms = min(lats) if lats else None
    calls = st.calls(m.moment_id)
    row.calls = len(calls)
    row.tokens = sum((c["tokens_in"] or 0) + (c["tokens_out"] or 0) for c in calls)
    if spec.group == "correction":
        t_corr = next((t for t, ty, p in bus.stamps if ty == "event" and p["type"] ==
                       "event_correction"), None)
        ok = {}
        for t, ty, p in bus.stamps:
            if ty == "overlay" and p["kind"] in ("correction_notice", "explanation") and t_corr \
                    and t >= t_corr and (p["kind"] == "correction_notice" or p["revision"] > 1):
                ok.setdefault(p["profile_id"], t - t_corr)
        row.correction_both_5s = len(ok) == 2 and all(v <= 5 for v in ok.values())
    if spec.group == "timeout":
        row.fault_profile = "fan-es"
        row.survived_fault = "analyst-en" in row.explained_profiles
    return row


async def run_all(provider: str = "fake", limit: int | None = None,
                  systems: tuple[str, ...] = SYSTEMS, specs: list[SeqSpec] | None = None
                  ) -> tuple[list[Row], list[str]]:
    specs = specs or build_specs()
    if limit:
        specs = [s for i, s in enumerate(specs) if i % max(1, len(specs) // limit) == 0][:limit]
    rows = []
    for spec in specs:
        for system in systems:
            rows.append(await run_one(system, spec, provider))
    return rows, [s.seq_id for s in specs]
