"""Chat-client factory, budget meter, response cache and call logging (design §6).

Providers: `fake` (default, offline) | `github` (GitHub Models, OpenAI-compatible) | `foundry`.
Nothing here calls a model unless the provider is github/foundry and a key is configured.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from .. import config


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    agent: str
    instructions: str
    messages: list[dict[str, Any]]  # {"role": "user"|"assistant"|"tool", "content": str, ...}
    tools: list[ToolSpec] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    max_tokens: int = 400
    model: str = ""


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


def request_key(req: LLMRequest) -> str:
    """Hash of everything that decides the answer: model, instructions, messages, tools, schema."""
    blob = json.dumps({"m": req.model, "i": req.instructions, "msgs": req.messages,
                       "t": [t.model_dump() for t in req.tools], "s": req.response_schema},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class MissingFixture(Exception):
    pass


class ChatClient(Protocol):
    name: str

    async def complete(self, req: LLMRequest) -> LLMResponse: ...


class FixtureClient:
    """Replays recorded responses by request key; a missing key is a hard error."""
    name = "fixture"

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        p = self.dir / f"{request_key(req)}.json"
        if not p.exists():
            raise MissingFixture(f"no fixture for {req.agent} key {request_key(req)}")
        return LLMResponse.model_validate_json(p.read_text())

    @staticmethod
    def record(directory: Path, req: LLMRequest, resp: LLMResponse) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{request_key(req)}.json"
        p.write_text(resp.model_dump_json(indent=2) + "\n")
        return p


class ScriptedClient:
    """Deterministic stand-in: `responders[agent](req) -> LLMResponse`. Used as the offline
    `fake` model (rule-based, not recorded model output) and in tests."""
    name = "scripted"

    def __init__(self, responders: dict[str, Any] | None = None):
        self.responders = responders or {}
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        fn = self.responders.get(req.agent)
        if fn is None:
            raise MissingFixture(f"no scripted responder for agent {req.agent}")
        r = fn(req)
        if asyncio.iscoroutine(r):
            r = await r
        return r


ENDPOINTS = {
    "github": "https://models.github.ai/inference",  # UNVERIFIED (docs unreachable at build time)
}
KEY_ENV = {"github": "GITHUB_TOKEN", "foundry": "FOUNDRY_KEY"}


class OpenAICompatClient:
    """Chat completions over HTTP (GitHub Models, Foundry OpenAI-compatible endpoints)."""

    def __init__(self, name: str, endpoint: str, key: str, model: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.name, self.endpoint, self.key, self.model = name, endpoint.rstrip("/"), key, model
        self.transport = transport

    def body(self, req: LLMRequest) -> dict:
        msgs = [{"role": "system", "content": req.instructions}, *req.messages]
        b: dict[str, Any] = {"model": req.model or self.model, "messages": msgs,
                             "max_tokens": req.max_tokens, "temperature": 0}
        if req.tools:
            b["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools]
        if req.response_schema:
            b["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "out", "schema": req.response_schema, "strict": False}}
        return b

    async def complete(self, req: LLMRequest) -> LLMResponse:
        async with httpx.AsyncClient(transport=self.transport, timeout=30) as c:
            r = await c.post(f"{self.endpoint}/chat/completions", json=self.body(req),
                             headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
            d = r.json()
        msg = d["choices"][0]["message"]
        calls = [ToolCall(id=t["id"], name=t["function"]["name"],
                          arguments=json.loads(t["function"]["arguments"] or "{}"))
                 for t in msg.get("tool_calls") or []]
        u = d.get("usage", {})
        return LLMResponse(text=msg.get("content") or "", tool_calls=calls,
                           tokens_in=u.get("prompt_tokens", 0),
                           tokens_out=u.get("completion_tokens", 0), model=d.get("model", ""))


@dataclass
class Budget:
    """Hard stops: per-run and per-day call caps (day count persisted to disk)."""
    run_cap: int
    daily_cap: int
    state_file: Path | None = None
    run_calls: int = 0

    def _read(self) -> dict:
        if self.state_file and self.state_file.exists():
            d = json.loads(self.state_file.read_text())
            if d.get("day") == date.today().isoformat():
                return d
        return {"day": date.today().isoformat(), "calls": 0}

    def allow(self) -> bool:
        return self.run_calls < self.run_cap and self._read()["calls"] < self.daily_cap

    def spend(self) -> None:
        self.run_calls += 1
        if self.state_file:
            d = self._read()
            d["calls"] += 1
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(d))


@dataclass
class CallResult:
    outcome: str  # ok | timeout | invalid | budget | error
    response: LLMResponse | None = None
    call_id: str = ""
    cached: bool = False
    error: str = ""


@dataclass
class ManagedLLM:
    client: ChatClient
    store: Any = None  # LedgerStore with log_call
    cache_dir: Path | None = None
    budget: Budget | None = None
    model: str = ""
    timeout_s: float = 8.0
    live: bool = False  # true for providers that cost calls
    calls_made: int = field(default=0)

    async def call(self, req: LLMRequest, moment_id: str = "",
                   timeout_s: float | None = None) -> CallResult:
        req = req.model_copy(update={"model": req.model or self.model})
        key = request_key(req)
        call_id = f"call-{uuid.uuid4().hex[:10]}"
        started = time.time()
        wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))

        def log(outcome, resp=None, cached=False):
            if self.store is not None:
                self.store.log_call({
                    "call_id": call_id, "moment_id": moment_id, "agent": req.agent,
                    "model": (resp.model if resp and resp.model else req.model) or self.client.name,
                    "started_wall": wall, "ms": int((time.time() - started) * 1000),
                    "tokens_in": resp.tokens_in if resp else 0,
                    "tokens_out": resp.tokens_out if resp else 0, "outcome": outcome,
                    "trace_id": key, "cached": int(cached)})

        if self.cache_dir:
            p = self.cache_dir / f"{key}.json"
            if p.exists():
                resp = LLMResponse.model_validate_json(p.read_text())
                log("ok", resp, cached=True)
                return CallResult("ok", resp, call_id, cached=True)
        if self.live and self.budget and not self.budget.allow():
            log("budget")
            return CallResult("budget", None, call_id)
        try:
            resp = await asyncio.wait_for(self.client.complete(req),
                                          timeout_s if timeout_s is not None else self.timeout_s)
        except TimeoutError:
            log("timeout")
            return CallResult("timeout", None, call_id)
        except MissingFixture:
            raise
        except Exception as e:  # network, HTTP error: the caller falls back
            log("error")
            return CallResult("error", None, call_id, error=f"{type(e).__name__}: {e}")
        self.calls_made += 1
        if self.live and self.budget:
            self.budget.spend()
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(resp.model_dump_json())
        log("ok", resp)
        return CallResult("ok", resp, call_id)

    def mark_invalid(self, call_id: str) -> None:
        if self.store is not None:
            self.store.set_call_outcome(call_id, "invalid")


def make_llm(provider: str | None = None, store: Any = None, root: Path | None = None,
             responders: dict[str, Any] | None = None, fixtures: Path | None = None
             ) -> ManagedLLM:
    """Build the managed client. `fake` never touches the network."""
    cfg = config.llm_config()
    provider = provider or cfg.provider
    root = root or Path(".")
    if provider == "fake":
        client: ChatClient = FixtureClient(fixtures) if fixtures else ScriptedClient(responders)
        return ManagedLLM(client, store, None, None, "fake", live=False)
    key = os.environ.get(KEY_ENV.get(provider, ""), "")
    if not key:
        raise RuntimeError(f"provider {provider!r} needs {KEY_ENV.get(provider)} in .env")
    if provider == "github":
        client = OpenAICompatClient("github", ENDPOINTS["github"], key, "openai/gpt-4.1-nano")
    elif provider == "foundry":
        client = OpenAICompatClient("foundry", os.environ["FOUNDRY_ENDPOINT"], key,
                                    os.environ.get("FOUNDRY_MODEL", "gpt-4.1-nano"))
    else:
        raise ValueError(provider)
    budget = Budget(cfg.run_cap, cfg.daily_cap, root / ".state" / "llm_budget.json")
    return ManagedLLM(client, store, root / ".cache" / "llm", budget,
                      getattr(client, "model", ""), live=True)
