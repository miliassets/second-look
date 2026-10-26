"""The 30 held-out sequences: never used in prompts or fixtures (seeds 7_000_001+)."""
from __future__ import annotations

from dataclasses import dataclass

from secondlook.generator import GeneratedMatch
from secondlook.generator.sequences import generate_sequence

BASE_SEED = 7_000_000


@dataclass(frozen=True)
class SeqSpec:
    seq_id: str
    group: str  # clean | misleading | correction | timeout
    family: str
    seed: int
    scorer_team: str
    correction_delay_ms: int | None = None

    def build(self) -> GeneratedMatch:
        return generate_sequence(self.seed, self.family, self.scorer_team,
                                 self.correction_delay_ms)


def build_specs() -> list[SeqSpec]:
    specs: list[SeqSpec] = []
    n = 0

    def add(group, family, delay=None):
        nonlocal n
        n += 1
        specs.append(SeqSpec(f"s{n:02d}-{group}", group, family, BASE_SEED + n,
                             "IRV" if n % 2 else "KBY", delay))
    clean = ["pressing", "chain", "counter"]
    for i in range(10):
        add("clean", clean[i % 3])
    for i in range(10):
        add("misleading", "misleading_press" if i % 2 else "misleading_counter")
    for i in range(5):  # correction 10-40 s after the story airs
        add("correction", clean[i % 3], 10_000 + i * 7_500)
    for i in range(5):
        add("timeout", clean[(i + 1) % 3])
    return specs
