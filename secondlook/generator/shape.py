"""Team shape: nominal player positions given the ball. Shared by the event engine and frames."""
from __future__ import annotations

import numpy as np

from ..config import PITCH_L, PITCH_W
from .roster import FORMATIONS


def to_real(direction: int, nx: float, ny: float) -> tuple[float, float]:
    if direction >= 0:
        return nx, ny
    return PITCH_L - nx, PITCH_W - ny


def to_norm(direction: int, rx: float, ry: float) -> tuple[float, float]:
    return to_real(direction, rx, ry)  # the mirror is its own inverse


def nominal_positions(formation: str, lineup: dict[str, str], direction: int,
                      ball_real: tuple[float, float], in_possession: bool
                      ) -> dict[str, tuple[float, float]]:
    bx, by = to_norm(direction, *ball_real)
    out: dict[str, tuple[float, float]] = {}
    for role, (hx, hy) in FORMATIONS[formation].items():
        if role not in lineup:  # a player has been sent off; the shape change follows
            continue
        pid = lineup[role]
        if role == "GK":
            nx, ny = hx + 0.08 * (bx - 52.5), hy + 0.1 * (by - 34)
        else:
            shift = float(np.clip(0.55 * (bx - 52.5), -24, 24))
            bonus = 6.0 if in_possession else -3.0
            if role in ("CB1", "CB2"):
                bonus *= 0.5
            nx = hx + shift + bonus
            ny = hy + 0.25 * (by - 34) * (1.0 if role not in ("RW", "LW") else 0.5)
        nx = float(np.clip(nx, 1.5, PITCH_L - 1.5))
        ny = float(np.clip(ny, 1.5, PITCH_W - 1.5))
        out[pid] = to_real(direction, nx, ny)
    return out
