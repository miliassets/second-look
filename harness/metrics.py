"""Metrics per system (design §7), computed from per-sequence rows. Pure functions."""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

SYSTEMS = ("multi", "single", "no_editor")


@dataclass
class Row:
    system: str
    seq_id: str
    group: str
    true_tag: str
    published_tag: str | None = None  # None = ended on the factual card
    proposed_claims: int = 0
    failed_claims: int = 0
    explained_profiles: list[str] = field(default_factory=list)
    latency_ms: int | None = None  # trigger -> first approved explanation overlay (wall)
    tokens: int = 0
    calls: int = 0
    correction_both_5s: bool | None = None  # correction sequences only
    survived_fault: bool | None = None  # timeout sequences only: unaffected profile explained
    fault_profile: str | None = None  # the profile whose job was delayed (fan-es)


def _pct(num: float, den: float) -> float | None:
    return None if den == 0 else 100.0 * num / den


def compute(rows: list[Row]) -> dict[str, float | None]:
    n = len(rows)
    proposed = sum(r.proposed_claims for r in rows)
    pub = [r for r in rows if r.published_tag is not None]
    corr = [r for r in rows if r.correction_both_5s is not None]
    tim = [r for r in rows if r.survived_fault is not None]
    lat = sorted(r.latency_ms for r in rows if r.latency_ms is not None)
    completed = [r for r in rows if r.explained_profiles]
    return {
        "unsupported_claims_per_100": None if proposed == 0 else
        100.0 * sum(r.failed_claims for r in rows) / proposed,
        "published_accuracy_pct": _pct(sum(1 for r in rows if r.published_tag == r.true_tag), n),
        "fallback_rate_pct": _pct(sum(1 for r in rows if not r.explained_profiles), n),
        "corrections_both_5s_pct": _pct(sum(1 for r in corr if r.correction_both_5s), len(corr)),
        "p95_latency_ms": lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat
        else None,
        "survival_under_fault_pct": _pct(sum(1 for r in tim if r.survived_fault), len(tim)),
        "tokens_per_completed_moment": None if not completed else
        sum(r.tokens for r in rows) / len(completed),
        "n": float(n), "published": float(len(pub)),
    }


def bootstrap_ci(rows: list[Row], metric: str, n_boot: int = 1000, seed: int = 1,
                 alpha: float = 0.05) -> tuple[float | None, float | None]:
    """95 % bootstrap interval, resampling sequences with replacement."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        v = compute(sample)[metric]
        if v is not None:
            vals.append(v)
    if len(vals) < 10:
        return None, None
    vals.sort()
    return vals[int(alpha / 2 * len(vals))], vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]


def summarize(rows_by_system: dict[str, list[Row]]) -> dict[str, dict]:
    out = {}
    for system, rows in rows_by_system.items():
        m = compute(rows)
        out[system] = {k: {"value": v, "ci95": list(bootstrap_ci(rows, k)) if v is not None
                           and k not in ("n", "published") else None} for k, v in m.items()}
    return out


def mean(vals):
    return statistics.mean(vals) if vals else None
