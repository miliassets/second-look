"""Orchestrates one match run: generator → delivery faults → ledger → moments → overlays → bus."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .agents.fake_model import responders as fake_responders
from .agents.llm import make_llm
from .agents.workflow import Services, run_moment
from .checker.roster import Roster
from .checker.verify import Checker
from .contracts.agents_io import Moment
from .contracts.events import Delivery, Event
from .contracts.prefs import DEFAULT_PROFILES, ViewerProfile
from .generator import GeneratedMatch, generate_match, generate_slice
from .generator.corrections import CorrectionSpec
from .generator.delivery import PRESETS, apply_faults
from .generator.player import SPEEDS, FeedItem, merge, play
from .ledger.ingest import Ingestor
from .ledger.sqlite_store import SQLiteStore
from .matchstate.tools import MatchTools
from .moments.detector import MomentDetector
from .moments.factual import build_factual_card, story_id_for
from .propagation.invalidate import Invalidation, propagate
from .propagation.notices import notice_for
from .propagation.reopen import reopen_moment
from .publish.bus import Bus
from .publish.publisher import Publisher
from .stats.engine import StatsEngine
from .timeline import format_clock, to_match_ms

MomentHandler = Callable[["Runner", Moment], Awaitable[None]]


@dataclass
class RunConfig:
    seed: int = config.DEMO_SEED
    speed: str = "max"
    faults: str = "none"
    corrections: list[CorrectionSpec] = field(default_factory=list)
    db_path: str = ":memory:"
    llm: str = "none"  # none = deterministic path only | fake | github | foundry
    scope: str = "slice"  # slice (55'-70') | full (90 minutes)
    scenario: str = "demo"  # full only: demo | rout | redcard | random
    state_dir: str = ".state"
    sync_agents: bool = False  # wait for each moment's agents before the feed goes on (tests)


def parse_speed(speed: str) -> float | str:
    if speed in SPEEDS:
        return speed
    if speed.endswith("x"):
        return float(speed[:-1])
    return float(speed)


def compact_event(e: Event, match_ms: int) -> dict:
    return {"event_id": e.event_id, "type": e.type, "team_id": e.team_id,
            "player_id": e.player_id, "match_ms": match_ms,
            "clock": format_clock(e.period, e.match_clock_ms), "x": e.x, "y": e.y,
            "end_x": e.end_x, "end_y": e.end_y, "outcome": e.outcome,
            "possession_id": e.possession_id}


class Runner:
    def __init__(self, cfg: RunConfig, bus: Bus,
                 profiles: list[ViewerProfile] | None = None, responders: dict | None = None,
                 match: GeneratedMatch | None = None):
        self.cfg, self.bus = cfg, bus
        self.responders = responders
        self.profiles = profiles or DEFAULT_PROFILES
        if match is not None:
            self.match: GeneratedMatch = match
        elif cfg.scope == "full":
            self.match = generate_match(
                cfg.seed, cfg.scenario, cfg.corrections if cfg.corrections else None)
        else:
            self.match = generate_slice(cfg.seed, cfg.corrections or None)
        self.periods = self.match.metadata.periods
        if cfg.db_path != ":memory:":
            Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cfg.db_path).unlink(missing_ok=True)
        self.store = SQLiteStore(cfg.db_path, "m001")
        self.ingestor = Ingestor(self.store, self.periods)
        self.detector = MomentDetector(self.store, self.periods)
        self.checker = Checker(self.store, self.periods, self.match.frames)
        self.roster = Roster.from_metadata(self.match.metadata)
        self.publisher = Publisher(self.store, bus, self.checker)
        self.tools = MatchTools(self.store, self.checker, self.periods)
        self.handlers: list[MomentHandler] = []
        self.tasks: set[asyncio.Task] = set()
        self.last_score: tuple[int, int] | None = None
        self.now_ms = 0
        self.done = asyncio.Event()
        bus.clock = lambda: self.now_ms
        self.services: Services | None = None
        self.reopen_attempts: dict[str, int] = {}
        self.injected = 0
        self.log: list[str] = []
        if cfg.llm != "none":
            self.attach_agents()
        deliveries = apply_faults(self.match.events, self.periods, PRESETS[cfg.faults], cfg.seed)
        self.items: list[FeedItem] = merge(deliveries, self.match.frames, self.periods)

    def attach_agents(self) -> None:
        llm = make_llm(self.cfg.llm, store=self.store, root=Path(self.cfg.state_dir).parent,
                       responders=self.responders or fake_responders())
        self.services = Services(
            llm, self.tools, self.checker, self.store, self.roster, self.periods,
            publisher=self.publisher, profiles=self.profiles, clock_ms=lambda: self.now_ms,
            on_stale=lambda m, ids: self.spawn(self.reopen(m.moment_id, {}, {})))

        async def handle(runner: Runner, m: Moment) -> None:
            cp = Path(self.cfg.state_dir) / "checkpoints" / m.moment_id
            await run_moment(self.services, m, runner.tools.by_moment[m.moment_id], cp)
        self.handlers.append(handle)

    # ---- messages
    def meta_payload(self) -> dict:
        md = self.match.metadata
        return {
            "match_id": md.match_id, "label": md.label, "venue": md.venue,
            "home": md.home.model_dump(), "away": md.away.model_dump(),
            "players": [{"player_id": p.player_id, "team_id": p.team_id, "name": p.name,
                         "number": p.number, "role": p.role} for p in md.players],
            "periods": md.periods, "profiles": [p.model_dump() for p in self.profiles],
            "mode": "live", "speed": self.cfg.speed, "llm": self.cfg.llm,
            "start_ms": to_match_ms(self.match.frames[0].period, self.match.frames[0].match_clock_ms, self.periods),
        }

    def publish_score(self, force: bool = False) -> None:
        st = StatsEngine.from_store(self.store, to_ms=self.now_ms)
        sc, _ = st.score(self.now_ms)
        home, away = self.match.metadata.home.team_id, self.match.metadata.away.team_id
        cur = (sc.get(home, 0), sc.get(away, 0))
        if force or cur != self.last_score:
            self.last_score = cur
            self.bus.publish("score", {"home": cur[0], "away": cur[1], "match_ms": self.now_ms})

    # ---- main loop
    async def run(self) -> None:
        if self.bus.meta is None:
            self.bus.publish("meta", self.meta_payload())
        n = 0
        async for it in play(self.items, parse_speed(self.cfg.speed)):
            n += 1
            self.now_ms = max(self.now_ms, it.at_ms)
            if it.kind == "frame":
                self.on_frame(it)
            else:
                await self.on_delivery(it.delivery)
            if self.cfg.sync_agents and it.kind == "event":
                while self.tasks:
                    await asyncio.gather(*list(self.tasks))
            if n % 40 == 0:
                await asyncio.sleep(0)
        for t in list(self.tasks):
            await t
        self.now_ms += 1  # `end` sorts after the last frame in any replay
        self.bus.publish("end", {"match_ms": self.now_ms})
        self.done.set()

    def on_frame(self, it: FeedItem) -> None:
        f = it.frame
        self.ingestor.tick(it.at_ms)
        self.bus.publish("frame", {
            "match_ms": it.at_ms, "clock": format_clock(f.period, f.match_clock_ms),
            "players": [[p.player_id, p.x, p.y] for p in f.players], "ball": list(f.ball)})

    async def on_delivery(self, d: Delivery) -> None:
        res = self.ingestor.ingest(d)
        if res.duplicate:
            return
        e = d.event
        self.bus.publish("event", compact_event(e, to_match_ms(e.period, e.match_clock_ms,
                                                              self.periods)))
        if e.type in ("match_metadata", "goal", "own_goal", "event_correction"):
            self.publish_score(force=e.type == "match_metadata")
        inv = None
        if res.kind in ("correction", "late", "gap"):
            inv = propagate(self.store, self.checker, res, self.now_ms)
        for m in self.detector.process(res):
            self.on_moment(m)
        if inv is not None:
            self.on_invalidation(inv, res)

    def spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def on_invalidation(self, inv: Invalidation, res) -> None:
        """Air notices at once (deterministic), re-render the score, then reopen moments."""
        self.publish_score()
        corr = self.store.get_event(res.event_id) if res.kind == "correction" else None
        q = corr.event.qualifiers if corr else {}
        target = self.store.get_event(q.get("corrects", "")) if corr else None
        kind = "disallowed_goal" if (q.get("kind") == "void" and target
                                     and target.event.type == "goal") else "correction"
        notice_ids: dict[str, dict[str, str]] = {}
        latest: dict[tuple[str, str], object] = {}
        for ov in inv.superseded:
            key = (ov.story_id, ov.profile_id)
            if key not in latest or ov.revision >= latest[key].revision:
                latest[key] = ov
        profiles = {p.profile_id: p for p in self.profiles}
        for (story_id, prof_id), ov in latest.items():
            if corr is None:
                continue
            n = notice_for(corr.event.event_id, kind, ov, profiles[prof_id], self.store,
                           self.checker, self.roster, self.periods, inv.rev, self.now_ms)
            if n is None:
                continue
            overlay, claims = n
            r = self.publisher.publish(overlay, claims, self.now_ms)
            if r.published:
                notice_ids.setdefault(story_id, {})[prof_id] = overlay.overlay_id
        normal, recaps = [], []
        for mid in inv.moments:
            mo = self.store.get("moment", mid)
            if mo is None or mo.kind in ("disallowed_goal", "correction"):
                continue
            ids = notice_ids.get(story_id_for(mid), {})
            if mo.kind in ("half_time", "full_time"):
                recaps.append((mid, ids))
            elif self.services is not None:
                normal.append((mid, ids))

        async def after():
            # replacement stories first, so a rebuilt recap cites their new revisions
            await asyncio.gather(*[self.reopen(m, inv.invalidated_claims, i) for m, i in normal])
            for m, i in recaps:
                await self.reopen(m, inv.invalidated_claims, i)
        if normal or recaps:
            self.spawn(after())

    def build_recap(self, m: Moment, notice_ids: dict | None = None) -> list[str]:
        from .publish.recap import build_recap
        return build_recap(self.store, self.publisher, self.profiles, m, self.now_ms,
                           notice_ids or {})

    async def reopen(self, moment_id: str, reasons: dict, notice_ids: dict) -> None:
        mo0 = self.store.get("moment", moment_id)
        if mo0 is not None and mo0.kind in ("half_time", "full_time"):
            self.build_recap(mo0, notice_ids)
            self.log.append(f"recap rebuilt {moment_id}")
            return
        n = self.reopen_attempts.get(moment_id, 0)
        if self.services is None or n >= 3:
            return
        self.reopen_attempts[moment_id] = n + 1
        mo = self.store.get("moment", moment_id)
        if mo is None:
            return
        cp = Path(self.cfg.state_dir) / "checkpoints" / f"{moment_id}-reopen{n + 1}"
        out = await reopen_moment(self.services, mo, reasons, notice_ids, cp)
        self.log.append(f"reopen {moment_id}: {out.status if out else 'notice only'}")

    async def ask(self, question: str, profile_id: str) -> dict:
        from .agents.askwhy import ask_why
        if self.services is None:
            raise LookupError("agents are off (llm=none)")
        prof = next((p for p in self.profiles if p.profile_id == profile_id), None)
        if prof is None:
            raise KeyError(profile_id)
        r = await ask_why(self.services, question, prof, self.now_ms)
        return {"supported": r.supported, "text": r.text, "overlay_id": r.overlay.overlay_id,
                "reason": r.reason}

    async def inject_correction(self, kind: str) -> dict:
        """Live control: a correction arrives now (design §5.2 control bar)."""
        from .generator.corrections import CorrectionSpec, insert_corrections
        evs = [s.event for s in self.store.get_events(statuses=("active", "corrected", "void"))]
        goal = next((e for e in evs if e.type == "goal"), None)
        if goal is None:
            raise LookupError("no goal on the ledger yet")
        self.injected += 1
        truth = {"beats": {"goal": {"event_id": goal.event_id},
                           "recovery_event_id": self.match.ground_truth["beats"].get(
                               "recovery_event_id")}}
        spec = (CorrectionSpec("void_goal", delay_ms=0) if kind == "void_goal"
                else CorrectionSpec("reattribute", delay_ms=0, new_player_id="IRV-06"))
        out = insert_corrections(evs, [spec], truth)
        ev = next(e for e in out if e.type == "event_correction")
        ev = ev.model_copy(update={"seq": 1_000_000 + self.injected,
                                   "event_id": f"{ev.event_id}-live{self.injected}"})
        if ev.qualifiers.get("replacement"):
            ev.qualifiers["replacement"]["event_id"] = f"m001-rlive{self.injected}"
        ev.match_clock_ms = max(ev.match_clock_ms, self.clock_for_injection())
        d = Delivery(event=ev, arrival_order=10**6 + self.injected, arrival_match_ms=self.now_ms)
        await self.on_delivery(d)
        return {"injected": kind, "event_id": ev.event_id}

    def clock_for_injection(self) -> int:
        return max(0, self.now_ms - self.periods[0]["length_ms"])

    def on_moment(self, m: Moment) -> None:
        self.tools.issue_token(m)
        if m.kind in ("disallowed_goal", "correction"):
            return  # the notice (from propagation) replaces the factual card and the agents
        if m.kind in ("half_time", "full_time"):
            for p in self.profiles:  # the score card, then the recap built from live stories
                card = build_factual_card(m, p, self.store, self.checker, self.roster,
                                          self.periods)
                if card is not None:
                    self.publisher.publish(card.overlay, card.claims, m.now_ms)
            self.build_recap(m)
            return
        for p in self.profiles:
            card = build_factual_card(m, p, self.store, self.checker, self.roster, self.periods)
            if card is not None:
                self.publisher.publish(card.overlay, card.claims, m.now_ms)
        for h in self.handlers:
            t = asyncio.create_task(h(self, m))
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)
