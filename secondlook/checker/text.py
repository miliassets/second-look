"""Text rules and the renderer (design §3.5 'Text rules').

Templates carry no raw digits and no typed names. Numbers are inserted by this code from the
checker's verified values, so a model cannot slip a wrong number into rendered text.

Placeholders:  {claim_id}  value of a verified claim     {time:EVENT_ID}  clock of an event
               {p:PLAYER_ID}  player surname              {t:TEAM_ID}  club short name
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from .. import config
from ..contracts.claims import ClaimRecord
from ..contracts.overlay import Span
from ..ledger.store import LedgerStore
from ..timeline import format_clock
from .roster import Roster

LEX = Path(__file__).parent / "lexicon"
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
CLAIM_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)


@cache
def lexicon(lang: str) -> dict:
    return json.loads((LEX / f"{lang}.json").read_text())


@dataclass
class TextResult:
    ok: bool
    text: str = ""
    spans: list[Span] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    claims_used: list[str] = field(default_factory=list)


def num(v: float | int, lang: str) -> str:
    sep = lexicon(lang)["decimal_sep"]
    if isinstance(v, bool):
        return str(v)
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.1f}".replace(".", sep)


def claim_text(rec: ClaimRecord, lang: str) -> str:
    v, lex = rec.value, lexicon(lang)
    t = rec.type
    if t == "score":
        return f"{v['home']}{lex['score_sep']}{v['away']}"
    if t == "chain":
        return num(v["passes"], lang)
    if t == "compare":
        return num(abs(v["delta"]), lang)
    if t == "phase":
        return lex["phase_labels"][v]
    if t == "involved":
        return str(v)
    if isinstance(v, str):
        return lex.get("outcomes", {}).get(v, v)
    return num(v, lang)


def check_text(template: str, claims: list[ClaimRecord], roster: Roster, lang: str, mode: str,
               store: LedgerStore | None = None, periods: list[dict[str, int]] | None = None
               ) -> TextResult:
    """Validate a template against its claims and render it. All problems are collected."""
    lex = lexicon(lang)
    by_id = {c.claim_id: c for c in claims}
    errors: list[str] = []
    used: list[str] = []

    for c in claims:
        if c.status != "verified":
            errors.append(f"claim {c.claim_id} is {c.status}")

    # allowed ids for names: appear in claim params or as actors of evidence events
    ok_players: set[str] = set()
    ok_teams: set[str] = set()
    evidence: set[str] = set()
    for c in claims:
        evidence.update(c.evidence_ids)
        for k in ("player_id", "team_id", "new_player_id"):
            v = c.params.get(k)
            if isinstance(v, str):
                (ok_players if k != "team_id" else ok_teams).add(v)
        for k in ("from_event_id", "to_event_id", "event_id", "ended_with_event_id"):
            if isinstance(c.params.get(k), str):
                evidence.add(c.params[k])
        for eid in c.params.get("event_ids", []) if isinstance(c.params.get("event_ids"), list) else []:
            evidence.add(eid)
    if store is not None:
        for eid in evidence:
            se = store.get_event(eid)
            if se is not None:
                if se.event.player_id:
                    ok_players.add(se.event.player_id)
                if se.event.team_id:
                    ok_teams.add(se.event.team_id)

    # ---- literal text rules (placeholders removed)
    literal = PLACEHOLDER.sub(" ", template)
    if "{" in literal or "}" in literal:
        errors.append("unbalanced braces")
    if re.search(r"\d", literal):
        errors.append("raw digit in template")
    if re.search(r"[<>]", literal):
        errors.append("markup characters in template")
    low = " " + re.sub(r"\s+", " ", literal.lower()) + " "
    for w in lex["causal"]:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low):
            errors.append(f"causal wording '{w}'")
    forbidden = roster.forbidden_words()
    allowed_caps = set(lex["allowed_caps"])
    for m in WORD.finditer(literal):
        w = m.group(0).strip("'’-")
        if not w:
            continue
        if w.lower() in forbidden:
            errors.append(f"name '{w}' typed as plain text")
        elif w[0].isupper() and w not in allowed_caps:
            errors.append(f"capitalised word '{w}' not in the lexicon")

    # ---- render placeholders
    out: list[str] = []
    spans: list[Span] = []
    pos = 0
    for m in PLACEHOLDER.finditer(template):
        out.append(template[pos:m.start()])
        pos = m.end()
        body = m.group(1).strip()
        txt = ""
        if ":" in body:
            kind, _, ref = body.partition(":")
            if kind == "p":
                if ref not in roster.players:
                    errors.append(f"unknown player {ref}")
                elif ref not in ok_players:
                    errors.append(f"player {ref} not backed by a claim")
                else:
                    txt = roster.player_short(ref)
                    start = len("".join(out))
                    spans.append(Span(start=start, end=start + len(txt), ref=f"p:{ref}"))
            elif kind == "t":
                if ref not in roster.teams:
                    errors.append(f"unknown team {ref}")
                elif ref not in ok_teams:
                    errors.append(f"team {ref} not backed by a claim")
                else:
                    txt = roster.teams[ref]
                    start = len("".join(out))
                    spans.append(Span(start=start, end=start + len(txt), ref=f"t:{ref}"))
            elif kind == "time":
                se = store.get_event(ref) if store is not None else None
                if se is None:
                    errors.append(f"time placeholder: event {ref} unknown")
                elif ref not in evidence:
                    errors.append(f"event {ref} not in the claims' evidence")
                else:
                    txt = format_clock(*(se.event.period, se.event.match_clock_ms))
            else:
                errors.append(f"unknown placeholder kind '{kind}'")
        elif CLAIM_ID.match(body) and body in by_id:
            rec = by_id[body]
            if rec.status == "verified":
                txt = claim_text(rec, lang)
                used.append(body)
        else:
            errors.append(f"placeholder {{{body}}} does not reference a claim of this output")
        out.append(txt)
    out.append(template[pos:])
    text = "".join(out)
    if len(text) > config.MAX_LEN[mode]:
        errors.append(f"too long ({len(text)} > {config.MAX_LEN[mode]})")
    return TextResult(ok=not errors, text=text if not errors else "", spans=spans if not errors
                      else [], errors=errors, claims_used=used)


