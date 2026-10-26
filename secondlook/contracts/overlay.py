"""Overlay JSON contract (design §5.1). Published as JSON Schema for broadcasters."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

OverlayKind = Literal[
    "factual", "explanation", "correction_notice", "recap", "player_tag", "score", "answer"
]
Slot = Literal["lower_third", "top_right", "side_panel"]


class Span(BaseModel):
    start: int
    end: int
    ref: str


class ChainLayer(BaseModel):
    type: Literal["chain"] = "chain"
    points: list[tuple[float, float]]
    event_ids: list[str] = Field(default_factory=list)


class MarkerLayer(BaseModel):
    type: Literal["marker"] = "marker"
    at: tuple[float, float]
    label: str = ""
    event_id: str | None = None


class ZoneLayer(BaseModel):
    type: Literal["zone"] = "zone"
    rect: tuple[float, float, float, float]  # x, y, w, h in metres
    label_key: str = ""


class HaloLayer(BaseModel):
    type: Literal["player_halo"] = "player_halo"
    player_id: str


Layer = Annotated[
    ChainLayer | MarkerLayer | ZoneLayer | HaloLayer, Field(discriminator="type")
]


class ClaimRef(BaseModel):
    claim_id: str
    type: str
    value: float | int | str | bool | None = None


class Source(BaseModel):
    kind: Literal["live", "recorded"] = "live"
    trace_id: str = ""
    latency_ms: int | None = None


class Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    overlay_id: str
    story_id: str
    revision: int = 1
    kind: OverlayKind
    viewer_mode: Literal["analyst", "fan"]
    profile_id: str
    lang: Literal["en", "es"]
    show_at_match_ms: int
    duration_ms: int
    slot: Slot = "lower_third"
    priority: int = 1
    headline: str = ""
    text: str = ""
    spans: list[Span] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[ClaimRef] = Field(default_factory=list)
    supersedes: str | None = None
    source: Source = Field(default_factory=Source)
