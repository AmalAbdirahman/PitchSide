"""Spanish captions from templates and a small football glossary.

The text is built from the fact's structured fields (claim, numbers, evidence),
not translated from the English, so words removed by the verifier cannot
reappear. Not reviewed by a native speaker.
"""
from __future__ import annotations

from typing import Any

from core.config import load

Fact = dict[str, Any]
Match = dict[str, Any]

GLOSSARY: dict[str, str] = {
    "left": "la banda izquierda", "right": "la banda derecha", "centre": "el centro",
    "first": "primera", "second": "segunda", "yellow": "amarilla", "red": "roja",
    "goal": "gol", "substitution": "cambio", "own third": "tercio defensivo", "xG": "xG",
}


def es_number(v: float, decimals: int = 0) -> str:
    """Spanish formatting: decimal comma."""
    return f"{v:.{decimals}f}".replace(".", ",")


def _nums(fact: Fact) -> dict[str, float]:
    return {n["key"]: n["value"] for n in fact["text"]["numbers"]}


def _team(match: Match, tid: str) -> str:
    return match["meta"]["teams"][tid]["name"]


def _player(match: Match, pid: str) -> str:
    for t in match["meta"]["teams"].values():
        if pid in t["players"]:
            return t["players"][pid]
    raise KeyError(pid)


def _team_of(match: Match, pid: str) -> str:
    return next(t["name"] for t in match["meta"]["teams"].values() if pid in t["players"])


def _half(fact: Fact, match: Match) -> str:
    second = fact["claim"]["window"]["start_t"] >= load()["sim"]["half_s"]
    return GLOSSARY["second" if second else "first"]


def _event_fact(fact: Fact, match: Match, n: dict[str, float]) -> str:
    metric, subj, m = fact["claim"]["metric"], fact["claim"]["subject"], int(n["minute"])
    if metric == "goals":
        home, away = match["meta"]["home"], match["meta"]["away"]
        return (f"¡GOL! {_player(match, subj['id'])} marca para {_team_of(match, subj['id'])} en el minuto {m}. "
                f"{_team(match, home)} {int(n['score_' + home])}–{int(n['score_' + away])} {_team(match, away)}.")
    if metric == "substitutions":
        on, off = (_player(match, p) for p in fact["evidence"]["players"])
        return f"Cambio en {_team(match, subj['id'])} en el minuto {m}: entra {on}, sale {off}."
    pid = subj["id"]
    return f"Tarjeta {GLOSSARY['yellow']} para {_player(match, pid)} ({_team_of(match, pid)}) en el minuto {m}."


def _share_fact(fact: Fact, match: Match, n: dict[str, float]) -> str:
    claim, hedge = fact["claim"], fact.get("uncertainty", {}).get("hedge", "clear")
    team = _team(match, claim["subject"]["id"])
    if claim["metric"] == "pass_accuracy":
        if "k" in n:
            return f"Primeras señales: {team} completó {int(n['k'])} de {int(n['n'])} pases en este tramo."
        return f"{team} completó el {int(n['value'])} % de sus pases en la {_half(fact, match)} parte."
    lane = GLOSSARY[claim["metric"].removeprefix("attack_share_")]
    since = int(n["since_minute"])
    if "k" in n:
        return (f"Primeras señales: {int(n['k'])} de {int(n['n'])} ataques de {team} han ido por {lane} "
                f"desde el minuto {since}.")
    if hedge == "suggests":
        return (f"Los datos sugieren que {team} carga el juego por {lane}: el {int(n['value'])} % de sus ataques "
                f"desde el minuto {since}.")
    return f"Desde el minuto {since}, el {int(n['value'])} % de los ataques de {team} han ido por {lane}."


def caption_es(fact: Fact, match: Match) -> str:
    """Spanish caption for a routed fact."""
    n, claim = _nums(fact), fact["claim"]
    metric, subj = claim["metric"], claim["subject"]
    if metric in ("goals", "substitutions", "cards"):
        return _event_fact(fact, match, n)
    if metric.startswith("attack_share_") or metric == "pass_accuracy":
        return _share_fact(fact, match, n)
    half = _half(fact, match)
    if metric == "turnovers_own_third":
        return (f"{_team(match, subj['id'])} ha perdido el balón {int(n['value'])} veces en su "
                f"{GLOSSARY['own third']} desde el minuto {int(n['since_minute'])}.")
    if metric == "xg_sum":
        return f"{_team(match, subj['id'])} generó {es_number(n['value'], 2)} de xG en la {half} parte."
    if metric == "top_shot_speed":
        shooter = _player(match, fact["evidence"]["players"][0])
        return f"Disparo más rápido de la {half} parte: {int(n['value'])} km/h, de {shooter}."
    if metric == "shots":
        return f"{_player(match, subj['id'])} realizó {int(n['value'])} disparos en la {half} parte."
    raise ValueError(f"no Spanish template for {metric}")
