"""Feed player: emits deliveries and frames at 1x / 10x / max (design §8.2)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from ..contracts.events import Delivery, Frame
from ..timeline import to_match_ms

SPEEDS = {"1x": 1.0, "4x": 4.0, "10x": 10.0, "max": 0.0}


@dataclass
class FeedItem:
    at_ms: int  # global match_ms at which it is emitted
    kind: str  # "event" | "frame"
    delivery: Delivery | None = None
    frame: Frame | None = None


def merge(deliveries: list[Delivery], frames: list[Frame], periods: list[dict[str, int]]
          ) -> list[FeedItem]:
    items = [FeedItem(d.arrival_match_ms, "event", delivery=d) for d in deliveries]
    items += [FeedItem(to_match_ms(f.period, f.match_clock_ms, periods), "frame", frame=f)
              for f in frames]
    order = {"event": 0, "frame": 1}
    # stable: events keep arrival order; a frame at the same instant follows events
    indexed = list(enumerate(items))
    indexed.sort(key=lambda p: (p[1].at_ms, order[p[1].kind], p[0]))
    return [it for _, it in indexed]


async def play(items: list[FeedItem], speed: str | float = "max",
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
               ) -> AsyncIterator[FeedItem]:
    factor = SPEEDS[speed] if isinstance(speed, str) else float(speed)
    prev: int | None = None
    for it in items:
        if factor > 0 and prev is not None and it.at_ms > prev:
            await sleep((it.at_ms - prev) / 1000.0 / factor)
        prev = it.at_ms if prev is None else max(prev, it.at_ms)
        yield it
