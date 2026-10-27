"""Seeded match simulator: one hero match with a planted story, plus a
background league used only for the league mean xG and realism checks.

Run: python -m sim.generate --seed 7

Conventions: metres on a 105 x 68 pitch; every event's x, y are in the acting
team's frame (it attacks left to right, y = 0 on its left touchline).
A `possession_change` event is credited to the team that LOST the ball.
In the hero match goals are scripted (only the 63' goal); random shots cannot score.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from typing import Any

from core.config import ROOT, load
from core.shares import lane_of
from core.xg import xg as shot_xg
from sim.data_card import write_data_card

Event = dict[str, Any]

ROSTERS: dict[str, dict[str, Any]] = {
    "NOR": {"name": "Northbridge", "players": [
        ("NOR_01", "R. Ashdown", "GK"), ("NOR_02", "L. Corrigan", "D"), ("NOR_03", "M. Dube", "D"),
        ("NOR_04", "S. Kettering", "D"), ("NOR_05", "J. Whitlow", "D"), ("NOR_06", "P. Okonjo", "M"),
        ("NOR_08", "E. Lindqvist", "M"), ("NOR_10", "F. Moreau-Lake", "M"), ("NOR_07", "D. Varga", "AL"),
        ("NOR_09", "C. Nkemelu", "AC"), ("NOR_11", "K. Tamsin", "AR"), ("NOR_12", "B. Adeyemi-Ross", "AL"),
    ]},
    "HAR": {"name": "Harlow", "players": [
        ("HAR_01", "G. Pell", "GK"), ("HAR_02", "N. Ostrander", "D"), ("HAR_03", "H. Quarry", "D"),
        ("HAR_04", "V. Seddon", "D"), ("HAR_05", "I. Marchetti-Holt", "D"), ("HAR_06", "W. Abara", "M"),
        ("HAR_08", "O. Fenwick", "M"), ("HAR_10", "Z. Halloran", "M"), ("HAR_07", "Y. Castell", "AL"),
        ("HAR_09", "T. Brannock", "AC"), ("HAR_11", "U. Kerridge", "AR"), ("HAR_12", "A. Somerby", "M"),
    ]},
}
HERO_SUB = ("NOR", "NOR_07", "NOR_12")
HERO_AWAY_SUB = ("HAR", "HAR_10", "HAR_12")
LANE_Y = {"left": 11.3, "centre": 34.0, "right": 56.7}


def sigmoid(v: float) -> float:
    return 1 / (1 + math.exp(-v))


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class Match:
    """Incremental simulator state for one match."""

    def __init__(self, seed: int, hero: bool) -> None:
        self.cfg = load()
        self.s = self.cfg["sim"]
        self.rng = random.Random(seed)
        self.hero = hero
        self.events: list[Event] = []
        self.t = 0.0
        self.period = 1
        self.score = {"NOR": 0, "HAR": 0}
        self.roles = {pid: role for team in ROSTERS.values() for pid, _, role in team["players"]}
        self.on_pitch = {k: [p[0] for p in v["players"][:11]] for k, v in ROSTERS.items()}
        self.done: set[str] = set()
        self.start_possession("NOR", 52.5, 34.0)

    # ---- bookkeeping -------------------------------------------------
    def emit(self, etype: str, team: str, player: str, x: float, y: float, dt: float = 0.0, **f: Any) -> Event:
        e: Event = {"id": len(self.events) + 1, "t": round(self.t + dt, 1), "period": self.period,
                    "type": etype, "team": team, "player_id": player, "x": round(x, 1), "y": round(y, 1)}
        e.update({k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items()})
        self.events.append(e)
        return e

    def other(self, team: str) -> str:
        return "HAR" if team == "NOR" else "NOR"

    def start_possession(self, team: str, x: float, y: float) -> None:
        self.team, self.x, self.y = team, x, y
        weights = self.s["lane_weights"]
        if self.overload_active(team):
            weights = self.s["overload_lane_weights"]
        lane = self.rng.choices(["left", "centre", "right"], weights=weights)[0]
        self.target_y = clamp(LANE_Y[lane] + self.rng.gauss(0, 5), 2, 66)

    def overload_active(self, team: str) -> bool:
        return (self.hero and team == "NOR" and "sub" in self.done
                and self.t < self.s["hero_overload_until_t"])

    def pressing(self) -> bool:
        return (self.hero and self.team == "HAR" and self.t >= self.s["hero_press_from_t"]
                and self.x < self.cfg["pitch"]["own_third_x"])

    def pick(self, team: str, x: float, y: float, shooting: bool = False) -> str:
        """Choose an on-pitch outfield player, weighted by zone and lane."""
        if shooting or x > 70:
            role_w = {"D": 0.0, "M": 0.35, "A": 0.65}
        elif x > 35:
            role_w = {"D": 0.2, "M": 0.6, "A": 0.2}
        else:
            role_w = {"D": 0.6, "M": 0.4, "A": 0.0}
        lane_role = {"left": "AL", "centre": "AC", "right": "AR"}[lane_of(y)]
        pool, weights = [], []
        for pid in self.on_pitch[team]:
            role = self.roles[pid]
            if role == "GK":
                continue
            w = role_w[role[0]] * (3.0 if role == lane_role else 1.0)
            pool.append(pid)
            weights.append(w + 1e-3)
        return self.rng.choices(pool, weights=weights)[0]

    # ---- actions -----------------------------------------------------
    def turnover(self, player: str, x: float, y: float, dt: float) -> None:
        self.emit("possession_change", self.team, player, x, y, dt)
        L, W = self.cfg["pitch"]["length"], self.cfg["pitch"]["width"]
        self.start_possession(self.other(self.team), L - x, W - y)

    def do_pass(self) -> None:
        s, rng = self.s, self.rng
        player = self.pick(self.team, self.x, self.y)
        pressed = self.pressing()
        pressured = rng.random() < (s["press_pressure_prob"] if pressed else s["pressure_prob"])
        if pressured:
            L, W = self.cfg["pitch"]["length"], self.cfg["pitch"]["width"]
            opp = self.other(self.team)
            self.emit("pressure", opp, self.pick(opp, L - self.x, W - self.y), L - self.x, W - self.y)
        dist = clamp(rng.gauss(16, 7), 4, 50)
        theta = rng.gauss(0.15, 0.9)
        ex = clamp(self.x + dist * math.cos(theta), 1, 104)
        ey = clamp(0.5 * (self.y + dist * math.sin(theta)) + 0.5 * self.target_y + rng.gauss(0, 4), 1, 67)
        pd = math.hypot(ex - self.x, ey - self.y)
        logit = (s["pass_logit_intercept"] + s["pass_logit_dist"] * pd + s["pass_logit_pressure"] * pressured
                 + s["pass_logit_final_third"] * (ex > self.cfg["pitch"]["final_third_x"])
                 + s["press_logit_boost"] * pressed)
        difficulty = sigmoid(logit)
        ok = rng.random() > difficulty
        self.emit("pass", self.team, player, self.x, self.y, 0.5, end_x=round(ex, 1), end_y=round(ey, 1),
                  outcome="success" if ok else "fail", pass_distance=round(pd, 1),
                  pass_difficulty=difficulty, ball_speed=round(clamp(25 + 2.2 * pd + rng.gauss(0, 5), 15, 130), 1))
        if ok:
            self.x, self.y = ex, ey
        else:
            self.turnover(player, ex, ey, 1.0)

    def do_carry(self) -> None:
        rng, s = self.rng, self.s
        player = self.pick(self.team, self.x, self.y)
        d = rng.uniform(3, 15)
        theta = rng.gauss(0, 0.5)
        ex = clamp(self.x + d * math.cos(theta), 1, 104)
        ey = clamp(0.6 * (self.y + d * math.sin(theta)) + 0.4 * self.target_y, 1, 67)
        tackled = rng.random() < s["tackle_prob"]
        self.emit("carry", self.team, player, self.x, self.y, 0.5, end_x=round(ex, 1), end_y=round(ey, 1),
                  outcome="fail" if tackled else "success",
                  player_speed=round(clamp(rng.gauss(20, 4), 8, 34), 1))
        if not tackled:
            self.x, self.y = ex, ey
            return
        L, W = self.cfg["pitch"]["length"], self.cfg["pitch"]["width"]
        opp = self.other(self.team)
        tackler = self.pick(opp, L - ex, W - ey)
        self.emit("tackle", opp, tackler, L - ex, W - ey, 1.0, outcome="success")
        if rng.random() < s["card_prob"]:
            self.emit("card", opp, tackler, L - ex, W - ey, 1.2, card="yellow")
        self.turnover(player, ex, ey, 1.5)

    def do_shot(self, player: str | None = None, force_goal: bool = False) -> None:
        rng = self.rng
        player = player or self.pick(self.team, self.x, self.y, shooting=True)
        p_goal = shot_xg(self.x, self.y)
        if force_goal:
            outcome = "goal"
        elif not self.hero and rng.random() < p_goal:
            outcome = "goal"
        else:
            outcome = rng.choices(["on_target", "off_target", "blocked"], weights=[0.35, 0.45, 0.2])[0]
        self.emit("shot", self.team, player, self.x, self.y, 0.5, end_x=105.0,
                  end_y=round(clamp(34 + rng.gauss(0, 5), 20, 48), 1), outcome=outcome, xg=round(p_goal, 3),
                  shot_speed=round(clamp(rng.gauss(88, 12), 50, 125), 1))
        if outcome == "goal":
            self.score[self.team] += 1
            self.emit("goal", self.team, player, self.x, self.y, 1.0)
            self.start_possession(self.other(self.team), 52.5, 34.0)
        else:
            self.start_possession(self.other(self.team), 6.0, 34.0)

    def step(self) -> None:
        s, rng = self.s, self.rng
        box = self.x > 88 and abs(self.y - 34) < 20
        p_shot = s["shot_prob_box"] if box else (s["shot_prob_long"] if self.x > 75 else 0.0)
        r = rng.random()
        if r < p_shot:
            self.do_shot()
        elif r < p_shot + 0.2:
            self.do_carry()
        else:
            self.do_pass()

    # ---- hero script -------------------------------------------------
    def substitute(self, sub: tuple[str, str, str], t: float) -> None:
        team, off, on = sub
        self.t = t
        self.emit("substitution", team, on, 52.5, 0.0, player_off=off)
        self.on_pitch[team] = [on if p == off else p for p in self.on_pitch[team]]
        self.t = t + 5

    def scripted_goal(self, t_shot: float) -> None:
        """Northbridge build down the left; the new left winger sets up the striker."""
        _, _, winger = HERO_SUB
        self.team = "NOR"
        self.t = t_shot - 20
        self.emit("carry", "NOR", winger, 58.0, 12.0, end_x=80.0, end_y=9.0, outcome="success", player_speed=27.4)
        self.t = t_shot - 12
        self.emit("pass", "NOR", winger, 80.0, 9.0, end_x=95.0, end_y=30.0, outcome="success", pass_distance=25.3,
                  pass_difficulty=0.31, ball_speed=71.0)
        self.t = t_shot - 0.5
        self.x, self.y = 95.0, 30.0
        self.do_shot(player="NOR_09", force_goal=True)
        self.t = t_shot + 40

    def hero_hooks(self) -> None:
        s = self.s
        if "sub" not in self.done and self.t >= s["hero_sub_t"] - 20:
            self.done.add("sub")
            self.substitute(HERO_SUB, s["hero_sub_t"])
        if "goal" not in self.done and self.t >= s["hero_goal_t"] - 40:
            self.done.add("goal")
            self.scripted_goal(s["hero_goal_t"])
        if "away_sub" not in self.done and self.t >= s["hero_away_sub_t"] - 20:
            self.done.add("away_sub")
            self.substitute(HERO_AWAY_SUB, s["hero_away_sub_t"])

    def run(self) -> list[Event]:
        half = self.s["half_s"]
        margin = 2.0  # a step emits up to 1.5 s after its start; keep halves inside [0, half) and [half, 2*half)
        while self.t < 2 * half - margin:
            if self.period == 1 and self.t >= half - margin:
                self.period, self.t = 2, float(half)
                self.start_possession("HAR", 52.5, 34.0)
            if self.hero:
                self.hero_hooks()
            self.step()
            self.t += self.rng.uniform(0.3, 1.7) * self.s["mean_gap_s"]
        return self.events


def league_mu(matches: list[list[Event]], minutes: float) -> float:
    """League mean xG per team per minute."""
    total = sum(e.get("xg", 0.0) for m in matches for e in m if e["type"] == "shot")
    return total / (len(matches) * 2 * minutes)


def roster_meta() -> dict[str, Any]:
    return {k: {"name": v["name"], "players": {pid: name for pid, name, _ in v["players"]}}
            for k, v in ROSTERS.items()}


def main(seed: int) -> None:
    cfg = load()
    hero = Match(seed, hero=True).run()
    league = [Match(seed * 1000 + i, hero=False).run() for i in range(cfg["sim"]["league_matches"])]
    mu = league_mu(league, cfg["winprob"]["match_minutes"])
    meta = {"match_id": cfg["sim"]["hero_match_id"], "seed": seed, "home": "NOR", "away": "HAR",
            "teams": roster_meta(), "synthetic": True}
    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    (out / "events.json").write_text(json.dumps({"meta": meta, "events": hero}, indent=1), encoding="utf-8")
    stats = {"seed": seed, "league_matches": len(league), "mu_xg_per_team_minute": round(mu, 6)}
    (out / "league_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    write_data_card(hero, league, seed, mu)
    print(f"hero events: {len(hero)}  league matches: {len(league)}  mu: {mu:.5f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=load()["seed"])
    main(ap.parse_args().seed)
