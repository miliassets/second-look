"""Event, frame and metadata models (synthetic-data-design.md §3, §4)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "match_metadata", "lineup", "kickoff", "period_start", "period_end", "match_end",
    "stoppage_added",
    "pass", "carry", "dribble", "cross", "shot", "goal", "own_goal",
    "tackle", "interception", "block", "clearance", "save", "claim",
    "pressure", "possession_change",
    "throw_in", "corner", "free_kick", "goal_kick", "penalty",
    "foul", "card", "offside",
    "substitution", "injury_stoppage", "tactical_shift",
    "event_correction",
]

Outcome = Literal[
    "complete", "incomplete", "won", "lost", "on_target", "off_target", "saved", "blocked",
    "scored", "forced_turnover", "forced_back", "none", "yellow", "second_yellow", "red",
]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    match_id: str
    seq: int
    type: EventType
    period: int
    match_clock_ms: int
    wall_ts: str | None = None
    team_id: str | None = None
    player_id: str | None = None
    possession_id: int | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    under_pressure: bool = False
    related_ids: list[str] = Field(default_factory=list)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "1.0"


class PlayerPos(BaseModel):
    player_id: str
    x: float
    y: float


class Frame(BaseModel):
    """Tracking frame, 2 Hz. 22 players and the ball."""

    model_config = ConfigDict(extra="forbid")

    match_id: str
    period: int
    match_clock_ms: int
    players: list[PlayerPos]
    ball: tuple[float, float]


class Player(BaseModel):
    player_id: str
    team_id: str
    name: str
    number: int
    role: str
    ratings: dict[str, float] = Field(default_factory=dict)


class Club(BaseModel):
    team_id: str
    name: str
    short: str
    kit_color: str
    style: dict[str, float] = Field(default_factory=dict)


class MatchMetadata(BaseModel):
    match_id: str
    seed: int
    home: Club
    away: Club
    players: list[Player]
    venue: str
    referee: str
    periods: list[dict[str, int]]  # [{"period":1,"length_ms":...}, ...]
    label: str = "Synthetic match"


class Delivery(BaseModel):
    """An event as delivered by the (possibly messy) feed."""

    event: Event
    arrival_order: int
    arrival_match_ms: int
    kind: Literal["normal", "late", "duplicate"] = "normal"
