"""Invented clubs and players (synthetic-data-design.md §7). Names are invented; the
name-collision check against real players is a Champion task (S18 list)."""
from __future__ import annotations

import zlib

from ..contracts.events import Club, Player

# role -> attack-normalised home position (x from own goal 0..105, y 0..68), per formation
FORMATIONS: dict[str, dict[str, tuple[float, float]]] = {
    "4-3-3": {
        "GK": (4, 34), "RB": (30, 8), "LB": (30, 60), "CB1": (24, 26), "CB2": (24, 42),
        "DM": (40, 34), "CM": (52, 22), "AM": (52, 46), "RW": (68, 8), "LW": (68, 60),
        "ST": (78, 34),
    },
    "4-2-3-1": {
        "GK": (4, 34), "RB": (30, 8), "LB": (30, 60), "CB1": (24, 26), "CB2": (24, 42),
        "DM": (42, 24), "CM": (42, 44), "AM": (62, 34), "RW": (66, 10), "LW": (66, 58),
        "ST": (80, 34),
    },
    # KBY after the 58' shift: Rook (DM) drops deeper, leaving a gap in midfield
    "4-1-4-1": {
        "GK": (4, 34), "RB": (30, 8), "LB": (30, 60), "CB1": (24, 26), "CB2": (24, 42),
        "DM": (30, 34), "CM": (52, 22), "AM": (52, 46), "RW": (62, 8), "LW": (62, 60),
        "ST": (78, 34),
    },
    # ten men (no striker): the side that has had a striker sent off
    "4-4-1": {
        "GK": (4, 34), "RB": (28, 8), "LB": (28, 60), "CB1": (22, 26), "CB2": (22, 42),
        "DM": (38, 24), "CM": (38, 44), "AM": (58, 34), "RW": (50, 10), "LW": (50, 58),
    },
}
OUTFIELD_ROLES = ["RB", "LB", "CB1", "CB2", "DM", "CM", "AM", "RW", "LW", "ST"]
ROLES = ["GK", *OUTFIELD_ROLES]

CLUBS = {
    "KBY": Club(team_id="KBY", name="Kestrel Bay FC", short="KBY", kit_color="#1b9aa6",
                style={"press": 0.16, "pass_skill": 0.01, "forward": 0.45}),
    "IRV": Club(team_id="IRV", name="Ironvale Athletic", short="IRV", kit_color="#7a1f3d",
                style={"press": 0.12, "pass_skill": 0.0, "forward": 0.50}),
}

# (number, name, role) ; starting XI first, then bench
_KBY = [
    (1, "Sami Varto", "GK"), (2, "Dov Esker", "RB"), (3, "Hale Marrin", "LB"),
    (4, "Ivo Kassen", "CB1"), (5, "Tomas Ledran", "CB2"), (6, "Callum Rook", "DM"),
    (8, "Niko Halvard", "CM"), (10, "Oskar Brenn", "AM"), (7, "Arlo Quist", "RW"),
    (11, "Felix Marn", "LW"), (9, "Emre Tazlan", "ST"),
    (12, "Ward Tilley", "GK"), (13, "Jory Penhale", "CB1"), (14, "Orrin Bask", "RW"),
    (15, "Lio Strand", "CM"), (16, "Gus Harlow", "LW"), (17, "Pim Corvane", "DM"),
    (18, "Ansel Cray", "ST"),
]
_IRV = [
    (1, "Jonah Pell", "GK"), (2, "Cato Brevik", "RB"), (3, "Mattis Orlo", "LB"),
    (4, "Idris Sable", "CB1"), (5, "Ruben Kaltz", "CB2"), (6, "Soren Dacre", "DM"),
    (8, "Iko Mbanefo", "CM"), (10, "Teodor Vask", "AM"), (7, "Lenz Arkwright", "RW"),
    (11, "Piet Hollis", "LW"), (9, "Dario Fenn", "ST"),
    (12, "Aksel Tarn", "GK"), (13, "Dev Rooke", "CB1"), (14, "Mika Sorrel", "CM"),
    (15, "Tobias Verne", "DM"), (16, "Elio Garrick", "LW"), (17, "Yusuf Kaddar", "RW"),
    (18, "Noam Fische", "ST"),
]
STARTING = {"KBY": [p[0] for p in _KBY[:11]], "IRV": [p[0] for p in _IRV[:11]]}


def pid(team: str, number: int) -> str:
    return f"{team}-{number:02d}"


def _ratings(player_id: str, role: str) -> dict[str, float]:
    base = {"passing": 0.6, "pace": 0.6, "finishing": 0.4, "pressing": 0.5, "stamina": 0.7}
    if role == "ST":
        base.update(finishing=0.75)
    if role in ("CM", "AM", "DM"):
        base.update(passing=0.75)
    if role in ("RW", "LW"):
        base.update(pace=0.78)
    out = {}
    for k, v in base.items():
        j = (zlib.crc32(f"{player_id}:{k}".encode()) % 1000) / 1000.0  # stable jitter
        out[k] = round(min(0.95, max(0.3, v + (j - 0.5) * 0.12)), 3)
    return out


def build_players() -> list[Player]:
    players = []
    for team, rows in (("KBY", _KBY), ("IRV", _IRV)):
        for num, name, role in rows:
            i = pid(team, num)
            players.append(Player(player_id=i, team_id=team, name=name, number=num, role=role,
                                  ratings=_ratings(i, role)))
    return players


def starting_lineup(team: str, players: list[Player]) -> dict[str, str]:
    """role -> player_id for the starting XI."""
    by_id = {p.player_id: p for p in players}
    return {by_id[pid(team, n)].role: pid(team, n) for n in STARTING[team]}
