"""2 Hz tracking frames derived from the event stream (design S3).

Players move toward role-based target positions that shift with the ball, at a capped speed.
"""
from __future__ import annotations

import numpy as np

from ..config import FRAME_HZ, MATCH_ID, PITCH_L, PITCH_W
from ..contracts.events import Event, Frame, PlayerPos
from .roster import starting_lineup
from .shape import nominal_positions

DIRECTION = {"KBY": -1, "IRV": 1}  # period 2
DIRECTION_P1 = {"KBY": 1, "IRV": -1}
FOLLOW_GAIN = 0.4  # smoothing: off-ball players ease toward their targets
VMAX = 7.5  # m/s (27 km/h); sprint scripting arrives with the full-match generator
ON_BALL = {"pass", "carry", "dribble", "cross", "shot", "corner", "free_kick", "throw_in",
           "goal_kick", "kickoff"}


def _ball_knots(events: list[Event]) -> list[tuple[int, float, float]]:
    knots: list[tuple[int, float, float]] = []
    for e in events:
        if e.x is None or e.y is None:
            continue
        knots.append((e.match_clock_ms, e.x, e.y))
        if e.end_x is not None and e.type in ON_BALL:
            dur = 900 if e.type == "shot" else 1500
            if e.qualifiers.get("length_m"):
                dur = int(1000 + 60 * float(e.qualifiers["length_m"]))
            knots.append((e.match_clock_ms + dur, e.end_x, e.end_y))
    knots.sort(key=lambda k: k[0])
    return knots


def build_frames(events: list[Event], players, start_ms: int, end_ms: int, seed: int,
                 period: int = 2) -> list[Frame]:
    rng = np.random.Generator(np.random.PCG64(seed + 1))
    by_id = {p.player_id: p for p in players}
    dirs = DIRECTION_P1 if period == 1 else DIRECTION
    lineup = {t: starting_lineup(t, players) for t in DIRECTION}
    formation = {"KBY": "4-3-3", "IRV": "4-2-3-1"}
    boost: dict[str, tuple[int, float]] = {}  # player -> (until clock_ms, vmax m/s)

    def apply_state(e):
        if e.type == "substitution":
            role = by_id[e.qualifiers["on"]].role
            lineup[e.team_id][role] = e.qualifiers["on"]
        elif e.type == "tactical_shift":
            formation[e.team_id] = e.qualifiers["formation_to"]
        elif e.type == "lineup":
            formation[e.team_id] = e.qualifiers["formation"]
        elif e.type == "card" and e.outcome in ("red", "second_yellow"):
            for role, pid_ in list(lineup[e.team_id].items()):
                if pid_ == e.player_id:
                    del lineup[e.team_id][role]
    for e in events:
        if e.period < period:
            apply_state(e)
    evs = [e for e in events if e.period == period]
    knots = _ball_knots(evs)
    kt = np.array([k[0] for k in knots], dtype=float)
    kx = np.array([k[1] for k in knots])
    ky = np.array([k[2] for k in knots])
    ei = 0
    poss_team, holder = "KBY", None
    pos: dict[str, tuple[float, float]] = {}
    frames: list[Frame] = []
    step_ms = 1000 // FRAME_HZ
    dt = step_ms / 1000.0
    for t in range(start_ms, end_ms + 1, step_ms):
        while ei < len(evs) and evs[ei].match_clock_ms <= t:
            e = evs[ei]
            ei += 1
            apply_state(e)
            if e.type == "carry" and e.qualifiers.get("sprint_kmh"):
                dur = int(e.qualifiers.get("duration_ms", 4000))
                boost[e.player_id] = (e.match_clock_ms + dur + 500,
                                      e.qualifiers["sprint_kmh"] / 3.6 * 1.005)
            if e.type == "possession_change" or e.type == "kickoff":
                poss_team = e.team_id
            if e.type in ON_BALL and e.player_id:
                poss_team, holder = e.team_id, e.player_id
                if e.type == "pass" and e.outcome == "complete" and e.qualifiers.get("recipient"):
                    holder = e.qualifiers["recipient"]
            elif e.type == "possession_change" and e.player_id:
                holder = e.player_id
        bx = float(np.interp(t, kt, kx)) if len(kt) else PITCH_L / 2
        by = float(np.interp(t, kt, ky)) if len(kt) else PITCH_W / 2
        ball = (bx, by)
        targets: dict[str, tuple[float, float]] = {}
        for team in DIRECTION:
            nom = nominal_positions(formation[team], lineup[team], dirs[team], ball,
                                    poss_team == team)
            for p, (x, y) in nom.items():
                targets[p] = (x + rng.normal(0, 0.05), y + rng.normal(0, 0.05))
        if holder in targets:
            targets[holder] = ball
        for p, (tx, ty) in targets.items():
            if p not in pos:
                pos[p] = (tx, ty)
                continue
            px, py = pos[p]
            gain = 1.0 if p == holder else FOLLOW_GAIN
            dx, dy = (tx - px) * gain, (ty - py) * gain
            d = float(np.hypot(dx, dy))
            minute = (45 if period == 2 else 0) + t / 60_000
            vmax = VMAX * (1 - 0.003 * max(0.0, minute - 55))  # late-match fatigue
            b = boost.get(p)
            if b and t <= b[0]:
                vmax = b[1]
            cap = vmax * dt
            if d > cap:
                dx, dy = dx * cap / d, dy * cap / d
            pos[p] = (min(max(px + dx, 0.0), PITCH_L), min(max(py + dy, 0.0), PITCH_W))
        on_pitch = set(targets)
        for gone in [p for p in pos if p not in on_pitch]:
            del pos[gone]
        frames.append(Frame(
            match_id=MATCH_ID, period=period, match_clock_ms=t,
            players=[PlayerPos(player_id=p, x=round(pos[p][0], 2), y=round(pos[p][1], 2))
                     for p in sorted(pos)],
            ball=(round(bx, 2), round(by, 2))))
    return frames



