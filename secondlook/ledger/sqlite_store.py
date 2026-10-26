"""SQLite implementation of LedgerStore (WAL when file-backed)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..contracts.agents_io import Candidate, Moment, Story, Verdict, ViewerOutput
from ..contracts.claims import ClaimRecord
from ..contracts.events import Event
from ..contracts.overlay import Overlay
from ..contracts.prefs import ViewerProfile
from .store import Gap, Revision, StoredEvent

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

# kind -> (table, model, id getter, extra column getters)
_KINDS: dict[str, tuple[str, type[BaseModel], Any, dict[str, Any]]] = {
    "moment": ("moments", Moment, lambda m: m.moment_id, {"status": lambda m: m.status}),
    "candidate": ("candidates", Candidate, lambda m: m.candidate_id,
                  {"moment_id": lambda m: m.moment_id, "status": lambda m: m.status}),
    "claim": ("claims", ClaimRecord, lambda m: m.claim_id,
              {"owner_kind": lambda m: m.owner_kind, "owner_id": lambda m: m.owner_id,
               "status": lambda m: m.status, "checked_rev": lambda m: m.checked_rev,
               "reason": lambda m: m.reason}),
    "verdict": ("verdicts", Verdict, lambda m: m.verdict_id,
                {"moment_id": lambda m: m.moment_id, "status": lambda m: "ruled"}),
    "story": ("stories", Story, lambda m: f"{m.story_id}:{m.revision}",
              {"story_id": lambda m: m.story_id, "revision": lambda m: m.revision,
               "moment_id": lambda m: m.moment_id, "status": lambda m: m.status}),
    "viewer_output": ("viewer_outputs", ViewerOutput, lambda m: m.output_id,
                      {"story_id": lambda m: m.story_id,
                       "story_revision": lambda m: m.story_revision,
                       "profile_id": lambda m: m.profile_id, "status": lambda m: m.status}),
    "overlay": ("overlays", Overlay, lambda m: m.overlay_id,
                {"story_id": lambda m: m.story_id, "revision": lambda m: m.revision,
                 "profile_id": lambda m: m.profile_id, "kind": lambda m: m.kind,
                 "status": lambda m: "live"}),
    "viewer_profile": ("viewer_profiles", ViewerProfile, lambda m: m.profile_id, {}),
}
# derived rows whose pydantic model carries a status that set_status must keep in sync
_STATUS_IN_MODEL = {"moment", "candidate", "claim", "story", "viewer_output"}


class SQLiteStore:
    def __init__(self, path: str = ":memory:", match_id: str = "m001"):
        self.match_id = match_id
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ----- revisions
    def current_rev(self) -> int:
        with self.lock:
            r = self.db.execute("SELECT COALESCE(MAX(rev),0) FROM revisions").fetchone()
            return int(r[0])

    def next_rev(self, kind, event_id=None, at_match_ms=0) -> int:
        with self.lock:
            rev = self.current_rev() + 1
            self.db.execute("INSERT INTO revisions VALUES (?,?,?,?,?)",
                            (rev, self.match_id, kind, event_id, at_match_ms))
            self.db.commit()
            return rev

    def revisions(self) -> list[Revision]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM revisions ORDER BY rev").fetchall()
        return [Revision(rev=r["rev"], kind=r["kind"], event_id=r["event_id"],
                         at_match_ms=r["at_match_ms"]) for r in rows]

    # ----- events
    def has_event(self, event_id) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM events WHERE event_id=?",
                                   (event_id,)).fetchone() is not None

    def append_event(self, se: StoredEvent) -> None:
        e = se.event
        with self.lock:
            self.db.execute(
                "INSERT INTO events (event_id, match_id, seq, type, period, match_clock_ms, "
                "match_ms, team_id, player_id, possession_id, payload, arrival_order, "
                "arrival_match_ms, status, corrected_by, rev) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.event_id, self.match_id, e.seq, e.type, e.period, e.match_clock_ms,
                 se.match_ms, e.team_id, e.player_id, e.possession_id, e.model_dump_json(),
                 se.arrival_order, se.arrival_match_ms, se.status, se.corrected_by, se.rev))
            self.db.commit()

    def apply_correction(self, target_id, status, corrected_by, rev) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE events SET status=?, corrected_by=?, corrected_rev=? WHERE event_id=?",
                (status, corrected_by, rev, target_id))
            self.db.commit()

    @staticmethod
    def _row_event(r, as_of_rev) -> StoredEvent:
        status = r["status"]
        corrected_by = r["corrected_by"]
        if as_of_rev is not None and r["corrected_rev"] is not None and r["corrected_rev"] > as_of_rev:
            status, corrected_by = "active", None
        return StoredEvent(event=Event.model_validate_json(r["payload"]), match_ms=r["match_ms"],
                           arrival_order=r["arrival_order"],
                           arrival_match_ms=r["arrival_match_ms"], status=status,
                           corrected_by=corrected_by, rev=r["rev"])

    def get_event(self, event_id, as_of_rev=None) -> StoredEvent | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if r is None or (as_of_rev is not None and r["rev"] > as_of_rev):
            return None
        return self._row_event(r, as_of_rev)

    def get_events(self, from_ms=None, to_ms=None, types=None, team_id=None, player_id=None,
                   as_of_rev=None, statuses=("active",)) -> list[StoredEvent]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if from_ms is not None:
            q += " AND match_ms >= ?"
            args.append(from_ms)
        if to_ms is not None:
            q += " AND match_ms <= ?"
            args.append(to_ms)
        if types:
            q += f" AND type IN ({','.join('?' * len(types))})"
            args += types
        if team_id:
            q += " AND team_id=?"
            args.append(team_id)
        if player_id:
            q += " AND player_id=?"
            args.append(player_id)
        if as_of_rev is not None:
            q += " AND rev <= ?"
            args.append(as_of_rev)
        q += " ORDER BY match_ms, seq, rowid"
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        out = [self._row_event(r, as_of_rev) for r in rows]
        return [s for s in out if s.status in statuses]

    # ----- feed health
    def open_gap(self, from_seq, to_seq, rev, at_ms) -> None:
        with self.lock:
            self.db.execute("INSERT INTO feed_gaps (match_id, from_seq, to_seq, opened_rev, opened_ms)"
                            " VALUES (?,?,?,?,?)", (self.match_id, from_seq, to_seq, rev, at_ms))
            self.db.commit()

    def close_gap(self, seq, rev) -> bool:
        with self.lock:
            cur = self.db.execute(
                "UPDATE feed_gaps SET closed_rev=? WHERE closed_rev IS NULL "
                "AND from_seq<=? AND to_seq>=?", (rev, seq, seq))
            self.db.commit()
            return cur.rowcount > 0

    def gaps(self, open_only=False) -> list[Gap]:
        q = "SELECT * FROM feed_gaps" + (" WHERE closed_rev IS NULL" if open_only else "")
        with self.lock:
            rows = self.db.execute(q + " ORDER BY gap_id").fetchall()
        return [Gap(from_seq=r["from_seq"], to_seq=r["to_seq"], opened_rev=r["opened_rev"],
                    closed_rev=r["closed_rev"]) for r in rows]

    def bump_stat(self, name, by=1) -> None:
        with self.lock:
            self.db.execute("INSERT INTO feed_stats VALUES (?,?) ON CONFLICT(name) "
                            "DO UPDATE SET value=value+?", (name, by, by))
            self.db.commit()

    def stat(self, name) -> int:
        with self.lock:
            r = self.db.execute("SELECT value FROM feed_stats WHERE name=?", (name,)).fetchone()
        return int(r[0]) if r else 0

    # ----- derived rows
    def put(self, kind, model, **cols) -> None:
        table, _, idf, extra = _KINDS[kind]
        values = {"id": idf(model), "match_id": self.match_id,
                  **{c: f(model) for c, f in extra.items()}, **cols,
                  "data": model.model_dump_json()}
        names = list(values)
        with self.lock:
            if table == "overlays":
                # keep the cursor stable across re-puts; new ids get the next cursor
                exists = self.db.execute("SELECT 1 FROM overlays WHERE id=?",
                                         (values["id"],)).fetchone()
                if exists:
                    sets = ",".join(f"{n}=?" for n in names if n != "id")
                    self.db.execute(f"UPDATE overlays SET {sets} WHERE id=?",
                                    [values[n] for n in names if n != "id"] + [values["id"]])
                    self.db.commit()
                    return
            self.db.execute(
                f"INSERT OR REPLACE INTO {table} ({','.join(names)}) "
                f"VALUES ({','.join('?' * len(names))})", [values[n] for n in names])
            self.db.commit()

    def _load(self, kind, row) -> BaseModel:
        _, model, _, _ = _KINDS[kind]
        m = model.model_validate_json(row["data"])
        if kind in _STATUS_IN_MODEL and "status" in row.keys() and row["status"]:
            m = m.model_copy(update={"status": row["status"]})
        if kind == "claim":
            m = m.model_copy(update={"reason": row["reason"] or "",
                                     "checked_rev": row["checked_rev"] or 0})
        return m

    def get(self, kind, id) -> BaseModel | None:
        table = _KINDS[kind][0]
        with self.lock:
            r = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
        return self._load(kind, r) if r else None

    def list_kind(self, kind, /, **filters) -> list[BaseModel]:
        table = _KINDS[kind][0]
        q, args = f"SELECT * FROM {table}", []
        if filters:
            q += " WHERE " + " AND ".join(f"{k}=?" for k in filters)
            args = list(filters.values())
        order = "cursor" if table == "overlays" else "rowid"
        with self.lock:
            rows = self.db.execute(f"{q} ORDER BY {order}", args).fetchall()
        return [self._load(kind, r) for r in rows]

    def set_status(self, kind, id, status, **fields) -> None:
        table = _KINDS[kind][0]
        sets = ["status=?"] + [f"{k}=?" for k in fields]
        with self.lock:
            self.db.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id=?",
                            [status, *fields.values(), id])
            self.db.commit()

    # ----- dependency graph
    def add_deps(self, edges) -> None:
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO deps VALUES (?,?,?,?)", edges)
            self.db.commit()

    def children(self, kind, id) -> list[tuple[str, str]]:
        with self.lock:
            rows = self.db.execute("SELECT child_kind, child_id FROM deps WHERE parent_kind=? "
                                   "AND parent_id=?", (kind, id)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def parents(self, kind, id) -> list[tuple[str, str]]:
        with self.lock:
            rows = self.db.execute("SELECT parent_kind, parent_id FROM deps WHERE child_kind=? "
                                   "AND child_id=?", (kind, id)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def add_claim_event_deps(self, claim_id, event_ids) -> None:
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO claim_event_deps VALUES (?,?)",
                                [(claim_id, e) for e in event_ids])
            self.db.commit()

    def add_claim_window_dep(self, claim_id, team_id, event_types, from_ms, to_ms) -> None:
        with self.lock:
            self.db.execute("INSERT INTO claim_window_deps VALUES (?,?,?,?,?)",
                            (claim_id, team_id, json.dumps(sorted(event_types)), from_ms, to_ms))
            self.db.commit()

    def claims_touching(self, event_ids, points) -> list[str]:
        found: list[str] = []
        with self.lock:
            if event_ids:
                rows = self.db.execute(
                    f"SELECT DISTINCT claim_id FROM claim_event_deps WHERE event_id IN "
                    f"({','.join('?' * len(event_ids))})", list(event_ids)).fetchall()
                found += [r[0] for r in rows]
            for team, etype, ms in points:
                rows = self.db.execute(
                    "SELECT claim_id, team_id, event_types FROM claim_window_deps "
                    "WHERE from_ms<=? AND to_ms>=?", (ms, ms)).fetchall()
                for r in rows:
                    if r["team_id"] not in (None, team) and team is not None:
                        continue
                    types = json.loads(r["event_types"])
                    if types and etype not in types:
                        continue
                    found.append(r["claim_id"])
        seen, out = set(), []
        for c in found:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def overlays_since(self, cursor, profile_id=None) -> list[tuple[int, BaseModel]]:
        q, args = "SELECT * FROM overlays WHERE cursor>?", [cursor]
        if profile_id:
            q += " AND profile_id=?"
            args.append(profile_id)
        with self.lock:
            rows = self.db.execute(q + " ORDER BY cursor", args).fetchall()
        return [(r["cursor"], self._load("overlay", r)) for r in rows]

    def batch_set_status(self, updates: list[tuple[str, str, str, dict]]) -> None:
        """Apply many status changes in one transaction (propagation, design §4 step 4)."""
        with self.lock:
            try:
                for kind, id_, status, fields in updates:
                    table = _KINDS[kind][0]
                    sets = ["status=?"] + [f"{k}=?" for k in fields]
                    self.db.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id=?",
                                    [status, *fields.values(), id_])
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def get_status(self, kind, id) -> str | None:
        table = _KINDS[kind][0]
        with self.lock:
            r = self.db.execute(f"SELECT status FROM {table} WHERE id=?", (id,)).fetchone()
        return r[0] if r else None

    def log_call(self, row: dict) -> None:
        cols = ["call_id", "moment_id", "agent", "model", "started_wall", "ms", "tokens_in",
                "tokens_out", "outcome", "trace_id", "cached"]
        with self.lock:
            self.db.execute(
                f"INSERT OR REPLACE INTO agent_calls (match_id, {','.join(cols)}) "
                f"VALUES (?,{','.join('?' * len(cols))})",
                [self.match_id, *[row.get(c) for c in cols]])
            self.db.commit()

    def set_call_outcome(self, call_id: str, outcome: str) -> None:
        with self.lock:
            self.db.execute("UPDATE agent_calls SET outcome=? WHERE call_id=?", (outcome, call_id))
            self.db.commit()

    def calls(self, moment_id: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM agent_calls", []
        if moment_id:
            q += " WHERE moment_id=?"
            a = [moment_id]
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY rowid", a).fetchall()]
