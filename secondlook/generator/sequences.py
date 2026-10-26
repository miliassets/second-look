"""Short held-out sequences for the single-agent comparison harness (design §7).

Each is a ~4-minute window with one goal and hidden ground truth (`truth["seq"]`):
- pressing: the scorers win the ball high up by pressing, three passes, goal  -> pressing_recovery
- chain: the scorers recover in midfield, ten passes, goal                  -> sustained_possession
- counter: the scorers recover deep and go long, two passes, goal           -> direct_play_chaos
- misleading: the OTHER side holds ~70 % of the ball for the whole window; the scorers score on a
  counter or a press, so the true tag is never sustained_possession.
"""
from __future__ import annotations

from ..config import PITCH_L, PITCH_W
from .corrections import CorrectionSpec, insert_corrections
from .engine import OTHER
from .engine_full import P1_DIR, Beat, FullEngine, W
from .frames import build_frames

SEQ_LEN_MS = 240_000
GOAL_AT_MS = 150_000
FAMILIES = ("pressing", "chain", "counter", "misleading_press", "misleading_counter")
TRUE_TAG = {"pressing": "pressing_recovery", "chain": "sustained_possession",
            "counter": "direct_play_chaos", "misleading_press": "pressing_recovery",
            "misleading_counter": "direct_play_chaos"}


class SeqEngine(FullEngine):
    def __init__(self, seed: int, family: str, scorer_team: str = "IRV"):
        super().__init__(seed, "random")
        self.family, self.scorer_team = family, scorer_team
        self.random_goals = False
        self.foul_p = 0.0
        self.next_foul = self.next_corner = 10**9  # no scheduled fouls or corners in a window
        self.periods = [{"period": 1, "length_ms": SEQ_LEN_MS + 5_000},
                        {"period": 2, "length_ms": 1_000}]
        t, o = scorer_team, OTHER[scorer_team]
        mis = family.startswith("misleading")
        self.windows = [W(1, 0, 5, "seq",
                          **({o: {"pass_skill": 0.12, "tempo": 0.85, "press": 0.1, "forward": 0.3},
                              t: {"pass_skill": -0.12, "tempo": 1.0, "press": 0.05}}
                             if mis else {}))]
        self.team, self.holder = o, self.lineup[o]["CM"]
        self.beats = [Beat(1, GOAL_AT_MS, self.goal_beat, "seq_goal")]
        self.sub_done = self.beat_done = True

    def pid(self, team, role):
        return self.lineup[team][role]

    def goal_beat(self):
        t = self.scorer_team
        p = lambda role: self.pid(t, role)  # noqa: E731
        fam = self.family
        if fam in ("pressing", "misleading_press"):
            moves = [(p("AM"), (80, 38)), (p("LW"), (88, 50)), (p("ST"), (93, 36))]
            args = dict(recovery_pos=(70, 30), kind="press", recoverer=p("CM"))
        elif fam == "chain":
            moves = [(p("DM"), (40, 26)), (p("CB1"), (30, 24)), (p("CB2"), (29, 42)),
                     (p("CM"), (44, 40)), (p("RB"), (50, 9)), (p("RW"), (62, 11)),
                     (p("AM"), (63, 32)), (p("ST"), (73, 36)), (p("AM"), (71, 31)),
                     (p("ST"), (86, 35))]
            args = dict(recovery_pos=(46, 30), kind="tackle", recoverer=p("CM"))
        else:  # counter
            moves = [(p("ST"), (78, 34)), (p("AM"), (90, 36))]
            args = dict(recovery_pos=(30, 34), kind="tackle", recoverer=p("DM"))
        scorer = moves[-1][0]
        self.scripted_move(t, "seq", moves, scorer=scorer, **args)
        self.truth["seq"] = {**self.truth.pop("seq"), "true_tag": TRUE_TAG[fam], "family": fam,
                             "scorer_team": t}

    def run(self):
        self.emit("match_metadata", None, None, q={
            "slice": False, "home": "KBY", "away": "IRV", "score": dict(self.score),
            "attacking_direction_p2": {"KBY": -1, "IRV": 1}, "scenario": "sequence"})
        for team in ("KBY", "IRV"):
            self.emit("lineup", team, None, q={"formation": self.formation[team],
                                               "lineup": dict(self.lineup[team])})
        self.period, self.dir = 1, dict(P1_DIR)
        self.emit("period_start", None, None, q={})
        self.poss_id += 1
        self.ball = (PITCH_L / 2, PITCH_W / 2)
        self.emit("kickoff", self.team, self.holder, self.ball, q={})
        while self.t < SEQ_LEN_MS:
            self.step()
        self.periods[0]["length_ms"] = max(self.periods[0]["length_ms"], self.t + 1000)
        self.emit("period_end", None, None, q={})
        return self.events


def generate_sequence(seed: int, family: str, scorer_team: str = "IRV",
                      correction_delay_ms: int | None = None):
    from . import GeneratedMatch
    eng = SeqEngine(seed, family, scorer_team)
    events = eng.run()
    frames = build_frames(events, eng.players, 0, eng.periods[0]["length_ms"], seed, 1)
    truth = {"seed": seed, "scope": f"sequence {family}", "beats": eng.truth}
    if correction_delay_ms is not None:
        events = insert_corrections(events, [CorrectionSpec(
            "void_goal", delay_ms=correction_delay_ms,
            target_event_id=eng.truth["seq"]["event_id"], reason="offside after review")], truth)
    md = eng.metadata()
    md.seed = seed
    return GeneratedMatch(md, events, frames, truth)
