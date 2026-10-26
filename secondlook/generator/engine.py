"""Possession-chain event engine, v0: the 55'-70' slice (design S3).

Scripted beats: 58' tactical substitution + shift, a ~67' Ironvale goal after an 11-pass move.
Everything else is generated. In the slice only the scripted goal can score.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from ..config import MATCH_ID, PITCH_L, PITCH_W
from ..contracts.events import Event, MatchMetadata
from .roster import CLUBS, build_players, starting_lineup
from .script import (
    BEAT_AFTER_MS,
    SLICE_END_MS,
    SLICE_START_MS,
    SLICE_WINDOWS,
    SUB_AT_MS,
    params,
)
from .shape import nominal_positions, to_norm, to_real

KICKOFF_WALL = datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc)
PERIODS = [{"period": 1, "length_ms": 2_820_000}, {"period": 2, "length_ms": 2_940_000}]
P2_WALL_OFFSET_MS = PERIODS[0]["length_ms"] + 900_000
OTHER = {"KBY": "IRV", "IRV": "KBY"}
DIRECTION = {"KBY": -1, "IRV": 1}  # period 2 attacking direction along x
PITCH_H_MID = PITCH_W / 2


def r2(v):
    return None if v is None else round(float(v), 2)


class Engine:
    def __init__(self, seed: int):
        self.seed = seed
        self.rng = np.random.Generator(np.random.PCG64(seed))
        self.players = build_players()
        self.by_id = {p.player_id: p for p in self.players}
        self.lineup = {t: starting_lineup(t, self.players) for t in ("KBY", "IRV")}
        self.formation = {"KBY": "4-3-3", "IRV": "4-2-3-1"}
        self.period = 2
        self.dir = dict(DIRECTION)
        self.windows = SLICE_WINDOWS
        self.random_goals = False
        self.yellows: set[str] = set()
        self.foul_p = 0.012
        self.corner_save_p = 0.4
        self.corner_block_p = 0.35
        self.t = SLICE_START_MS
        self.seq = 0
        self.events: list[Event] = []
        self.poss_id = 1
        self.score = {"KBY": 1, "IRV": 0}
        self.team = "KBY"
        self.holder = self.lineup["KBY"]["CM"]
        self.ball = to_real(self.dir["KBY"], 50.0, 22.0)
        self.pressured = False
        self.force_back = False
        self.restart: tuple[str, tuple[float, float]] | None = None
        self.sub_done = False
        self.beat_done = False
        self.truth: dict = {}

    # ---------- helpers
    def metadata(self) -> MatchMetadata:
        return MatchMetadata(
            match_id=MATCH_ID, seed=self.seed, home=CLUBS["KBY"], away=CLUBS["IRV"],
            players=self.players, venue="Saltmarsh Ground", referee="A. Durrant",
            periods=getattr(self, "periods", PERIODS),
        )

    def wall(self, clock_ms: int) -> str:
        off = P2_WALL_OFFSET_MS if self.period == 2 else 0
        ts = KICKOFF_WALL + timedelta(milliseconds=off + clock_ms)
        return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"

    def emit(self, type_, team, player, pos=None, end=None, outcome=None, q=None,
             related=None, up=False, dt=0) -> Event:
        self.seq += 1
        clock = int(self.t + dt)
        e = Event(
            event_id=f"{MATCH_ID}-e{self.seq:05d}", match_id=MATCH_ID, seq=self.seq, type=type_,
            period=self.period, match_clock_ms=clock, wall_ts=self.wall(clock), team_id=team,
            player_id=player, possession_id=self.poss_id,
            x=r2(pos[0]) if pos else None, y=r2(pos[1]) if pos else None,
            end_x=r2(end[0]) if end else None, end_y=r2(end[1]) if end else None,
            outcome=outcome, under_pressure=up, related_ids=related or [], qualifiers=q or {},
        )
        self.events.append(e)
        return e

    def nom(self, team):
        return nominal_positions(self.formation[team], self.lineup[team], self.dir[team],
                                 self.ball, self.team == team)

    def norm(self, team, pos):
        return to_norm(self.dir[team], *pos)

    def real(self, team, nx, ny):
        return to_real(self.dir[team], nx, ny)

    def gk(self, team):
        return self.lineup[team]["GK"]

    def nearest(self, team, pos, exclude=()):
        best, bd = None, 1e9
        for p, (x, y) in self.nom(team).items():
            if p in exclude or self.by_id[p].role == "GK":
                continue
            d = math.hypot(x - pos[0], y - pos[1])
            if d < bd:
                best, bd = p, d
        return best

    def change(self, new_team, cause, holder, pos, dt=0):
        old = self.team
        self.poss_id += 1
        self.team, self.holder, self.ball = new_team, holder, pos
        self.pressured = False
        self.force_back = False
        self.emit("possession_change", new_team, holder, pos,
                  q={"cause": cause, "from_team": old}, dt=dt)

    def rt(self, lo, hi):
        return float(self.rng.uniform(lo, hi))

    # ---------- run
    def run(self) -> list[Event]:
        self.emit("match_metadata", None, None, q={
            "slice": True, "from_min": 55, "to_min": 70, "home": "KBY", "away": "IRV",
            "score": dict(self.score), "attacking_direction_p2": DIRECTION})
        for team in ("KBY", "IRV"):
            self.emit("lineup", team, None, q={
                "formation": self.formation[team], "lineup": dict(self.lineup[team])})
        while self.t < SLICE_END_MS:
            self.step()
        return self.events

    def prm(self, team):
        return params(team, self.t, self.windows, CLUBS[team].style, self.period)

    def step(self):
        if not self.sub_done and self.t >= SUB_AT_MS:
            return self.do_sub()
        if not self.beat_done and self.t >= BEAT_AFTER_MS:
            return self.do_beat()
        return self.play()

    def play(self):
        if self.restart:
            etype, pos = self.restart
            self.restart = None
            self.ball = pos
            return self.do_pass(etype)
        team, opp = self.team, OTHER[self.team]
        P, Q = self.prm(team), self.prm(opp)
        if self.rng.random() < self.foul_p:
            return self.do_foul()
        if self.rng.random() < Q["press"] and not self.force_back:
            if self.do_pressure(P, Q):
                return
        nx, ny = self.norm(team, self.ball)
        if nx > 80 and self.rng.random() < P["shot"] * (0.6 + 0.4 * (nx - 80) / 25):
            return self.do_shot()
        if self.by_id[self.holder].role != "GK" and self.rng.random() < 0.18:
            return self.do_carry()
        return self.do_pass("pass")

    # ---------- actions
    def do_pressure(self, P, Q) -> bool:
        _team, opp = self.team, OTHER[self.team]
        presser = self.nearest(opp, self.ball)
        dur = int(self.rt(600, 1800))
        pft = float(np.clip(0.20 - 1.5 * P["pass_skill"], 0.08, 0.35))
        r = self.rng.random()
        q = {"target_player_id": self.holder, "duration_ms": dur}
        if r < pft:
            q["result"] = "forced_turnover"
            self.emit("pressure", opp, presser, self.ball, outcome="forced_turnover", q=q)
            self.emit("tackle", opp, presser, self.ball, outcome="won",
                      q={"victim": self.holder}, dt=dur)
            self.t += dur + 300
            self.change(opp, "tackle", presser, self.ball)
            return True
        if r < pft + 0.3:
            q["result"] = "forced_back"
            self.force_back = True
        else:
            q["result"] = "none"
        self.emit("pressure", opp, presser, self.ball, outcome=q["result"], q=q)
        self.pressured = True
        self.t += int(dur * 0.3)
        return False

    def do_foul(self):
        team, opp = self.team, OTHER[self.team]
        fouler = self.nearest(opp, self.ball)
        self.emit("foul", opp, fouler, self.ball, outcome="none", q={"fouled": self.holder})
        if (self.random_goals and fouler not in self.yellows
                and self.rng.random() < self.card_prob()):
            self.yellows.add(fouler)
            self.emit("card", opp, fouler, self.ball, outcome="yellow", q={"reason": "foul"},
                      dt=1500)
        self.t += int(self.rt(3000, 6000))
        self.restart = ("free_kick", self.ball)
        self.pressured = False
        self.team = team

    def do_carry(self):
        team = self.team
        nx, ny = self.norm(team, self.ball)
        tx = float(np.clip(nx + self.rt(4, 14), 2, 103))
        ty = float(np.clip(ny + self.rt(-6, 6), 2, 66))
        end = self.real(team, tx, ty)
        dur = int(self.rt(1500, 3000) * self.prm(team)["tempo"])
        self.emit("carry", team, self.holder, self.ball, end, outcome="complete",
                  q={"distance_m": r2(math.hypot(tx - nx, ty - ny))}, up=self.pressured)
        self.t += dur
        self.ball = end
        self.pressured = False

    def pick_receiver(self, team, forward):
        nom = self.nom(team)
        hx, hy = self.ball
        d_ = self.dir[team]
        hn = self.norm(team, self.ball)[0]
        ids, w = [], []
        for p, (x, y) in nom.items():
            if p == self.holder:
                continue
            if self.by_id[p].role == "GK" and hn > 30:
                continue
            d = math.hypot(x - hx, y - hy)
            if d < 4 or d > 45:
                continue
            dxn = (x - hx) * d_
            ids.append(p)
            w.append(math.exp(-d / 16) * (0.05 + 1 + forward * float(np.clip(dxn / 15, -1, 1))))
        if not ids:
            ids = [p for p in nom if p != self.holder]
            w = [1.0] * len(ids)
        w = np.array(w) / sum(w)
        return str(self.rng.choice(ids, p=w)), nom

    def do_pass(self, etype="pass"):
        team, opp = self.team, OTHER[self.team]
        P = self.prm(team)
        fwd = 0.0 if self.force_back else P["forward"]
        if etype == "corner":
            roles = [r for r in ("ST", "CB1", "CB2", "AM") if r in self.lineup[team]]
            recv_id = self.lineup[team][str(self.rng.choice(roles))]
            if recv_id == self.holder:
                recv_id = next(v for k, v in self.lineup[team].items() if k != "GK"
                               and v != self.holder)
            end = self.real(team, self.rt(92, 100), self.rt(24, 44))
        else:
            recv_id, nom = self.pick_receiver(team, fwd)
            rx, ry = nom[recv_id]
            end = (float(np.clip(rx + self.rt(-1.5, 1.5), 0.5, PITCH_L - 0.5)),
                   float(np.clip(ry + self.rt(-1.5, 1.5), 0.5, PITCH_W - 0.5)))
        start = self.ball
        dist = math.hypot(end[0] - start[0], end[1] - start[1])
        rating = self.by_id[self.holder].ratings.get("passing", 0.6)
        pc = 0.90 - 0.004 * max(0.0, dist - 12) - (0.10 if self.pressured else 0.0) \
            + P["pass_skill"] + 0.1 * (rating - 0.6)
        if etype == "corner":
            pc = 0.40
        pc = float(np.clip(pc, 0.40, 0.97))
        ok = self.rng.random() < pc
        dur = int((1400 + 90 * dist) * P["tempo"] * self.rt(0.8, 1.2))
        en = self.norm(team, end)
        sn = self.norm(team, start)
        q = {
            "length_m": r2(dist), "height": "air" if dist > 30 or etype == "corner" else "ground",
            "body_part": str(self.rng.choice(["right_foot", "left_foot"], p=[0.7, 0.3])),
            "through_ball": bool(en[0] - sn[0] > 12 and abs(en[1] - 34) < 14 and dist < 30),
            "switch": bool(abs(end[1] - start[1]) > 25),
            "recipient": recv_id,
        }
        pass_ev = self.emit(etype, team, self.holder, start, end,
                            outcome="complete" if ok else "incomplete", q=q, up=self.pressured)
        self.t += dur
        if ok:
            self.holder, self.ball = recv_id, end
            self.pressured = False
            self.force_back = False
            return
        if self.rng.random() < 0.7 or etype == "corner":
            defender = self.nearest(opp, end)
            kind = "clearance" if en[0] > 88 else "interception"
            self.emit(kind, opp, defender, end, outcome="won" if kind == "interception" else "none",
                      q={"intended": recv_id}, related=[pass_ev.event_id])
            self.change(opp, kind, defender, end)
        else:
            ex = float(np.clip(end[0], 1, PITCH_L - 1))
            ey = 0.0 if end[1] < PITCH_H_MID else PITCH_W
            pos = (ex, ey)
            taker = self.nearest(opp, pos)
            self.change(opp, "out_of_play", taker, pos)
            self.restart = ("throw_in", pos)
            self.t += int(self.rt(2000, 5000))

    def do_shot(self, scripted=False):
        team, opp = self.team, OTHER[self.team]
        nx, ny = self.norm(team, self.ball)
        speed = float(np.clip(self.rng.normal(80, 13), 52, 118))
        body = str(self.rng.choice(["right_foot", "left_foot", "head"], p=[0.5, 0.3, 0.2]))
        if self.random_goals and self.rng.random() < 0.105:
            return self.do_random_goal(speed, body)
        r = self.rng.random()
        outcome = "saved" if r < 0.30 else "off_target" if r < 0.70 else "blocked"
        gy = self.rt(30, 38) if outcome == "saved" else self.rt(20, 48)
        end = self.real(team, 105.0, gy)
        q = {"speed_kmh": r2(speed), "body_part": body, "situation": "open_play",
             "on_target": outcome == "saved"}
        shot = self.emit("shot", team, self.holder, self.ball, end, outcome=outcome, q=q,
                         up=self.pressured)
        self.t += 600
        gk = self.gk(opp)
        corner = self.real(team, 105.0, 0.0 if ny < 34 else 68.0)
        if outcome == "saved":
            self.emit("save", opp, gk, end, outcome="saved", related=[shot.event_id])
            if self.rng.random() < self.corner_save_p:
                self.t += int(self.rt(6000, 10000))
                self.corner(team, corner)
            else:
                self.change(opp, "shot_ended", gk, self.real(opp, 5.0, 34.0), dt=300)
                self.t += 3000
        elif outcome == "off_target":
            self.change(opp, "shot_ended", gk, self.real(opp, 5.0, 34.0), dt=300)
            self.t += int(self.rt(6000, 9000))
            self.restart = ("goal_kick", self.ball)
        else:
            d = self.nearest(opp, self.ball)
            self.emit("block", opp, d, self.ball, outcome="won", related=[shot.event_id])
            if self.rng.random() < self.corner_block_p:
                self.t += int(self.rt(6000, 10000))
                self.corner(team, corner)
            else:
                self.change(opp, "block", d, self.ball, dt=200)
                self.t += 1500
        self.pressured = False

    def do_random_goal(self, speed, body):
        team, opp = self.team, OTHER[self.team]
        end = self.real(team, 105.0, self.rt(30, 38))
        shot = self.emit("shot", team, self.holder, self.ball, end, outcome="scored", q={
            "speed_kmh": r2(speed), "body_part": body, "situation": "open_play", "on_target": True},
            up=self.pressured)
        self.score[team] += 1
        self.emit("goal", team, self.holder, end, outcome="scored", q={
            "home_score": self.score["KBY"], "away_score": self.score["IRV"]},
            related=[shot.event_id], dt=400)
        self.kickoff_after_goal(opp)

    def card_prob(self) -> float:
        return 0.12

    def kickoff_after_goal(self, receiver_team):
        self.t += 40_000
        self.poss_id += 1
        role = next(r for r in ("ST", "AM", "CM") if r in self.lineup[receiver_team])
        self.team, self.holder = receiver_team, self.lineup[receiver_team][role]
        self.ball = (PITCH_L / 2, PITCH_W / 2)
        self.emit("kickoff", receiver_team, self.holder, self.ball, q={"after_goal": True})
        self.pressured = False

    def corner(self, team, flag):
        taker = self.lineup[team][str(self.rng.choice(["CM", "AM"]))]
        self.holder = taker
        self.restart = ("corner", flag)

    def do_sub(self):
        self.sub_done = True
        e = self.emit("substitution", "KBY", "KBY-07", q={
            "on": "KBY-14", "role": "RW", "reason": "tactical"})
        self.lineup["KBY"]["RW"] = "KBY-14"
        if self.holder == "KBY-07":
            self.holder = "KBY-14"
        t = self.emit("tactical_shift", "KBY", "KBY-06", q={
            "formation_from": "4-3-3", "formation_to": "4-1-4-1", "deeper_player_id": "KBY-06",
            "note": "holding midfielder drops deeper; a gap opens in midfield"}, dt=2000)
        self.formation["KBY"] = "4-1-4-1"
        self.truth["substitution_event_id"] = e.event_id
        self.truth["tactical_shift_event_id"] = t.event_id
        self.t += 20_000

    def do_beat(self):
        self.beat_done = True
        irv, kby = "IRV", "KBY"
        # 1. Ironvale win the ball in the midfield gap
        if self.team == irv:
            h = self.holder
            stray = self.emit("pass", irv, h, self.ball, self.real(irv, 40.0, 40.0),
                              outcome="incomplete", q={"length_m": 14.0, "height": "ground"})
            self.t += 1800
            d = self.nearest(kby, self.real(irv, 40.0, 40.0))
            self.emit("interception", kby, d, self.real(irv, 40.0, 40.0), outcome="won",
                      related=[stray.event_id])
            self.change(kby, "interception", d, self.real(irv, 40.0, 40.0))
            self.t += 2200
        victim = self.holder
        pos = self.real(irv, 46.0, 30.0)
        self.ball = pos
        rec = self.emit("tackle", irv, "IRV-08", pos, outcome="won", q={"victim": victim})
        self.t += 400
        self.change(irv, "tackle", "IRV-08", pos)
        self.truth["recovery_event_id"] = rec.event_id
        # 2. the 11-pass move
        seq = [
            ("IRV-06", (40, 26)), ("IRV-04", (30, 24)), ("IRV-05", (29, 42)), ("IRV-08", (44, 40)),
            ("IRV-02", (50, 9)), ("IRV-07", (62, 11)), ("IRV-10", (63, 32)), ("IRV-09", (73, 36)),
            ("IRV-10", (71, 31)), ("IRV-11", (69, 56)), ("IRV-10", (84, 35)),
        ]
        chain_ids = []
        for i, (to, (nx, ny)) in enumerate(seq, start=1):
            if i in (4, 8):
                self.emit("pressure", kby, "KBY-08" if i == 4 else "KBY-10", self.ball,
                          outcome="none", q={"target_player_id": self.holder,
                                             "duration_ms": 900, "result": "none"})
                self.pressured = True
            end = self.real(irv, nx + self.rt(-0.8, 0.8), ny + self.rt(-0.8, 0.8))
            start = self.ball
            dist = math.hypot(end[0] - start[0], end[1] - start[1])
            dur = int(self.rt(2000, 3200))
            ev = self.emit("pass", irv, self.holder, start, end, outcome="complete", q={
                "length_m": r2(dist), "height": "ground", "body_part": "right_foot",
                "through_ball": i == 7, "switch": bool(abs(end[1] - start[1]) > 25),
                "cutback": i == 11, "recipient": to}, up=self.pressured)
            chain_ids.append(ev.event_id)
            self.t += dur
            self.holder, self.ball = to, end
            self.pressured = False
        # 3. Vask shoots and scores
        shot_end = self.real(irv, 105.0, 36.5)
        speed = self.rt(84, 92)
        shot = self.emit("shot", irv, "IRV-10", self.ball, shot_end, outcome="scored", q={
            "speed_kmh": r2(speed), "body_part": "right_foot", "situation": "open_play",
            "on_target": True}, related=[chain_ids[-1]])
        self.score[irv] += 1
        goal = self.emit("goal", irv, "IRV-10", shot_end, outcome="scored", q={
            "home_score": self.score[kby], "away_score": self.score[irv]},
            related=[shot.event_id], dt=400)
        self.truth["goal"] = {
            "event_id": goal.event_id, "shot_event_id": shot.event_id,
            "chain_pass_event_ids": chain_ids, "chain_passes": len(chain_ids),
            "true_thesis_tag": "sustained_possession",
            "explanation": "Ironvale won the ball in the midfield gap Kestrel Bay opened after "
                           "their 58' shift, then moved it through 11 passes to Vask.",
        }
        # 4. restart
        self.t += 40_000
        self.poss_id += 1
        self.team, self.holder = kby, "KBY-09"
        self.ball = (PITCH_L / 2, PITCH_W / 2)
        self.emit("kickoff", kby, "KBY-09", self.ball, q={"after_goal": True})
        self.pressured = False

