"""Display names for the renderer."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.events import MatchMetadata


@dataclass
class Roster:
    players: dict[str, str] = field(default_factory=dict)  # id -> full name
    teams: dict[str, str] = field(default_factory=dict)  # id -> short name
    team_full: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_metadata(cls, md: MatchMetadata) -> Roster:
        r = cls()
        for p in md.players:
            r.players[p.player_id] = p.name
        for c in (md.home, md.away):
            r.teams[c.team_id] = c.name.split()[0] if c.team_id != "KBY" else "Kestrel Bay"
            r.team_full[c.team_id] = c.name
        return r

    def player_short(self, pid: str) -> str:
        return self.players[pid].split()[-1]

    def forbidden_words(self) -> set[str]:
        words: set[str] = set()
        for n in [*self.players.values(), *self.teams.values(), *self.team_full.values()]:
            for w in n.replace("-", " ").split():
                if len(w) >= 3:
                    words.add(w.lower())
        return words
