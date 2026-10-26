"""Tool specs handed to the model (JSON Schema), matching matchstate/tools.py."""
from __future__ import annotations

from ..agents.llm import ToolSpec

_TOK = {"moment_token": {"type": "string"}}
_WIN = {"from_ms": {"type": "integer"}, "to_ms": {"type": "integer"}}


def _t(name, desc, props, required):
    return ToolSpec(name=name, description=desc, parameters={
        "type": "object", "properties": {**_TOK, **props},
        "required": ["moment_token", *required]})


TOOL_SPECS: dict[str, ToolSpec] = {s.name: s for s in [
    _t("get_dossier", "Pre-computed facts for the moment, each already a claim object.", {}, []),
    _t("get_events", "Events in a window (max 200).", {
        **_WIN, "types": {"type": "array", "items": {"type": "string"}},
        "team_id": {"type": "string"}, "player_id": {"type": "string"},
        "limit": {"type": "integer"}}, ["from_ms", "to_ms"]),
    _t("get_event", "One event by id.", {"event_id": {"type": "string"}}, ["event_id"]),
    _t("get_possession", "A possession chain.", {"possession_id": {"type": "integer"}},
       ["possession_id"]),
    _t("get_stat", "A count, rate or physical metric.", {
        "metric": {"type": "string"}, **_WIN, "team_id": {"type": "string"},
        "player_id": {"type": "string"}}, ["metric", "from_ms", "to_ms"]),
    _t("compare_windows", "Same metric over two windows.", {
        "metric": {"type": "string"}, "team_id": {"type": "string"},
        "window_a": {"type": "array", "items": {"type": "integer"}},
        "window_b": {"type": "array", "items": {"type": "integer"}}},
       ["metric", "team_id", "window_a", "window_b"]),
    _t("get_physical", "Top speed, distance, sprints.", {
        "player_id": {"type": "string"}, **_WIN}, ["player_id", "from_ms", "to_ms"]),
    _t("get_score", "Score at a time (default now).", {"at_ms": {"type": "integer"}}, []),
    _t("verify_claim", "Ask the checker if a claim holds.", {"claim": {"type": "object"}},
       ["claim"]),
]}
