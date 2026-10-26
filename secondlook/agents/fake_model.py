"""Deterministic rule-based stand-in for the model (LLM_PROVIDER=fake).

NOT recorded model output: it reads the dossier in the request and answers with candidates built
only from dossier claim objects, so everything it says verifies. Real recorded fixtures replace
it after the live smoke run.
"""
from __future__ import annotations

import json
import uuid

from .llm import LLMRequest, LLMResponse, ToolCall


def _payload(req: LLMRequest) -> dict:
    return json.loads(req.messages[0]["content"])


def _facts(p: dict) -> list[dict]:
    return p["dossier"]["facts"]


def _find(facts, type_, **match):
    for f in facts:
        if f["type"] == type_ and all(f["params"].get(k) == v for k, v in match.items()):
            return f
    return None


def _claim(f: dict, cid: str) -> dict:
    return {"claim_id": cid, "type": f["type"], "params": f["params"], "value": f["value"]}


def _tool_results(req: LLMRequest) -> list[dict]:
    return [json.loads(m["content"]) for m in req.messages if m["role"] == "tool"]


def _resp(cands: list[dict]) -> LLMResponse:
    return LLMResponse(text=json.dumps({"candidates": cands}), tokens_in=0, tokens_out=0,
                       model="fake")


def pressure(req: LLMRequest) -> LLMResponse:
    p = _payload(req)
    team = p["dossier"]["moment"].get("team_id")
    facts = _facts(p)
    rec = _find(facts, "count", metric="recoveries", team_id=team)
    if not team or rec is None or rec["value"] < 1:
        return _resp([])
    claims = [_claim(rec, "c1")]
    chain = _find(facts, "chain")
    if chain:
        claims.append(_claim(chain, "c2"))
    return _resp([{"scout": "pressure", "thesis_tag": "pressing_recovery",
                   "thesis": "They won the ball back in midfield and moved it forward.",
                   "claims": claims}])


def rhythm(req: LLMRequest) -> LLMResponse:
    p = _payload(req)
    team = p["dossier"]["moment"].get("team_id")
    facts = _facts(p)
    chain = _find(facts, "chain")
    if chain and not _tool_results(req):
        return LLMResponse(tool_calls=[ToolCall(
            id="t" + uuid.uuid4().hex[:6], name="get_possession",
            arguments={"moment_token": p["moment_token"],
                       "possession_id": chain["params"]["possession_id"]})], model="fake")
    claims = []
    if chain:
        claims.append(_claim(chain, "c1"))
    share = _find(facts, "rate", metric="possession_share", team_id=team)
    if share:
        claims.append(_claim(share, "c2"))
    phase = _find(facts, "phase", team_id=team)
    if phase and phase["value"] == "control":
        claims.append(_claim(phase, "c3"))
    if not claims:
        return _resp([])
    tag = "sustained_possession" if chain and chain["value"]["passes"] >= 8 else "even_rhythm"
    thesis = ("The ball was kept through a long spell of passing before the finish."
              if tag == "sustained_possession" else "The match stayed at an even rhythm.")
    return _resp([{"scout": "rhythm", "thesis_tag": tag, "thesis": thesis, "claims": claims}])


def responders() -> dict:
    return {"pressure": pressure, "rhythm": rhythm}


def editor(req: LLMRequest) -> LLMResponse:
    p = _payload(req)
    cands = p["candidates"]
    if not cands:
        return LLMResponse(text=json.dumps({"chosen_candidate_id": None}), model="fake")

    def score(c):
        chain = next((x for x in c["claims"] if x["type"] == "chain"), None)
        return (c["thesis_tag"] == "sustained_possession" and bool(chain)
                and chain["value"]["passes"] >= 8, len(c["claims"]))
    reopen = p.get("reopen")
    system = next((c for c in cands if c["scout"] == "system"), None)
    if reopen:
        survivors = [c for c in cands if c["scout"] != "system"
                     and any(x["type"] == "count" for x in c["claims"])]
        if system is None or not survivors:
            return LLMResponse(text=json.dumps({"chosen_candidate_id": system and system[
                "candidate_id"]}), model="fake")
        best = survivors[0]
        count = next(x for x in best["claims"] if x["type"] == "count")
        status = next(x for x in system["claims"] if x["type"] == "event_fact")
        team = p["moment"].get("team_id")
        text = (f"{{t:{team}}} won the ball back {{{count['claim_id']}}} times, "
                "but the goal was ruled out.")
        return LLMResponse(text=json.dumps({
            "chosen_candidate_id": best["candidate_id"], "rejected": [],
            "claim_ids": [count["claim_id"], status["claim_id"]], "text_en": text}), model="fake")
    best = max([c for c in cands if c["scout"] != "system"], key=score)
    rejected = [{"candidate_id": c["candidate_id"],
                 "reason": f"{c['thesis_tag']}: fewer supporting claims than the chosen explanation"}
                for c in cands if c is not best]
    team = p["moment"].get("team_id")
    claims = best["claims"]
    by_type = {c["type"]: c for c in claims}
    if "chain" in by_type and team and p["moment"]["kind"] == "goal":
        c = by_type["chain"]
        text = f"{{t:{team}}} moved the ball through {{{c['claim_id']}}} passes, then scored."
        ids = [c["claim_id"]]
    elif "rate" in by_type and team and p["moment"]["kind"] != "goal":
        c = by_type["rate"]
        text = f"{{t:{team}}} held {{{c['claim_id']}}}% of the ball over the last two minutes."
        ids = [c["claim_id"]]
    elif "count" in by_type and team:
        c = by_type["count"]
        text = f"{{t:{team}}} won the ball back {{{c['claim_id']}}} times in the spell before this."
        ids = [c["claim_id"]]
    elif "phase" in by_type and team:
        c = by_type["phase"]
        text = f"Rhythm: {{t:{team}}} in {{{c['claim_id']}}}."
        ids = [c["claim_id"]]
    else:
        c = claims[0]
        text = f"Backed by {{{c['claim_id']}}}."
        ids = [c["claim_id"]]
    return LLMResponse(text=json.dumps({
        "chosen_candidate_id": best["candidate_id"], "rejected": rejected, "claim_ids": ids,
        "text_en": text}), model="fake")


def responders() -> dict:  # noqa: F811
    return {"pressure": pressure, "rhythm": rhythm, "editor": editor}


def producer(req: LLMRequest) -> LLMResponse:
    p = _payload(req)
    prof, story, claims = p["profile"], p["story"], p["claims"]
    lang, mode = prof["lang"], prof["mode"]
    team = p["moment"].get("team_id")
    by_type = {c["type"]: c for c in claims}
    if "event_fact" in by_type and "count" in by_type and team:  # reopened story
        c = by_type["count"]["claim_id"]
        tpl = (f"{{t:{team}}} recuperó el balón {{{c}}} veces, pero el gol fue anulado."
               if lang == "es" else story["text_en"])
        return LLMResponse(text=json.dumps({
            "template": tpl, "headline": "Así fue la jugada" if lang == "es" else "Why it happened",
            "claim_ids": story["claim_ids"], "layers": ["marker"]}), model="fake")
    follow = prof.get("followed_player_id")
    follow_ok = follow and follow in p.get("evidence_players", [])
    layers = ["chain", "marker", "player_halo"] if mode == "analyst" else ["marker", "player_halo"]
    if lang == "en":
        template, head = story["text_en"], "Why it happened"
        if mode == "analyst" and "chain" in by_type and p["moment"]["kind"] == "goal":
            template = story["text_en"]
            layers = ["chain", "zone", "marker", "player_halo"]
        ids = story["claim_ids"]
    else:
        head = "Así fue la jugada"
        if "chain" in by_type and team:
            c = by_type["chain"]["claim_id"]
            who = f"{{p:{follow}}}" if follow_ok else f"{{t:{team}}}"
            verb = "remató" if follow_ok else "movió el balón"
            template = f"{who} {verb} tras {{{c}}} pases seguidos."
            ids = [c]
        elif "rate" in by_type and team:
            c = by_type["rate"]["claim_id"]
            template = f"{{t:{team}}} tuvo {{{c}}}% del balón en los últimos minutos."
            ids = [c]
        elif "count" in by_type and team:
            c = by_type["count"]["claim_id"]
            template = f"{{t:{team}}} recuperó el balón {{{c}}} veces antes de la jugada."
            ids = [c]
        elif "phase" in by_type and team:
            c = by_type["phase"]["claim_id"]
            template = f"Ritmo: {{t:{team}}} en {{{c}}}."
            ids = [c]
        else:
            template, ids = "", []
    return LLMResponse(text=json.dumps({"template": template, "headline": head,
                                        "claim_ids": ids, "layers": layers}), model="fake")


def responders() -> dict:  # noqa: F811
    return {"pressure": pressure, "rhythm": rhythm, "editor": editor,
            "producer-analyst-en": producer, "producer-fan-es": producer}


def askwhy(req: LLMRequest) -> LLMResponse:
    p = _payload(req)
    q = p["question"].lower()
    prof = p["profile"]
    facts = p["dossier"]["facts"]
    topical = any(w in q for w in ("score", "goal", "why", "ball", "possession", "control", "gol",
                                   "por qué", "marcador", "posesión"))
    shares = [f for f in facts if f["type"] == "rate" and f["params"].get("metric") ==
              "possession_share"]
    score = next((f for f in facts if f["type"] == "score"), None)
    if not topical or not shares or not score:
        return LLMResponse(text=json.dumps({"supported": False}), model="fake")
    lead = max(shares, key=lambda f: f["value"])
    team = lead["params"]["team_id"]
    claims = [_claim(score, "a1"), _claim(lead, "a2")]
    if prof["lang"] == "es":
        tpl = f"Marcador {{a1}}. {{t:{team}}} tuvo {{a2}}% del balón en los últimos minutos."
    else:
        tpl = f"Score {{a1}}. {{t:{team}}} held {{a2}}% of the ball over the last two minutes."
    return LLMResponse(text=json.dumps({"supported": True, "claims": claims, "template": tpl}),
                       model="fake")


def responders() -> dict:  # noqa: F811
    return {"pressure": pressure, "rhythm": rhythm, "editor": editor,
            "producer-analyst-en": producer, "producer-fan-es": producer, "askwhy": askwhy}


def player(req: LLMRequest) -> LLMResponse:
    p = _payload(req)
    facts = _facts(p)
    trig = p["dossier"]["moment"].get("trigger_event_id")
    actor = next((f for f in facts if f["type"] == "event_fact"
                  and f["params"].get("field") == "player_id"), None)
    if not actor or not trig:
        return _resp([])
    pid = actor["value"]
    followed = p.get("followed_players", [])
    if followed and pid not in followed:
        return _resp([])
    claims = [_claim(actor, "c1"), {
        "claim_id": "c2", "type": "involved", "params": {"player_id": pid, "event_ids": [trig]},
        "value": pid}]
    return _resp([{"scout": "player", "thesis_tag": "individual_action",
                   "thesis": "The player involved took the decisive action themselves.",
                   "claims": claims}])


def responders() -> dict:  # noqa: F811
    return {"pressure": pressure, "rhythm": rhythm, "editor": editor, "player": player,
            "producer-analyst-en": producer, "producer-fan-es": producer, "askwhy": askwhy}


def single(req: LLMRequest) -> LLMResponse:
    """A naive one-shot rule: no competing explanations, so possession volume decides."""
    p = _payload(req)
    facts = _facts(p)
    team = p["dossier"]["moment"].get("team_id")
    chain = _find(facts, "chain")
    rec = _find(facts, "count", metric="recoveries", team_id=team)
    share = _find(facts, "rate", metric="possession_share", team_id=team)
    if chain and chain["value"]["passes"] >= 8:
        tag, c = "sustained_possession", chain
        en = f"{{t:{team}}} moved the ball through {{c1}} passes, then scored."
        es = f"{{t:{team}}} movió el balón en {{c1}} pases seguidos."
    elif rec and rec["value"] >= 1:
        tag, c = "pressing_recovery", rec
        en = f"{{t:{team}}} won the ball back {{c1}} times in the spell before this."
        es = f"{{t:{team}}} recuperó el balón {{c1}} veces antes de la jugada."
    elif share:
        tag, c = "even_rhythm", share
        en = f"{{t:{team}}} held {{c1}}% of the ball over the last two minutes."
        es = f"{{t:{team}}} tuvo {{c1}}% del balón en los últimos minutos."
    else:
        return LLMResponse(text=json.dumps({"thesis_tag": None}), model="fake")
    return LLMResponse(text=json.dumps({
        "thesis_tag": tag, "claims": [_claim(c, "c1")], "analyst_template": en,
        "analyst_headline": "Why it happened", "fan_template": es,
        "fan_headline": "Así fue la jugada", "layers": ["marker"]}), model="fake")


def responders() -> dict:  # noqa: F811
    return {"pressure": pressure, "rhythm": rhythm, "editor": editor, "player": player,
            "producer-analyst-en": producer, "producer-fan-es": producer, "askwhy": askwhy,
            "single": single}
