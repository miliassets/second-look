import json

import httpx
import pytest

from secondlook.agents.llm import (
    Budget,
    FixtureClient,
    LLMRequest,
    LLMResponse,
    ManagedLLM,
    MissingFixture,
    OpenAICompatClient,
    ScriptedClient,
    ToolSpec,
    make_llm,
    request_key,
)
from secondlook.ledger.sqlite_store import SQLiteStore


def req(text="hi", agent="a"):
    return LLMRequest(agent=agent, instructions="be brief",
                      messages=[{"role": "user", "content": text}])


def test_key_is_stable_and_sensitive():
    assert request_key(req("x")) == request_key(req("x"))
    assert request_key(req("x")) != request_key(req("y"))
    r2 = req("x").model_copy(update={"tools": [ToolSpec(name="t")]})
    assert request_key(r2) != request_key(req("x"))


async def test_fixture_replays_and_fails_on_missing(tmp_path):
    r = req("x")
    FixtureClient.record(tmp_path, r, LLMResponse(text="ok", tokens_in=3))
    c = FixtureClient(tmp_path)
    assert (await c.complete(r)).text == "ok"
    with pytest.raises(MissingFixture):
        await c.complete(req("other"))


async def test_managed_propagates_missing_fixture(tmp_path):
    m = ManagedLLM(FixtureClient(tmp_path), None, None, None, "fake")
    with pytest.raises(MissingFixture):
        await m.call(req("nope"))


async def test_cache_hit_makes_no_call(tmp_path):
    n = {"c": 0}

    def resp(r):
        n["c"] += 1
        return LLMResponse(text="a", tokens_in=5, tokens_out=2)
    store = SQLiteStore(":memory:")
    m = ManagedLLM(ScriptedClient({"a": resp}), store, tmp_path / "c", Budget(5, 5), "fake")
    r1 = await m.call(req("q"), "mo1")
    r2 = await m.call(req("q"), "mo1")
    assert r1.outcome == r2.outcome == "ok" and n["c"] == 1 and r2.cached and not r1.cached
    rows = store.calls("mo1")
    assert [x["cached"] for x in rows] == [0, 1] and rows[0]["tokens_in"] == 5


async def test_budget_cap_returns_budget_outcome(tmp_path):
    store = SQLiteStore(":memory:")
    b = Budget(run_cap=2, daily_cap=100, state_file=tmp_path / "b.json")
    m = ManagedLLM(ScriptedClient({"a": lambda r: LLMResponse(text="x")}), store, None, b,
                   "m", live=True)
    outs = [(await m.call(req(str(i)))).outcome for i in range(4)]
    assert outs == ["ok", "ok", "budget", "budget"]
    assert [c["outcome"] for c in store.calls()] == ["ok", "ok", "budget", "budget"]
    assert m.calls_made == 2
    # the daily counter is persisted and enforced for a new run
    b2 = Budget(run_cap=10, daily_cap=2, state_file=tmp_path / "b.json")
    assert not b2.allow()


async def test_timeout_and_error_outcomes():
    import asyncio

    async def slow(r):
        await asyncio.sleep(1)
        return LLMResponse()

    def boom(r):
        raise ValueError("bad")
    store = SQLiteStore(":memory:")
    m = ManagedLLM(ScriptedClient({"slow": slow, "boom": boom}), store, None, None, "m")
    assert (await m.call(req(agent="slow"), timeout_s=0.05)).outcome == "timeout"
    e = await m.call(req(agent="boom"))
    assert e.outcome == "error" and "ValueError" in e.error
    cid = (await m.call(req(agent="slow"), timeout_s=0.01)).call_id
    m.mark_invalid(cid)
    assert [c["outcome"] for c in store.calls()] == ["timeout", "error", "invalid"]


async def test_openai_compat_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "m1", "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "t1", "function": {"name": "get_dossier", "arguments": "{\"a\": 1}"}}]}}]})
    c = OpenAICompatClient("github", "https://example.test/inference", "SECRET", "mini",
                           transport=httpx.MockTransport(handler))
    r = LLMRequest(agent="s", instructions="sys", messages=[{"role": "user", "content": "u"}],
                   tools=[ToolSpec(name="get_dossier", parameters={"type": "object"})],
                   response_schema={"type": "object"})
    out = await c.complete(r)
    assert seen["url"].endswith("/inference/chat/completions") and seen["auth"] == "Bearer SECRET"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["tools"][0]["function"]["name"] == "get_dossier"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert out.tool_calls[0].arguments == {"a": 1} and out.tokens_in == 11 and out.model == "m1"


def test_factory_defaults_to_fake_and_requires_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    m = make_llm(root=tmp_path, responders={})
    assert m.model == "fake" and not m.live
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        make_llm("github", root=tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    g = make_llm("github", root=tmp_path)
    assert g.live and g.cache_dir == tmp_path / ".cache" / "llm" and g.budget.run_cap == 60


# ---- Gemini native client (mock transport, no network)
def _gem(handler, **kw):
    from secondlook.agents.llm import GeminiNativeClient
    return GeminiNativeClient(transport=httpx.MockTransport(handler), backoff_s=0, **kw)


def _gem_req(**kw):
    base = dict(agent="s", instructions="sys", messages=[{"role": "user", "content": "u"}])
    return LLMRequest(**{**base, **kw})


async def test_gemini_maps_request_and_tool_call():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return httpx.Response(200, json={"modelVersion": "gemini-3.5-flash", "usageMetadata": {
            "promptTokenCount": 9, "candidatesTokenCount": 2, "thoughtsTokenCount": 3},
            "candidates": [{"content": {"parts": [{"functionCall": {"name": "get_dossier",
                            "args": {"a": 1}}, "thoughtSignature": "SIG"}]}}]})
    req = _gem_req(tools=[ToolSpec(name="get_dossier", description="d", parameters={
        "type": "object", "title": "X", "additionalProperties": False,
        "properties": {"a": {"type": "integer", "default": 0}}})])
    out = await _gem(handler).complete(req.model_copy(update={"model": "gemini-3.5-flash"}))
    assert seen["url"].endswith("/models/gemini-3.5-flash:generateContent")
    assert not {"authorization", "x-goog-api-key"} & set(seen["headers"].keys())
    b = seen["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "sys"
    assert b["contents"] == [{"role": "user", "parts": [{"text": "u"}]}]
    decl = b["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "get_dossier"
    assert decl["parameters"] == {"type": "object", "properties": {"a": {"type": "integer"}}}
    assert out.tool_calls[0].name == "get_dossier" and out.tool_calls[0].arguments == {"a": 1}
    assert out.tool_calls[0].id == "gem-0.SIG" and out.tokens_in == 9 and out.tokens_out == 5


async def test_gemini_tool_round_trip_and_schema():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "x", "thought": True}, {"text": "{\"ok\": true}"}]}}]})
    msgs = [{"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "gem-0.SIG", "type": "function", "function": {"name": "f", "arguments": "{\"x\": 1}"}},
                {"id": "gem-1", "type": "function", "function": {"name": "g", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "gem-0.SIG", "content": "{\"v\": 2}"},
            {"role": "tool", "tool_call_id": "gem-1", "content": "[1, 2]"}]
    out = await _gem(handler).complete(_gem_req(messages=msgs, response_schema={"type": "object"}))
    c = seen["body"]["contents"]
    assert [x["role"] for x in c] == ["user", "model", "user"]
    assert c[1]["parts"][0] == {"functionCall": {"name": "f", "args": {"x": 1}}, "thoughtSignature": "SIG"}
    assert c[2]["parts"][0]["functionResponse"] == {"name": "f", "response": {"v": 2}}
    assert c[2]["parts"][1]["functionResponse"] == {"name": "g", "response": {"result": [1, 2]}}
    g = seen["body"]["generationConfig"]
    assert g["responseMimeType"] == "application/json" and g["responseJsonSchema"] == {"type": "object"}
    assert out.text == "{\"ok\": true}"  # thought parts dropped


async def test_gemini_retries_then_falls_back_and_schema_fallback():
    urls = []

    def handler(request: httpx.Request):
        urls.append(request.url.path.rsplit("/", 1)[1])
        if "3.5" in urls[-1]:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    out = await _gem(handler).complete(_gem_req())
    assert urls == ["gemini-3.5-flash:generateContent"] * 3 + ["gemini-flash-latest:generateContent"]
    assert out.text == "hi"

    urls.clear()

    def h429(request: httpx.Request):
        urls.append(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    with pytest.raises(httpx.HTTPStatusError):
        await _gem(h429).complete(_gem_req())
    assert urls == ["gemini-3.5-flash:generateContent", "gemini-flash-latest:generateContent"]

    bodies = []

    def h2(request: httpx.Request):
        bodies.append(json.loads(request.content))
        if "responseJsonSchema" in bodies[-1]["generationConfig"]:
            return httpx.Response(400, json={"error": {"message": "bad schema"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    c = _gem(h2)
    out = await c.complete(_gem_req(response_schema={"type": "object", "properties": {"a": {}}}))
    assert c.schema_fallbacks == 1 and "JSON Schema" in bodies[-1]["systemInstruction"]["parts"][0]["text"]


def test_factory_gemini_needs_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    g = make_llm("gemini", root=tmp_path)
    assert g.live and g.model == "gemini-3.5-flash" and g.client.name == "gemini"
