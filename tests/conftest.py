import socket
import threading
import time

import pytest
import uvicorn

from secondlook.checker.roster import Roster
from secondlook.checker.verify import Checker
from secondlook.contracts.events import Delivery
from secondlook.generator import GeneratedMatch, generate_slice
from secondlook.generator.corrections import CorrectionSpec
from secondlook.generator.engine import PERIODS
from secondlook.ledger.ingest import Ingestor
from secondlook.ledger.sqlite_store import SQLiteStore
from secondlook.timeline import to_match_ms

SEED = 20261024
P2 = PERIODS[0]["length_ms"]


def ms(e):
    return to_match_ms(e.period, e.match_clock_ms, PERIODS)


def build(match: GeneratedMatch, skip_seqs=(), until_seq=None):
    store = SQLiteStore(":memory:", "m001")
    ing = Ingestor(store, PERIODS)
    last = 0
    for i, e in enumerate(match.events):
        if e.seq in skip_seqs or (until_seq and e.seq > until_seq):
            continue
        ing.ingest(Delivery(event=e, arrival_order=i, arrival_match_ms=ms(e)))
        last = ms(e)
    return store, ing, last


class World:
    """A fully ingested clean slice with a checker. Shared, read-only."""

    def __init__(self, corrections=None):
        self.match = generate_slice(SEED, corrections=corrections)
        self.store, self.ing, self.last_ms = build(self.match)
        self.checker = Checker(self.store, PERIODS, self.match.frames)
        self.roster = Roster.from_metadata(self.match.metadata)
        self.goal = next(e for e in self.match.events if e.type == "goal")
        self.shot = next(e for e in self.match.events if e.event_id == self.goal.related_ids[0])
        self.now = self.last_ms


@pytest.fixture(scope="module")
def world():
    return World()


@pytest.fixture
def fresh_world():
    return World()


@pytest.fixture
def corrected_world():
    return World(corrections=[CorrectionSpec("void_goal")])


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def serve():
    """Start the real app in a thread. Usage: url = serve(cfg, autostart=False, setup=None)."""
    servers = []

    def _serve(cfg, autostart=True, setup=None):
        from secondlook.api.app import create_app
        port = free_port()
        app = create_app(cfg, autostart=autostart, setup=setup)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error"))
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        servers.append((server, th))
        return f"http://127.0.0.1:{port}"

    yield _serve
    for server, th in servers:
        server.should_exit = True
        th.join(timeout=5)
