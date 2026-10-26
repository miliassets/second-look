"""Claim vocabulary (design §3.5). The checker computes its own value."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimType = Literal[
    "event_fact", "count", "rate", "chain", "gap_seconds", "physical", "compare", "phase",
    "score", "involved",
]
ClaimStatus = Literal["proposed", "verified", "failed", "unknown", "invalidated"]

COUNT_METRICS = (
    "passes", "completed_passes", "recoveries", "recoveries_att_third", "pressures",
    "forced_turnovers", "shots", "possession_changes", "fouls", "corners",
)
RATE_METRICS = ("pass_completion", "possession_share", "press_success")
PHYS_METRICS = ("top_speed_kmh", "distance_m", "sprints")
PHASE_LABELS = ("control", "chaos", "even")


class Claim(BaseModel):
    claim_id: str
    type: ClaimType
    params: dict[str, Any] = Field(default_factory=dict)
    value: Any = None  # what the agent asserts; the checker compares with its own


class ClaimRecord(BaseModel):
    claim_id: str
    owner_kind: str = "candidate"
    owner_id: str = ""
    type: str  # a plain str: the checker must be able to record claims of an unknown type
    params: dict[str, Any] = Field(default_factory=dict)
    value: Any = None  # computed by the checker
    asserted: Any = None  # what the agent asserted (kept so the publisher can re-check)
    status: ClaimStatus = "proposed"
    checked_rev: int = 0
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
