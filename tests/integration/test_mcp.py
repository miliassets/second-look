import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from secondlook.runner import RunConfig


async def _wait(url, serve_holder):
    for _ in range(200):
        r = serve_holder.get("runner")
        if r and r.done.is_set():
            return r
        await asyncio.sleep(0.1)
    raise AssertionError("run did not finish")


async def test_tools_through_a_real_mcp_client_session(serve):
    holder = {}

    def setup(runner):
        holder["runner"] = runner
    url = serve(RunConfig(speed="max"), autostart=True, setup=setup)
    runner = await _wait(url, holder)
    tok = runner.tools.by_moment["mo-m001-067-goal"]
    async with streamable_http_client(url + "/mcp/") as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"get_dossier", "get_events", "get_event", "get_possession", "get_stat",
                    "compare_windows", "get_physical", "get_score", "verify_claim"} <= names
            r = await s.call_tool("get_score", {"moment_token": tok})
            data = r.structuredContent or {}
            assert data["home"]["goals"] == 1 and data["away"]["goals"] == 1
            d = await s.call_tool("get_dossier", {"moment_token": tok})
            assert any(f["type"] == "chain" for f in d.structuredContent["facts"])
            bad = await s.call_tool("get_score", {"moment_token": "tok-nope"})
            assert bad.isError
