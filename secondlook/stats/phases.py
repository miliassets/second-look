"""Phase labels from deterministic rules (thresholds UNVERIFIED; tuned on generator output)."""
from __future__ import annotations

from .. import config
from .engine import StatsEngine


def phase_label(stats: StatsEngine, team: str, from_ms: int, to_ms: int) -> str:
    share = stats.rate("possession_share", team, from_ms, to_ms).value
    comp = stats.rate("pass_completion", team, from_ms, to_ms).value
    minutes = max((to_ms - from_ms) / 60_000.0, 1e-9)
    changes = stats.count("possession_changes", None, from_ms, to_ms).value / minutes
    if (share is not None and comp is not None and share >= config.PHASE_CONTROL_POSSESSION
            and comp >= config.PHASE_CONTROL_COMPLETION):
        return "control"
    if comp is not None and changes >= config.PHASE_CHAOS_CHANGES_PER_MIN \
            and comp < config.PHASE_CHAOS_COMPLETION:
        return "chaos"
    return "even"


def phase_timeline(stats: StatsEngine, team: str, from_ms: int, to_ms: int,
                   win_ms: int = config.PHASE_WINDOW_MS, step_ms: int = config.PHASE_STEP_MS
                   ) -> list[tuple[int, str]]:
    """(window_end_ms, label) for sliding windows that fit inside [from_ms, to_ms]."""
    out = []
    t = from_ms + win_ms
    while t <= to_ms:
        out.append((t, phase_label(stats, team, t - win_ms, t)))
        t += step_ms
    return out


def phase_changes(timeline: list[tuple[int, str]], hold_ms: int = config.PHASE_WINDOW_MS,
                  step_ms: int = config.PHASE_STEP_MS) -> list[tuple[int, str]]:
    """Points where the label changed and then held for `hold_ms`. Returns (confirmed_at, label)."""
    need = max(1, hold_ms // step_ms)
    out: list[tuple[int, str]] = []
    current = timeline[0][1] if timeline else None
    i = 1
    while i < len(timeline):
        label = timeline[i][1]
        if label != current and i + need <= len(timeline) and \
                all(lbl == label for _, lbl in timeline[i:i + need]):
            out.append((timeline[i + need - 1][0], label))
            current = label
            i += need
        else:
            i += 1
    return out
