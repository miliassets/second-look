"""In-process pub/sub with a catch-up backlog (SQLite mode). Cosmos mode replaces this with a
change-feed reader (design §2.3)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class Msg:
    type: str  # meta | frame | event | overlay | score | end
    payload: Any
    profile_id: str | None = None  # None = for everyone
    at_ms: int = 0  # feed clock (global match_ms) when it was published


class Bus:
    def __init__(self) -> None:
        self.subs: list[tuple[asyncio.Queue[Msg], str | None]] = []
        self.history: list[Msg] = []  # everything except frames
        self.meta: Msg | None = None
        self.last_frame: Msg | None = None
        self.ended = False
        self.clock = lambda: 0  # the runner sets this to its feed clock

    def publish(self, type_: str, payload: Any, profile_id: str | None = None) -> None:
        m = Msg(type_, payload, profile_id, int(self.clock()))
        if type_ == "meta":
            self.meta = m
        elif type_ == "frame":
            self.last_frame = m
        else:
            self.history.append(m)
        if type_ == "end":
            self.ended = True
        for q, prof in self.subs:
            if m.profile_id is None or prof is None or m.profile_id == prof:
                q.put_nowait(m)

    def backlog(self, profile_id: str | None) -> list[Msg]:
        out: list[Msg] = []
        if self.meta:
            out.append(self.meta)
        out += [m for m in self.history if m.type != "end"
                and (m.profile_id is None or profile_id is None or m.profile_id == profile_id)]
        if self.last_frame:
            out.append(self.last_frame)
        if self.ended:
            out.append(next(m for m in reversed(self.history) if m.type == "end"))
        return out

    def subscribe(self, profile_id: str | None) -> tuple[asyncio.Queue[Msg], list[Msg]]:
        q: asyncio.Queue[Msg] = asyncio.Queue()
        backlog = self.backlog(profile_id)
        if not self.ended:
            self.subs.append((q, profile_id))
        return q, backlog

    def unsubscribe(self, q: asyncio.Queue[Msg]) -> None:
        self.subs = [(x, p) for x, p in self.subs if x is not q]
