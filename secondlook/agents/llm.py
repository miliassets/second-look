"""Chat-client factory, budget meter, response cache and call logging (design §6).

Providers: `fake` (default, offline) | `github` (GitHub Models, OpenAI-compatible) | `foundry` |
`gemini` (Google's native generateContent API; needs no key in this code: a network proxy or
the caller's environment supplies credentials).
Nothing here calls a model unless the provider is github/foundry/gemini.
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
KEY_ENV = {"github": "GITHUB_TOKEN", "foundry": "FOUNDRY_KEY"}  # gemini: no key handled here
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS = ("gemini-3.5-flash", "gemini-flash-latest")  # preferred, fallback
RETRY_STATUS = {500, 502, 503, 504}  # worth retrying on the same model
FALLBACK_STATUS = RETRY_STATUS | {429}  # 429 = daily quota: no retry, try the other model
_DROP_SCHEMA_KEYS = {"title", "default", "additionalProperties", "$schema"}


def _clean_schema(node: Any) -> Any:
    """Tool parameter schemas: drop keywords Gemini's function declarations reject."""
    if isinstance(node, dict):
        return {k: _clean_schema(v) for k, v in node.items() if k not in _DROP_SCHEMA_KEYS}
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    return node


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


class GeminiNativeClient:
    """Google's native `models/<model>:generateContent`. No key is read or sent by this code.

    Mapping: instructions -> systemInstruction, messages -> contents, tools ->
    functionDeclarations, functionCall -> ToolCall. Structured output uses
    responseMimeType=application/json + responseJsonSchema; if the API rejects the schema (400)
    the schema is put in the prompt instead. Gemini 3 thought signatures ride inside
    ToolCall.id ("gem-<n>.<signature>") so they come back with the tool result.
    """

    def __init__(self, model: str = GEMINI_MODELS[0], fallback: str | None = GEMINI_MODELS[1],
                 transport: httpx.AsyncBaseTransport | None = None, base: str = GEMINI_BASE,
                 tries: int = 3, backoff_s: float = 1.0, timeout_s: float = 60.0,
                 thinking: str = "low"):
        self.name, self.model, self.fallback, self.base = "gemini", model, fallback, base.rstrip("/")
        self.transport, self.tries, self.backoff_s, self.timeout_s = transport, tries, backoff_s, timeout_s
        self.schema_fallbacks = 0
        self.thinking = thinking  # thinkingLevel: minimal | low | medium | high ("" = model default)

    @staticmethod
    def _contents(messages: list[dict]) -> list[dict]:
        names: dict[str, str] = {}
        out: list[dict] = []

        def add(role: str, part: dict) -> None:
            if out and out[-1]["role"] == role and (role == "user" or "functionCall" in part):
                out[-1]["parts"].append(part)
            else:
                out.append({"role": role, "parts": [part]})

        for m in messages:
            role = m.get("role")
            if role == "assistant":
                if m.get("content"):
                    out.append({"role": "model", "parts": [{"text": m["content"]}]})
                for c in m.get("tool_calls") or []:
                    cid, fn = c["id"], c["function"]
                    names[cid] = fn["name"]
                    part: dict[str, Any] = {"functionCall": {
                        "name": fn["name"], "args": json.loads(fn.get("arguments") or "{}")}}
                    if "." in cid:
                        part["thoughtSignature"] = cid.split(".", 1)[1]
                    add("model", part)
            elif role == "tool":
                try:
                    result = json.loads(m.get("content") or "null")
                except ValueError:
                    result = m.get("content")
                if not isinstance(result, dict):
                    result = {"result": result}
                add("user", {"functionResponse": {
                    "name": names.get(m.get("tool_call_id", ""), "tool"), "response": result}})
            else:
                add("user", {"text": m.get("content") or ""})
        return out

    def body(self, req: LLMRequest, schema_in_prompt: bool = False) -> dict:
        instructions = req.instructions
        gen: dict[str, Any] = {"temperature": 0, "maxOutputTokens": max(req.max_tokens * 4, 1024)}
        if self.thinking:
            gen["thinkingConfig"] = {"thinkingLevel": self.thinking}
        if req.response_schema and not req.tools:
            gen["responseMimeType"] = "application/json"
            if schema_in_prompt:
                instructions += ("\n\nReply with JSON only, matching this JSON Schema:\n"
                                 + json.dumps(req.response_schema))
            else:
                gen["responseJsonSchema"] = req.response_schema
        elif req.response_schema:
            # tools and a JSON mime type do not mix on every model: ask for JSON in the prompt
            instructions += ("\n\nWhen you are done calling tools, reply with JSON only, "
                             "matching this JSON Schema:\n" + json.dumps(req.response_schema))
        b: dict[str, Any] = {"systemInstruction": {"parts": [{"text": instructions}]},
                             "contents": self._contents(req.messages), "generationConfig": gen}
        if req.tools:
            b["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description,
                 **({"parameters": _clean_schema(t.parameters)}
                    if t.parameters.get("properties") else {})} for t in req.tools]}]
        return b

    async def _post(self, c: httpx.AsyncClient, model: str, body: dict) -> httpx.Response:
        r = None
        for i in range(self.tries):
            r = await c.post(f"{self.base}/{model}:generateContent", json=body)
            if r.status_code not in RETRY_STATUS:
                return r
            if i + 1 < self.tries:
                await asyncio.sleep(self.backoff_s * 2 ** i)
        assert r is not None
        return r

    async def complete(self, req: LLMRequest) -> LLMResponse:
        model = req.model if req.model.startswith("gemini") else self.model
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s) as c:
            body = self.body(req)
            r = await self._post(c, model, body)
            if r.status_code == 400 and req.response_schema and not req.tools:
                self.schema_fallbacks += 1
                body = self.body(req, schema_in_prompt=True)
                r = await self._post(c, model, body)
            if r.status_code in FALLBACK_STATUS and self.fallback and self.fallback != model:
                model = self.fallback
                r = await self._post(c, model, body)
            r.raise_for_status()
            d = r.json()
        cand = (d.get("candidates") or [{}])[0]
        text, calls = "", []
        for i, p in enumerate((cand.get("content") or {}).get("parts") or []):
            if p.get("thought"):
                continue
            if "functionCall" in p:
                fc = p["functionCall"]
                sig = p.get("thoughtSignature")
                calls.append(ToolCall(id=f"gem-{i}" + (f".{sig}" if sig else ""),
                                      name=fc["name"], arguments=fc.get("args") or {}))
            elif "text" in p:
                text += p["text"]
        u = d.get("usageMetadata", {})
        return LLMResponse(text=text, tool_calls=calls, tokens_in=u.get("promptTokenCount", 0),
                           tokens_out=u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
                           model=d.get("modelVersion") or model)


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
    if provider != "gemini" and not key:
        raise RuntimeError(f"provider {provider!r} needs {KEY_ENV.get(provider)} in .env")
    if provider == "github":
        client = OpenAICompatClient("github", ENDPOINTS["github"], key, "openai/gpt-4.1-nano")
    elif provider == "foundry":
        client = OpenAICompatClient("foundry", os.environ["FOUNDRY_ENDPOINT"], key,
                                    os.environ.get("FOUNDRY_MODEL", "gpt-4.1-nano"))
    elif provider == "gemini":
        client = GeminiNativeClient(os.environ.get("GEMINI_MODEL", GEMINI_MODELS[0]),
                                    thinking=os.environ.get("GEMINI_THINKING", "low"))
    else:
        raise ValueError(provider)
    budget = Budget(cfg.run_cap, cfg.daily_cap, root / ".state" / "llm_budget.json")
    return ManagedLLM(client, store, root / ".cache" / "llm", budget,
                      getattr(client, "model", ""), live=True)
