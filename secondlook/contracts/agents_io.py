"""Structured inputs and outputs for the agents (design §3.4)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .claims import Claim

ThesisTag = Literal[
    "pressing_recovery", "sustained_possession", "direct_play_chaos", "tactical_shift",
    "fatigue", "individual_action", "set_piece", "even_rhythm", "correction",
]
MomentKind = Literal[
    "goal", "disallowed_goal", "fast_shot", "card", "tactical_shift", "phase_change",
    "half_time", "full_time", "correction", "question",
]
ScoutName = Literal["pressure", "rhythm", "player", "system"]


class Moment(BaseModel):
    moment_id: str
    kind: MomentKind
    trigger_event_id: str | None = None
    window_from_ms: int
    window_to_ms: int
    now_ms: int
    as_of_rev: int
    status: Literal["open", "published", "fallback", "reopened"] = "open"
    team_id: str | None = None
    detail: dict = Field(default_factory=dict)  # e.g. {"label": "control"} for phase changes


class Candidate(BaseModel):
    candidate_id: str = ""
    moment_id: str = ""
    scout: ScoutName
    thesis_tag: ThesisTag
    thesis: str
    claims: list[Claim] = Field(default_factory=list)
    status: Literal["proposed", "rejected", "chosen", "invalidated"] = "proposed"
    as_of_rev: int = 0


class ScoutOutput(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list, max_length=2)


class EvidenceRequest(BaseModel):
    to_scout: ScoutName
    question: str


class Rejection(BaseModel):
    candidate_id: str
    reason: str


class Verdict(BaseModel):
    verdict_id: str = ""
    moment_id: str = ""
    chosen_candidate_id: str | None = None
    rejected: list[Rejection] = Field(default_factory=list)
    evidence_request: EvidenceRequest | None = None
    notes: list[str] = Field(default_factory=list)  # e.g. "pressure: timed out"
    as_of_rev: int = 0


class Story(BaseModel):
    story_id: str
    revision: int = 1
    moment_id: str
    verdict_id: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    text_en: str  # template: placeholders only, no raw digits
    thesis_tag: ThesisTag | None = None
    status: Literal["approved", "superseded", "invalidated"] = "approved"
    supersedes_revision: int | None = None


class EditorOutput(BaseModel):
    verdict: Verdict
    story: Story | None = None


class ViewerOutput(BaseModel):
    output_id: str = ""
    story_id: str
    story_revision: int
    profile_id: str
    lang: Literal["en", "es"]
    template: str
    headline: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)  # layer kinds chosen: chain, marker, ...
    status: Literal["proposed", "approved", "invalidated"] = "proposed"


class Answer(BaseModel):
    template: str
    claim_ids: list[str] = Field(default_factory=list)
    supported: bool = True


class EditorLLMOutput(BaseModel):
    """What the Editor model returns; code turns it into a Verdict and a Story."""
    chosen_candidate_id: str | None = None
    rejected: list[Rejection] = Field(default_factory=list)
    evidence_request: EvidenceRequest | None = None
    claim_ids: list[str] = Field(default_factory=list)
    text_en: str = ""


class ProducerLLMOutput(BaseModel):
    template: str = ""
    headline: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    layers: list[Literal["chain", "marker", "zone", "player_halo"]] = Field(default_factory=list)


class AskLLMOutput(BaseModel):
    supported: bool = False
    claims: list[Claim] = Field(default_factory=list)
    template: str = ""
