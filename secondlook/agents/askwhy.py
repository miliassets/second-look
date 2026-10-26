"""Ask why: a sourced answer from match-state tools, or an honest refusal with the top facts."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .. import config
from ..checker.text import check_text
from ..contracts.agents_io import AskLLMOutput, Moment
from ..contracts.claims import Claim, ClaimRecord
from ..contracts.overlay import ClaimRef, Overlay, Source
from ..contracts.prefs import ViewerProfile
from .runtime import prompt, run_agent
from .scouts import SCOUT_TOOLS
from .workflow import Services

_counter = itertools.count(1)
REFUSAL = {"en": "I can't support an answer from the match data.",
           "es": "No puedo respaldar una respuesta con los datos del partido."}
FACTS = {"en": "Score {score}. {ta}: {pa}% possession; {tb}: {pb}%.",
         "es": "Marcador {score}. {ta}: {pa}% de posesión; {tb}: {pb}%."}
HEADLINE = {"en": "Ask why", "es": "Pregunta"}


@dataclass
class AskResult:
    supported: bool
    text: str
    overlay: Overlay | None = None
    claims: list[ClaimRecord] = field(default_factory=list)
    reason: str = ""
    outcome: str = "ok"


def _facts_line(svc: Services, dossier: dict, n: int, now_ms: int, lang: str, mode: str,
                owner: str) -> tuple[str, list[ClaimRecord]]:
    facts = {(f["type"], f["params"].get("metric"), f["params"].get("team_id")): f
             for f in dossier["facts"]}
    score = next((f for f in dossier["facts"] if f["type"] == "score"), None)
    teams = sorted({f["params"]["team_id"] for f in dossier["facts"]
                    if f["type"] == "rate" and f["params"].get("metric") == "possession_share"})
    if not score or len(teams) < 2:
        return "", []
    recs, ids = [], {}
    for key, f in (("score", score), ("pa", facts[("rate", "possession_share", teams[0])]),
                   ("pb", facts[("rate", "possession_share", teams[1])])):
        cid = f"ask{n}.{key}"
        rec = svc.checker.verify(Claim(claim_id=cid, type=f["type"], params=f["params"]),
                                 now_ms, "answer", owner)
        if rec.status != "verified":
            return "", []
        recs.append(rec)
        ids[key] = "{" + cid + "}"
    tpl = FACTS[lang].format(score=ids["score"], ta="{t:" + teams[0] + "}",
                             tb="{t:" + teams[1] + "}", pa=ids["pa"], pb=ids["pb"])
    chk = check_text(tpl, recs, svc.roster, lang, mode, store=svc.store, periods=svc.periods)
    return (chk.text, recs) if chk.ok else ("", [])


async def ask_why(svc: Services, question: str, profile: ViewerProfile, now_ms: int
                  ) -> AskResult:
    n = next(_counter)
    owner = f"ask{n}"
    store = svc.store
    m = Moment(moment_id=f"mo-ask-{n}", kind="question", window_from_ms=max(0, now_ms - 120_000),
               window_to_ms=now_ms, now_ms=now_ms, as_of_rev=store.current_rev())
    store.put("moment", m)
    token = svc.tools.issue_token(m)
    dossier = svc.tools.get_dossier(token)
    payload = {"question": question, "profile": profile.model_dump(), "moment_token": token,
               "dossier": dossier}
    run = await run_agent(svc.llm, "askwhy", prompt("askwhy"), payload, AskLLMOutput, svc.tools,
                          SCOUT_TOOLS + ["get_dossier"], m.moment_id, max_tool_rounds=3,
                          max_tokens=300, timeout_s=config.TIMEOUT_ASKWHY)
    text, recs, reason = "", [], run.outcome
    if run.outcome == "ok" and run.output is not None:
        out: AskLLMOutput = run.output  # type: ignore[assignment]
        if out.supported and out.claims and out.template:
            for j, c in enumerate(out.claims, start=1):
                old = c.claim_id
                new_id = f"{owner}.c{j}"
                tpl = out.template.replace("{" + old + "}", "{" + new_id + "}")
                out.template = tpl
                rec = svc.checker.verify(c.model_copy(update={"claim_id": new_id}), now_ms,
                                         "answer", owner)
                recs.append(rec)
            chk = check_text(out.template, [r for r in recs if r.status == "verified"],
                             svc.roster, profile.lang, profile.mode, store=store,
                             periods=svc.periods)
            if chk.ok and all(r.status == "verified" for r in recs):
                text, reason = chk.text, "ok"
            else:
                recs, reason = [], "; ".join(chk.errors or ["claims did not verify"])[:200]
        else:
            reason = "unsupported"
    supported = bool(text)
    if not supported:
        facts, recs = _facts_line(svc, dossier, n, now_ms, profile.lang, profile.mode, owner)
        text = (REFUSAL[profile.lang] + (" " + facts if facts else ""))
    ov = Overlay(
        overlay_id=f"ov-ask-{n}-{profile.profile_id}", story_id=f"st-ask-{n}", revision=1,
        kind="answer", viewer_mode=profile.mode, profile_id=profile.profile_id, lang=profile.lang,
        show_at_match_ms=now_ms, duration_ms=20_000, slot="side_panel", priority=1,
        headline=HEADLINE[profile.lang], text=text,
        evidence_ids=list(dict.fromkeys(e for r in recs for e in r.evidence_ids)),
        claims=[ClaimRef(claim_id=r.claim_id, type=r.type, value=None) for r in recs],
        source=Source(kind="live", trace_id=f"{svc.llm.model}:ask{n}"))
    ok = recs and svc.publisher is not None
    if svc.publisher is not None:
        if recs:
            svc.publisher.publish(ov, recs, now_ms)
        else:  # a bare refusal has no claims to re-check
            store.put("overlay", ov)
            svc.publisher.bus.publish("overlay", ov.model_dump(mode="json"), profile.profile_id)
    del ok
    return AskResult(supported, text, ov, recs, reason, run.outcome)
