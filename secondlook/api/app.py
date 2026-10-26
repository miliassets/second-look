"""FastAPI app: SSE stream, state, static front end."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..matchstate.mcp_server import build_mcp
from ..publish.bus import Bus
from ..runner import RunConfig, Runner

WEB = Path(__file__).resolve().parents[2] / "web"


def create_app(cfg: RunConfig | None = None, autostart: bool = True,
               setup=None) -> FastAPI:
    """`setup(runner)` lets later steps attach handlers (agents) before the feed starts."""
    cfg = cfg or RunConfig()
    bus = Bus()
    holder: dict = {"runner": None, "task": None}

    def prepare() -> Runner:
        if holder["runner"] is None:
            runner = Runner(cfg, bus)
            if setup:
                setup(runner)
            holder["runner"] = runner
            bus.publish("meta", runner.meta_payload())  # viewers can connect before the feed starts
        return holder["runner"]

    def start() -> Runner:
        runner = prepare()
        if holder["task"] is None:
            holder["task"] = asyncio.create_task(runner.run())
        return runner

    mcp = build_mcp(lambda: holder["runner"].tools if holder["runner"] else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        prepare()
        if autostart:
            start()
        async with mcp.session_manager.run():
            yield
        t = holder["task"]
        if t and not t.done():
            t.cancel()

    app = FastAPI(title="Second Look", lifespan=lifespan)
    app.state.bus = bus
    app.state.holder = holder
    app.state.start = start

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/state")
    def state():
        r: Runner | None = holder["runner"]
        return {"started": holder["task"] is not None, "ended": bus.ended, "match_ms": r.now_ms if r else 0,
                "speed": cfg.speed, "llm": cfg.llm, "mode": "live"}

    @app.post("/control/start")
    async def control_start():
        if holder["task"] is not None:
            raise HTTPException(409, "already started")
        start()
        return {"started": True}

    @app.post("/control/correction")
    async def control_correction(kind: str = "void_goal"):
        r = holder["runner"]
        if r is None or holder["task"] is None:
            raise HTTPException(409, "feed not started")
        if kind not in ("void_goal", "reattribute"):
            raise HTTPException(422, "kind must be void_goal or reattribute")
        try:
            return await r.inject_correction(kind)
        except LookupError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/story/{story_id}")
    def story_detail(story_id: str):
        r: Runner | None = holder["runner"]
        if r is None:
            raise HTTPException(409, "feed not started")
        st = r.store
        mid = "mo-" + story_id.removeprefix("st-")
        moment = st.get("moment", mid)
        if moment is None:
            raise HTTPException(404, "unknown story")
        cands = []
        for c in st.list_kind("candidate", moment_id=mid):
            claims = []
            for x in c.claims:
                rec = st.get("claim", x.claim_id)
                claims.append({"claim_id": x.claim_id, "type": x.type,
                               "status": rec.status if rec else "unknown",
                               "reason": rec.reason if rec else "",
                               "value": rec.value if rec else None,
                               "evidence_ids": rec.evidence_ids[:12] if rec else []})
            cands.append({**c.model_dump(mode="json", exclude={"claims"}), "claims": claims})
        stories = [s.model_dump(mode="json") for s in st.list_kind("story", moment_id=mid)]
        verdicts = [v.model_dump(mode="json") for v in st.list_kind("verdict", moment_id=mid)]
        return {"moment": moment.model_dump(mode="json"), "candidates": cands,
                "stories": stories, "verdicts": verdicts, "calls": st.calls(mid)}

    @app.post("/ask")
    async def ask(body: dict):
        r = holder["runner"]
        q = str(body.get("question", "")).strip()
        if r is None or holder["task"] is None:
            raise HTTPException(409, "feed not started")
        if not q or len(q) > 300:
            raise HTTPException(422, "question must be 1-300 characters")
        try:
            return await r.ask(q, str(body.get("profile_id", "analyst-en")))
        except KeyError as e:  # KeyError is a LookupError: test it first
            raise HTTPException(404, "unknown profile") from e
        except LookupError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/stream")
    async def stream(profile: str | None = None):
        q, backlog = bus.subscribe(profile)

        async def gen() -> AsyncIterator[dict]:
            try:
                for m in backlog:
                    yield {"event": m.type, "data": json.dumps(m.payload)}
                    if m.type == "end":
                        return
                while True:
                    m = await q.get()
                    yield {"event": m.type, "data": json.dumps(m.payload)}
                    if m.type == "end":
                        return
            finally:
                bus.unsubscribe(q)

        return EventSourceResponse(gen(), ping=15)

    app.mount("/mcp", mcp.streamable_http_app())
    if WEB.exists():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app
