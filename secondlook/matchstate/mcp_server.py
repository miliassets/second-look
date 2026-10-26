"""FastMCP wrapper over MatchTools, mounted at /mcp (streamable HTTP)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .tools import MatchTools, ToolError


def build_mcp(get_tools: Callable[[], MatchTools | None]) -> FastMCP:
    mcp = FastMCP(
        "second-look-match-state",
        instructions="Read-only match-state tools. Every call needs a moment_token; results never "
                     "include events after the moment's now.",
        streamable_http_path="/", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def tools() -> MatchTools:
        t = get_tools()
        if t is None:
            raise ToolError("match not started")
        return t

    @mcp.tool()
    def get_dossier(moment_token: str) -> dict[str, Any]:
        """Pre-computed facts for the moment's window, each already a claim object."""
        return tools().get_dossier(moment_token)

    @mcp.tool()
    def get_events(moment_token: str, from_ms: int, to_ms: int, types: list[str] | None = None,
                   team_id: str | None = None, player_id: str | None = None,
                   limit: int = 200) -> dict[str, Any]:
        """Events in a window (clamped to the moment's now). At most 200."""
        return tools().get_events(moment_token, from_ms, to_ms, types, team_id, player_id, limit)

    @mcp.tool()
    def get_event(moment_token: str, event_id: str) -> dict[str, Any]:
        """One event by id."""
        return tools().get_event(moment_token, event_id)

    @mcp.tool()
    def get_possession(moment_token: str, possession_id: int) -> dict[str, Any]:
        """A possession chain: events, pass count, duration."""
        return tools().get_possession(moment_token, possession_id)

    @mcp.tool()
    def get_stat(moment_token: str, metric: str, from_ms: int, to_ms: int,
                 team_id: str | None = None, player_id: str | None = None) -> dict[str, Any]:
        """A count, rate or physical metric with its evidence."""
        return tools().get_stat(moment_token, metric, from_ms, to_ms, team_id, player_id)

    @mcp.tool()
    def compare_windows(moment_token: str, metric: str, team_id: str, window_a: list[int],
                        window_b: list[int]) -> dict[str, Any]:
        """The same metric over two windows, and the delta."""
        return tools().compare_windows(moment_token, metric, team_id, window_a, window_b)

    @mcp.tool()
    def get_physical(moment_token: str, player_id: str, from_ms: int, to_ms: int
                     ) -> dict[str, Any]:
        """Top speed, distance and sprint count from tracking frames."""
        return tools().get_physical(moment_token, player_id, from_ms, to_ms)

    @mcp.tool()
    def get_score(moment_token: str, at_ms: int | None = None) -> dict[str, Any]:
        """The score at a time (default: now)."""
        return tools().get_score(moment_token, at_ms)

    @mcp.tool()
    def verify_claim(moment_token: str, claim: dict[str, Any]) -> dict[str, Any]:
        """Ask the checker whether a claim holds, before proposing it."""
        return tools().verify_claim(moment_token, claim)

    return mcp
