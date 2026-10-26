"""Full 90-minute generator (design S18): scripted beats from synthetic-data-design §7 plus the
4-0 and red-card scripts. Same possession-chain engine; period 1 and 2, stoppage, half time.

Scenarios: demo (the §7 script, final 1-1), rout (4-0), redcard (Ironvale striker sent off),
random (no script, stochastic goals: used for the realism distribution over seeds).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from ..config import PITCH_L, PITCH_W
from .engine import OTHER, PERIODS, Engine, r2
from .script import BEAT_AFTER_MS, SUB_AT_MS, Window

SHOT_BASE = 0.10
SHOT_TARGET = 24
TEMPO_BASE = 1.3
P1_DIR = {"KBY": 1, "IRV": -1}
P2_DIR = {"KBY": -1, "IRV": 1}


@dataclass
class Beat:
    period: int
    at_ms: int
    fn: Callable[[], None]
    name: str = ""


def W(period, lo_min, hi_min, label, **deltas):
    return Window(int(lo_min * 60_000), int(hi_min * 60_000), label, deltas, period)


def demo_windows() -> list[Window]:
    return [
        W(1, 0, 20, "kby_press", KBY={"press": 0.22, "pass_skill": 0.05, "tempo": 0.9},
          IRV={"press": 0.08, "pass_skill": -0.03}),
        W(1, 21, 38, "irv_direct_chaos", KBY={"forward": 0.75, "pass_skill": -0.11, "tempo": 0.85,
                                              "press": 0.15},
          IRV={"forward": 0.8, "pass_skill": -0.10, "tempo": 0.85, "press": 0.15}),
        W(2, 0, 10, "even_slow", KBY={"tempo": 1.35, "press": 0.08, "forward": 0.30},
          IRV={"tempo": 1.35, "press": 0.08, "forward": 0.30}),
        W(2, 10, 16, "even_slow", KBY={"tempo": 1.35, "press": 0.08, "forward": 0.30},
          IRV={"tempo": 1.35, "press": 0.08, "forward": 0.30}),
        W(2, 16, 23, "irv_control", IRV={"pass_skill": 0.07, "forward": 0.55, "tempo": 0.9,
                                         "press": 0.22},
          KBY={"pass_skill": -0.07, "press": 0.05, "tempo": 1.0}),
        W(2, 40, 45, "kby_fatigue", KBY={"tempo": 1.3, "press": 0.04, "pass_skill": -0.05}),
    ]


def rout_windows() -> list[Window]:
    return [W(p, 0, 50, "kby_dominate", KBY={"press": 0.25, "pass_skill": 0.08, "tempo": 0.9},
              IRV={"press": 0.06, "pass_skill": -0.08, "tempo": 1.1}) for p in (1, 2)]


def red_windows(after_min=30) -> list[Window]:
    return [W(1, after_min, 50, "irv_ten", KBY={"press": 0.2, "pass_skill": 0.07, "tempo": 0.9},
              IRV={"press": 0.05, "pass_skill": -0.05, "tempo": 1.1}),
            W(2, 0, 50, "irv_ten", KBY={"press": 0.2, "pass_skill": 0.07, "tempo": 0.9},
              IRV={"press": 0.05, "pass_skill": -0.05, "tempo": 1.1})]


class FullEngine(Engine):
    def __init__(self, seed: int, scenario: str = "demo"):
        super().__init__(seed)
        self.scenario = scenario
        self.period = 1
        self.dir = dict(P1_DIR)
        self.t = 0
        self.score = {"KBY": 0, "IRV": 0}
        self.team = "KBY"
        self.holder = self.lineup["KBY"]["CM"]
        self.ball = (PITCH_L / 2, PITCH_W / 2)
        self.random_goals = scenario in ("random", "redcard")
        self.foul_p, self.corner_save_p, self.corner_block_p = 0.0, 0.1, 0.1
        self.n_shots = 0
        self.next_foul = int(self.rt(120_000, 280_000))  # global ms; scheduled, not Poisson
        self.next_corner = int(self.rt(300_000, 700_000))
        self.periods = [dict(p) for p in PERIODS]
        if scenario == "random":
            self.periods[0]["length_ms"] = 2_700_000 + int(self.rt(60_000, 180_000))
            self.periods[1]["length_ms"] = 2_700_000 + int(self.rt(120_000, 300_000))
        self.windows = {"demo": demo_windows, "rout": rout_windows, "redcard": red_windows,
                        "random": lambda: []}[scenario]()
        self.beats: list[Beat] = []
        self.stoppage_said = {1: False, 2: False}
        self.truth["scenario"] = scenario
        self.setup_beats()

    # ---------- params
    def emit(self, type_, *a, **k):
        if type_ == "shot":
            self.n_shots += 1
        return super().emit(type_, *a, **k)

    def gt(self) -> int:
        """Global match time in ms (period 2 starts after period 1's actual length)."""
        return self.t + (self.periods[0]["length_ms"] if self.period == 2 else 0)

    def card_prob(self) -> float:
        yel = len(self.yellows)
        if yel == 0 and self.gt() > 3_000_000:
            return 0.6
        return 0.0 if yel >= 5 else 0.16

    def prm(self, team):
        p = super().prm(team)
        # feedback keeps the shot count near a target of 24 whatever the seed does
        expected = SHOT_TARGET * min(1.0, self.gt() / 5_700_000)
        mult = min(3.0, max(0.15, 1.0 + 0.45 * (expected - self.n_shots)))
        p["shot"] = SHOT_BASE * mult
        p["tempo"] *= TEMPO_BASE
        return p

    # ---------- beats
    def setup_beats(self):
        sc = self.scenario
        if sc == "demo":
            self.beats = [
                Beat(1, 840_000, lambda: self.scripted_move("KBY", "kby_goal_14", [
                    ("KBY-10", (80, 40)), ("KBY-11", (88, 52)), ("KBY-09", (93, 36))],
                    scorer="KBY-09", recovery_pos=(70, 30), kind="press", recoverer="KBY-08"),
                     "kby_goal_14"),
                Beat(1, 1_980_000, self.halvard_card, "halvard_yellow"),
                Beat(1, 2_790_000, self.fast_shot_saved, "irv_shot_112"),
                Beat(2, SUB_AT_MS, self.do_sub, "kby_sub_shift"),
                Beat(2, BEAT_AFTER_MS, self.do_beat, "irv_goal_67"),
                Beat(2, 1_740_000, self.kaddar_on, "kaddar_sprint"),
                Beat(2, 2_160_000, lambda: self.scripted_move("IRV", "irv_goal_81_offside", [
                    ("IRV-10", (62, 32)), ("IRV-08", (70, 30)), ("IRV-10", (80, 34)),
                    ("IRV-09", (90, 34))], scorer="IRV-09", recovery_pos=(52, 30),
                    kind="tackle", recoverer="IRV-06"), "irv_goal_81_offside"),
                Beat(2, 2_910_000, self.late_corner, "kby_corner_90_3"),
            ]
            self.sub_done = self.beat_done = False
        elif sc == "rout":
            self.sub_done = self.beat_done = True
            goals = [(1, 720_000, "KBY-09"), (1, 1_860_000, "KBY-10"), (2, 480_000, "KBY-09"),
                     (2, 1_980_000, "KBY-11")]
            for i, (per, at, who) in enumerate(goals, start=1):
                self.beats.append(Beat(per, at, (lambda who=who, i=i: self.scripted_move(
                    "KBY", f"rout{i}", [("KBY-08", (62, 30)), ("KBY-10", (74, 38)),
                                        ("KBY-07", (84, 52)), (who, (92, 36))],
                    scorer=who, recovery_pos=(52, 32), kind="tackle", recoverer="KBY-06")),
                                  f"kby_goal_{i}"))
        elif sc == "redcard":
            self.sub_done = self.beat_done = True
            self.beats = [Beat(1, 1_800_000, self.fenn_red, "fenn_red")]
        else:
            self.sub_done = self.beat_done = True

    def step(self):
        for b in list(self.beats):
            if b.period == self.period and self.t >= b.at_ms:
                self.beats.remove(b)
                return b.fn()
        if self.t >= 2_670_000 and not self.stoppage_said[self.period]:
            self.stoppage_said[self.period] = True
            mins = round((self.periods[self.period - 1]["length_ms"] - 2_700_000) / 60_000)
            self.emit("stoppage_added", None, None, q={"minutes": mins})
        g = self.gt()
        if g >= self.next_foul and not self.restart:
            self.next_foul = g + int(self.rt(190_000, 290_000))
            return self.do_foul()
        if g >= self.next_corner and not self.restart and self.holder_in_attack():
            self.next_corner = g + int(self.rt(540_000, 900_000))
            return self.scheduled_corner()
        return self.play()

    def holder_in_attack(self) -> bool:
        return self.norm(self.team, self.ball)[0] > 62 and self.by_id[self.holder].role != "GK"

    def scheduled_corner(self):
        team, opp = self.team, OTHER[self.team]
        d = self.nearest(opp, self.ball)
        self.emit("block", opp, d, self.ball, outcome="won", q={"deflected_out": True})
        ny = self.norm(team, self.ball)[1]
        self.t += int(self.rt(6000, 10000))
        self.corner(team, self.real(team, 105.0, 0.0 if ny < 34 else 68.0))

    # ---------- scripted building blocks
    def force_possession(self, team, pos_norm, kind="tackle", recoverer=None) -> str:
        """Make `team` win the ball at pos_norm (their attacking frame). Returns the winning event id."""
        opp = OTHER[team]
        if self.team == team:  # concede it first: a stray pass is intercepted, then it is won back
            stray_end = self.real(team, pos_norm[0] - 6, pos_norm[1] + 6)
            stray = self.emit("pass", team, self.holder, self.ball, stray_end,
                              outcome="incomplete", q={"length_m": 12.0, "height": "ground"})
            self.t += 1800
            d = self.nearest(opp, stray_end)
            self.emit("interception", opp, d, stray_end, outcome="won", related=[stray.event_id])
            self.change(opp, "interception", d, stray_end)
            self.t += 2200
        victim = self.holder
        pos = self.real(team, *pos_norm)
        self.ball = pos
        rec = recoverer or self.nearest(team, pos)
        if kind == "press":
            dur = 1300
            self.emit("pressure", team, rec, pos, outcome="forced_turnover", q={
                "target_player_id": victim, "duration_ms": dur, "result": "forced_turnover"})
            ev = self.emit("tackle", team, rec, pos, outcome="won", q={"victim": victim}, dt=dur)
            self.t += dur + 300
        else:
            ev = self.emit("tackle", team, rec, pos, outcome="won", q={"victim": victim})
            self.t += 400
        self.change(team, "tackle", rec, pos)
        return ev.event_id

    def scripted_move(self, team, name, passes, scorer, recovery_pos, kind, recoverer):
        opp = OTHER[team]
        rec_id = self.force_possession(team, recovery_pos, kind, recoverer)
        ids = []
        for to, (nx, ny) in passes:
            end = self.real(team, nx + self.rt(-0.8, 0.8), ny + self.rt(-0.8, 0.8))
            start = self.ball
            dist = math.hypot(end[0] - start[0], end[1] - start[1])
            ev = self.emit("pass", team, self.holder, start, end, outcome="complete", q={
                "length_m": r2(dist), "height": "ground", "body_part": "right_foot",
                "through_ball": False, "switch": False, "cutback": False, "recipient": to})
            ids.append(ev.event_id)
            self.t += int(self.rt(2000, 3000))
            self.holder, self.ball = to, end
        shot_end = self.real(team, 105.0, self.rt(32, 37))
        shot = self.emit("shot", team, scorer, self.ball, shot_end, outcome="scored", q={
            "speed_kmh": r2(self.rt(82, 94)), "body_part": "right_foot", "situation": "open_play",
            "on_target": True}, related=[ids[-1]])
        self.score[team] += 1
        goal = self.emit("goal", team, scorer, shot_end, outcome="scored", q={
            "home_score": self.score["KBY"], "away_score": self.score["IRV"]},
            related=[shot.event_id], dt=400)
        self.truth[name] = {"event_id": goal.event_id, "shot_event_id": shot.event_id,
                            "recovery_event_id": rec_id, "chain_passes": len(ids),
                            "chain_pass_event_ids": ids, "scorer": scorer, "team": team,
                            "minute": 1 + (self.t // 60_000) + (45 if self.period == 2 else 0)}
        self.kickoff_after_goal(opp)

    def halvard_card(self):
        """33': Halvard's tactical foul stops an Ironvale counter, yellow card."""
        self.force_possession("IRV", (40, 30), "tackle", "IRV-06")
        end = self.real("IRV", 62.0, 34.0)
        self.emit("carry", "IRV", self.holder, self.ball, end, outcome="complete",
                  q={"distance_m": 22.0, "counter": True})
        self.t += 2800
        self.ball = end
        foul = self.emit("foul", "KBY", "KBY-08", end, outcome="none", q={
            "fouled": self.holder, "tactical": True})
        card = self.emit("card", "KBY", "KBY-08", end, outcome="yellow", q={
            "reason": "tactical_foul"}, dt=2000)
        self.yellows.add("KBY-08")
        self.truth["halvard_card"] = {"event_id": card.event_id, "foul_event_id": foul.event_id}
        self.t += 6000
        self.restart = ("free_kick", end)

    def fast_shot_saved(self):
        """45+2': Ironvale shot at 112 km/h, saved by Varto."""
        self.force_possession("IRV", (66, 30), "tackle", "IRV-08")
        shot_end = self.real("IRV", 105.0, 35.0)
        shot = self.emit("shot", "IRV", "IRV-10", self.ball, shot_end, outcome="saved", q={
            "speed_kmh": 112.0, "body_part": "right_foot", "situation": "open_play",
            "on_target": True})
        self.t += 600
        save = self.emit("save", "KBY", "KBY-01", shot_end, outcome="saved",
                         related=[shot.event_id])
        self.change("KBY", "shot_ended", "KBY-01", self.real("KBY", 5.0, 34.0), dt=300)
        self.t += 3000
        self.truth["irv_shot_112"] = {"shot_event_id": shot.event_id, "save_event_id": save.event_id}

    def kaddar_on(self):
        """74': Kaddar replaces Arkwright and sprints at 35 km/h."""
        sub = self.emit("substitution", "IRV", "IRV-07", q={
            "on": "IRV-17", "role": "RW", "reason": "tactical"})
        self.lineup["IRV"]["RW"] = "IRV-17"
        self.t += 20_000
        self.force_possession("IRV", (40, 8), "tackle", "IRV-17")
        start = self.ball
        end = self.real("IRV", 80.0, 8.0)  # 40 m
        dur = 4100
        carry = self.emit("carry", "IRV", "IRV-17", start, end, outcome="complete", q={
            "distance_m": 40.0, "sprint_kmh": 35.0, "duration_ms": dur})
        self.t += dur
        self.ball = end
        self.truth["kaddar_sprint"] = {"event_id": carry.event_id, "player_id": "IRV-17",
                                       "from_ms": carry.match_clock_ms,
                                       "to_ms": carry.match_clock_ms + dur,
                                       "substitution_event_id": sub.event_id}

    def late_corner(self):
        """90+3': Kestrel Bay corner, Tazlan's header goes wide."""
        if self.team != "KBY":
            self.force_possession("KBY", (88, 20), "tackle", "KBY-11")
        flag = self.real("KBY", 105.0, 0.0)
        self.holder = "KBY-08"
        self.ball = flag
        end = self.real("KBY", 98.0, 33.0)
        c = self.emit("corner", "KBY", "KBY-08", flag, end, outcome="complete", q={
            "length_m": 36.0, "height": "air", "recipient": "KBY-09", "body_part": "right_foot"})
        self.t += 2200
        shot = self.emit("shot", "KBY", "KBY-09", end, self.real("KBY", 105.0, 42.0),
                         outcome="off_target", q={"speed_kmh": 71.0, "body_part": "head",
                                                  "situation": "set_piece", "on_target": False},
                         related=[c.event_id])
        self.t += 1500
        self.truth["late_corner"] = {"corner_event_id": c.event_id,
                                     "shot_event_id": shot.event_id}
        self.change("IRV", "shot_ended", "IRV-01", self.real("IRV", 5.0, 34.0), dt=300)
        self.t += 4000

    def fenn_red(self):
        """30': Fenn is sent off; Ironvale go to ten men and shift shape."""
        if self.team != "IRV":
            self.force_possession("IRV", (50, 30), "tackle", "IRV-06")
        self.emit("foul", "IRV", "IRV-09", self.ball, outcome="none", q={
            "fouled": "KBY-06", "serious": True})
        card = self.emit("card", "IRV", "IRV-09", self.ball, outcome="red", q={
            "reason": "serious_foul_play"}, dt=2500)
        del self.lineup["IRV"]["ST"]
        shift = self.emit("tactical_shift", "IRV", None, q={
            "formation_from": "4-2-3-1", "formation_to": "4-4-1", "note": "down to ten men"},
            dt=4000)
        self.formation["IRV"] = "4-4-1"
        self.t += 30_000
        self.truth["red_card"] = {"event_id": card.event_id, "player_id": "IRV-09",
                                  "shift_event_id": shift.event_id}
        self.team = "KBY"
        self.holder = self.lineup["KBY"]["CM"]
        self.poss_id += 1
        self.emit("free_kick", "KBY", self.holder, self.ball, outcome="complete", q={})
        self.restart = None

    # ---------- the whole match
    def run(self):
        self.emit("match_metadata", None, None, q={
            "slice": False, "home": "KBY", "away": "IRV", "score": dict(self.score),
            "attacking_direction_p2": P2_DIR, "scenario": self.scenario})
        for team in ("KBY", "IRV"):
            self.emit("lineup", team, None, q={"formation": self.formation[team],
                                               "lineup": dict(self.lineup[team])})
        for period in (1, 2):
            self.period = period
            self.dir = dict(P1_DIR if period == 1 else P2_DIR)
            self.t = 0
            self.pending_restart_clear()
            self.emit("period_start", None, None, q={})
            kick = "KBY" if period == 1 else "IRV"
            self.poss_id += 1
            self.team, self.holder = kick, self.lineup[kick]["CM"]
            self.ball = (PITCH_L / 2, PITCH_W / 2)
            self.emit("kickoff", kick, self.holder, self.ball, q={})
            length = self.periods[period - 1]["length_ms"]
            while self.t < length:
                self.step()
            self.t = max(self.t, length)
            self.periods[period - 1]["length_ms"] = self.t  # never let a period overrun its length
            self.emit("period_end", None, None, q={})
        self.t = self.periods[1]["length_ms"]
        self.emit("match_end", None, None, q={"score": dict(self.score)})
        self.truth["final_score_before_corrections"] = dict(self.score)
        return self.events

    def pending_restart_clear(self):
        self.restart = None
        self.pressured = False
        self.force_back = False
