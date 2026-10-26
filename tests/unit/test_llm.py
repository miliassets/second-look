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
