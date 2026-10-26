"""Microsoft Agent Framework workflow, one run per moment (design §3.4).

Dispatch -> [PressureScout, RhythmScout] (fan-out) -> Collect (fan-in) -> Editor.
A checkpoint is saved after every superstep (FileCheckpointStorage under .state/checkpoints/), so
a crashed run resumes without calling the scouts again. The Editor's single evidence round (one
extra scout call, then a second Editor call) runs inside the Editor executor.
All framework imports live in this file so API churn touches one place.
"""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import (
    Executor,
    FileCheckpointStorage,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from ..checker.roster import Roster
from ..checker.verify import Checker
from ..contracts.agents_io import Moment, Story
from ..contracts.claims import Claim, ClaimRecord
from ..contracts.prefs import DEFAULT_PROFILES, ViewerProfile
from ..ledger.store import LedgerStore
from ..matchstate.tools import MatchTools
from ..publish.assemble import assemble_overlay
from ..publish.publisher import Publisher
from .editor import edit_moment, persist
from .llm import ManagedLLM
from .producers import ProducerResult, run_producer
from .scouts import ScoutResult, run_scout

DEFAULT_SCOUTS = ["pressure", "rhythm", "player"]  # cut order: drop "player" first


@dataclass
class Services:
    llm: ManagedLLM
    tools: MatchTools
    checker: Checker
    store: LedgerStore
    roster: Roster
    periods: list[dict[str, int]]
    scouts: list[str] = field(default_factory=lambda: list(DEFAULT_SCOUTS))
    publisher: Publisher | None = None
    profiles: list[ViewerProfile] = field(default_factory=lambda: list(DEFAULT_PROFILES))
    clock_ms: Any = None  # callable -> current feed match_ms (when the explanation may air)
    on_stale: Any = None  # callable(moment, stale_claim_ids) when the publish-time re-check fails
    crash_in_editor: bool = False  # test hook: simulate a crash after the scouts


class Dispatch(Executor):
    def __init__(self):
        super().__init__(id="dispatch")

    @handler
    async def go(self, msg: dict, ctx: WorkflowContext[dict]) -> None:
        await ctx.send_message(msg)


class ScoutExecutor(Executor):
    def __init__(self, svc: Services, name: str):
        super().__init__(id=f"scout-{name}")
        self.svc, self.name = svc, name

    @handler
    async def go(self, msg: dict, ctx: WorkflowContext[dict]) -> None:
        m = Moment.model_validate(msg["moment"])
        extra = None
        if self.name == "player":
            extra = {"followed_players": sorted({p.followed_player_id for p in self.svc.profiles
                                                 if p.followed_player_id})}
        res = await run_scout(self.name, self.svc.llm, self.svc.tools, self.svc.checker,
                              self.svc.store, m, msg["token"], extra)
        await ctx.send_message({"moment": msg["moment"], "token": msg["token"],
                                "result": res.model_dump(mode="json")})


class ReopenStart(Executor):
    """Start of a reopened-moment workflow: the Editor runs first with surviving candidates."""

    def __init__(self):
        super().__init__(id="reopen-start")

    @handler
    async def go(self, msg: dict, ctx: WorkflowContext[dict]) -> None:
        await ctx.send_message(msg)


class Collect(Executor):
    def __init__(self):
        super().__init__(id="collect")

    @handler
    async def go(self, msgs: list[dict], ctx: WorkflowContext[dict]) -> None:
        first = msgs[0]
        await ctx.send_message({"moment": first["moment"], "token": first["token"],
                                "results": [m["result"] for m in msgs]})


class EditorExecutor(Executor):
    def __init__(self, svc: Services):
        super().__init__(id="editor")
        self.svc = svc

    @handler
    async def go(self, msg: dict, ctx: WorkflowContext[dict]) -> None:
        s = self.svc
        if s.crash_in_editor:
            raise RuntimeError("simulated crash after the scouts")
        m = Moment.model_validate(msg["moment"])
        results = [ScoutResult.model_validate(r) for r in msg["results"]]

        async def rerun(scout: str, question: str, suffix: str) -> ScoutResult:
            return await run_scout(scout, s.llm, s.tools, s.checker, s.store, m, msg["token"],
                                   extra={"evidence_request": question}, id_suffix=suffix)

        dec = await edit_moment(s.llm, s.tools, s.store, s.roster, s.periods, m, msg["token"],
                                results, rerun, revision=msg.get("revision", 1),
                                supersedes_revision=msg.get("supersedes_revision"),
                                reopen=msg.get("reopen"))
        persist(s.store, m, results, dec)
        await ctx.send_message({
            "moment": msg["moment"], "t0": msg.get("t0", time.time()),
            "supersedes_by_profile": msg.get("supersedes_by_profile", {}),
            "reopen": msg.get("reopen"),
            "moment_id": m.moment_id, "status": dec.status, "reason": dec.reason,
            "story": dec.story.model_dump(mode="json") if dec.story else None,
            "verdict": dec.verdict.model_dump(mode="json") if dec.verdict else None,
            "claims": {k: v.model_dump(mode="json") for k, v in dec.records.items()},
            "notes": dec.notes, "evidence_round": dec.evidence_round,
            "editor_calls": dec.editor_calls})


class ProducerExecutor(Executor):
    def __init__(self, svc: Services, profile: ViewerProfile):
        super().__init__(id=f"producer-{profile.profile_id}")
        self.svc, self.profile = svc, profile

    @handler
    async def go(self, msg: dict, ctx: WorkflowContext[dict]) -> None:
        s = self.svc
        if msg["status"] != "story":
            res = ProducerResult(profile_id=self.profile.profile_id, status="skipped")
        else:
            story = Story.model_validate(msg["story"])
            records = {k: ClaimRecord.model_validate(v) for k, v in msg["claims"].items()}
            m = Moment.model_validate(msg["moment"])
            evidence_players = sorted({
                se.event.player_id for c in story.claim_ids for e in records[c].evidence_ids
                if (se := s.store.get_event(e)) and se.event.player_id})
            res = await run_producer(
                s.llm, s.store, s.roster, s.periods, self.profile, story, records,
                {"moment_id": m.moment_id, "kind": m.kind, "team_id": m.team_id},
                evidence_players)
        await ctx.send_message({**msg, "producer": res.model_dump(mode="json")})


def trigger_claim(s: Services, m: Moment) -> ClaimRecord | None:
    """A verified claim that the event being explained still stands. Every explanation depends
    on it, so a correction that voids that event takes the explanation down (design §4)."""
    if not m.trigger_event_id:
        return None
    ev = s.store.get_event(m.trigger_event_id)
    if ev is None or ev.status != "active":
        return None  # reopened moments explain the correction itself
    rec = s.checker.verify(Claim(claim_id=f"{m.moment_id}.trigger", type="event_fact", params={
        "event_id": m.trigger_event_id, "field": "status", "value": "active"}), m.now_ms,
        "story", m.moment_id)
    return rec if rec.status == "verified" else None


def publish_outputs(s: Services, m: Moment, story: Story, records: dict, producers: dict,
                    t0: float, supersedes_by_profile: dict) -> tuple[list[dict], list[str]]:
    """Assemble and publish each profile's approved output (publish-time re-check inside)."""
    overlays: list[dict] = []
    stale_all: list[str] = []
    trig = trigger_claim(s, m)
    for prof in s.profiles:
        pr = producers.get(prof.profile_id)
        if not pr or pr["status"] != "ok":
            continue
        vo = ProducerResult.model_validate(pr).output
        latency = int((time.time() - t0) * 1000)
        show = max(m.now_ms, int(s.clock_ms())) if s.clock_ms else m.now_ms
        ov = assemble_overlay(story, vo, prof, pr["text"], pr["spans"], records, m, s.store,
                              show, latency, vo.headline,
                              supersedes_by_profile.get(prof.profile_id), provider=s.llm.model)
        used = [records[c] for c in vo.claim_ids] + ([trig] if trig else [])
        s.store.put("viewer_output", vo)
        s.store.add_deps([("story", f"{story.story_id}:{story.revision}", "viewer_output",
                           vo.output_id), ("viewer_output", vo.output_id, "overlay",
                                           ov.overlay_id)])
        if s.publisher is None:
            overlays.append(ov.model_dump(mode="json"))
            continue
        res = s.publisher.publish(ov, used, m.now_ms)
        if res.published:
            overlays.append(ov.model_dump(mode="json"))
        else:
            stale_all += res.stale_claims
    return overlays, stale_all


class PublishExecutor(Executor):
    def __init__(self, svc: Services):
        super().__init__(id="publish")
        self.svc = svc

    @handler
    async def go(self, msgs: list[dict], ctx: WorkflowContext[dict, dict]) -> None:
        s = self.svc
        base = msgs[0]
        m = Moment.model_validate(base["moment"])
        producers = {x["producer"]["profile_id"]: x["producer"] for x in msgs}
        overlays, stale_all = [], []
        trig = s.store.get_event(m.trigger_event_id) if m.trigger_event_id else None
        if (base["status"] == "story" and not base.get("reopen") and trig is not None
                and trig.status != "active"):
            # the event this story explains was corrected while we worked: do not air it
            stale_all = [f"trigger {m.trigger_event_id} is {trig.status}"]
            if s.on_stale:
                s.on_stale(m, stale_all)
        elif base["status"] == "story":
            story = Story.model_validate(base["story"])
            records = {k: ClaimRecord.model_validate(v) for k, v in base["claims"].items()}
            overlays, stale_all = publish_outputs(
                s, m, story, records, producers, base["t0"],
                base.get("supersedes_by_profile", {}))
            if stale_all and s.on_stale:
                s.on_stale(m, stale_all)
            if overlays:
                s.store.set_status("moment", m.moment_id, "published")
        await ctx.yield_output({
            "moment_id": m.moment_id, "status": base["status"], "reason": base["reason"],
            "story": base["story"], "verdict": base["verdict"], "claims": base["claims"],
            "notes": base["notes"], "evidence_round": base["evidence_round"],
            "editor_calls": base["editor_calls"], "overlays": overlays,
            "producers": {k: v["status"] for k, v in producers.items()},
            "stale_claims": stale_all})


def build_reopen_workflow(svc: Services, checkpoint_dir: Path | str):
    start, editor = ReopenStart(), EditorExecutor(svc)
    producers = [ProducerExecutor(svc, p) for p in svc.profiles]
    publish = PublishExecutor(svc)
    return (WorkflowBuilder(start_executor=start, name="second-look-reopen",
                            checkpoint_storage=FileCheckpointStorage(str(checkpoint_dir)))
            .add_edge(start, editor)
            .add_fan_out_edges(editor, producers)
            .add_fan_in_edges(producers, publish)
            .build())


def build_workflow(svc: Services, checkpoint_dir: Path | str):
    dispatch = Dispatch()
    scouts = [ScoutExecutor(svc, n) for n in svc.scouts]
    collect, editor = Collect(), EditorExecutor(svc)
    producers = [ProducerExecutor(svc, p) for p in svc.profiles]
    publish = PublishExecutor(svc)
    return (WorkflowBuilder(start_executor=dispatch, name="second-look-moment",
                            checkpoint_storage=FileCheckpointStorage(str(checkpoint_dir)))
            .add_fan_out_edges(dispatch, scouts)
            .add_fan_in_edges(scouts, collect)
            .add_edge(collect, editor)
            .add_fan_out_edges(editor, producers)
            .add_fan_in_edges(producers, publish)
            .build())


@dataclass
class MomentOutcome:
    moment_id: str
    status: str  # story | fallback
    reason: str = ""
    story: dict | None = None
    verdict: dict | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    evidence_round: bool = False
    editor_calls: int = 0
    overlays: list[dict] = field(default_factory=list)
    producers: dict[str, str] = field(default_factory=dict)
    stale_claims: list[str] = field(default_factory=list)


def _outcome(raw: dict) -> MomentOutcome:
    return MomentOutcome(**{k: raw[k] for k in (
        "moment_id", "status", "reason", "story", "verdict", "claims", "notes",
        "evidence_round", "editor_calls", "overlays", "producers", "stale_claims")})


async def run_moment(svc: Services, moment: Moment, token: str,
                     checkpoint_dir: Path | str | None = None) -> MomentOutcome:
    cp = Path(checkpoint_dir) if checkpoint_dir else Path(tempfile.mkdtemp(prefix="sl-cp-"))
    wf = build_workflow(svc, cp)
    result = await wf.run({"moment": moment.model_dump(mode="json"), "token": token,
                           "t0": time.time()})
    outs = result.get_outputs()
    if not outs:
        svc.store.set_status("moment", moment.moment_id, "fallback")
        return MomentOutcome(moment.moment_id, "fallback", "workflow produced no output")
    return _outcome(outs[0])


async def resume_moment(svc: Services, moment: Moment, checkpoint_dir: Path | str
                        ) -> MomentOutcome:
    """Resume from the latest checkpoint after a crash. Scouts are not called again."""
    storage = FileCheckpointStorage(str(checkpoint_dir))
    wf = build_workflow(svc, checkpoint_dir)
    cps = await storage.list_checkpoints(workflow_name=wf.name)
    latest = max(cps, key=lambda c: c.iteration_count)
    result = await wf.run(checkpoint_id=latest.checkpoint_id)
    outs = result.get_outputs()
    if not outs:
        svc.store.set_status("moment", moment.moment_id, "fallback")
        return MomentOutcome(moment.moment_id, "fallback", "resume produced no output")
    return _outcome(outs[0])


async def run_reopen(svc: Services, msg: dict, checkpoint_dir: Path | str | None = None
                     ) -> MomentOutcome:
    cp = Path(checkpoint_dir) if checkpoint_dir else Path(tempfile.mkdtemp(prefix="sl-cp-"))
    wf = build_reopen_workflow(svc, cp)
    result = await wf.run({**msg, "t0": time.time()})
    outs = result.get_outputs()
    if not outs:
        return MomentOutcome(msg["moment"]["moment_id"], "fallback", "reopen produced no output")
    return _outcome(outs[0])
