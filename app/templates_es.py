"""Spanish captions from templates and a small football glossary.

The text is built from the fact's structured fields (claim, numbers, evidence),
not translated from the English, so words removed by the verifier cannot
reappear. Not reviewed by a native speaker.
"""

from __future__ import annotations

from core.events import Match
from core.facts import Fact

GLOSSARY: dict[str, str] = {
    "left": "la banda izquierda",
    "right": "la banda derecha",
    "centre": "el centro",
    "first": "primera",
    "second": "segunda",
    "yellow": "amarilla",
    "red": "roja",
    "goal": "gol",
    "substitution": "cambio",
    "own third": "tercio defensivo",
    "xG": "xG",
}


def es_number(v: float, decimals: int = 0) -> str:
    """Spanish formatting: decimal comma."""
    return f"{v:.{decimals}f}".replace(".", ",")


def _nums(fact: Fact) -> dict[str, float]:
    return {n.key: n.value for n in fact.text.numbers}


def _team_of(match: Match, pid: str) -> str:
    return match.meta.team_name(match.meta.team_of(pid))


def _half(fact: Fact, half_s: int) -> str:
    second = fact.claim.window.start_t >= half_s
    return GLOSSARY["second" if second else "first"]


def _players(fact: Fact) -> list[str]:
    return fact.evidence.players or []


def _event_fact(fact: Fact, match: Match, n: dict[str, float]) -> str:
    metric, subj, m = fact.claim.metric, fact.claim.subject, int(n["minute"])
    meta = match.meta
    if metric == "goals":
        home, away = meta.home, meta.away
        return (
            f"¡GOL! {meta.player_name(subj.id)} marca para {_team_of(match, subj.id)} en el minuto {m}. "
            f"{meta.team_name(home)} {int(n['score_' + home])}–{int(n['score_' + away])} {meta.team_name(away)}."
        )
    if metric == "substitutions":
        on, off = (meta.player_name(p) for p in _players(fact))
        return f"Cambio en {meta.team_name(subj.id)} en el minuto {m}: entra {on}, sale {off}."
    pid = subj.id
    return f"Tarjeta {GLOSSARY['yellow']} para {meta.player_name(pid)} ({_team_of(match, pid)}) en el minuto {m}."


def _share_fact(fact: Fact, match: Match, n: dict[str, float], half_s: int) -> str:
    claim = fact.claim
    hedge = fact.uncertainty.hedge if fact.uncertainty else "clear"
    team = match.meta.team_name(claim.subject.id)
    if claim.metric == "pass_accuracy":
        if "k" in n:
            return f"Primeras señales: {team} completó {int(n['k'])} de {int(n['n'])} pases en este tramo."
        return f"{team} completó el {int(n['value'])} % de sus pases en la {_half(fact, half_s)} parte."
    lane = GLOSSARY[claim.metric.removeprefix("attack_share_")]
    since = int(n["since_minute"])
    if "k" in n:
        return (
            f"Primeras señales: {int(n['k'])} de {int(n['n'])} ataques de {team} han ido por {lane} "
            f"desde el minuto {since}."
        )
    if hedge == "suggests":
        return (
            f"Los datos sugieren que {team} carga el juego por {lane}: el {int(n['value'])} % de sus ataques "
            f"desde el minuto {since}."
        )
    return f"Desde el minuto {since}, el {int(n['value'])} % de los ataques de {team} han ido por {lane}."


def caption_es(fact: Fact, match: Match, half_s: int) -> str:
    """Spanish caption for a routed fact. `half_s` is the length of a half in seconds."""
    n, claim, meta = _nums(fact), fact.claim, match.meta
    metric, subj = claim.metric, claim.subject
    if metric in ("goals", "substitutions", "cards"):
        return _event_fact(fact, match, n)
    if metric.startswith("attack_share_") or metric == "pass_accuracy":
        return _share_fact(fact, match, n, half_s)
    half = _half(fact, half_s)
    if metric == "turnovers_own_third":
        return (
            f"{meta.team_name(subj.id)} ha perdido el balón {int(n['value'])} veces en su "
            f"{GLOSSARY['own third']} desde el minuto {int(n['since_minute'])}."
        )
    if metric == "xg_sum":
        return (
            f"{meta.team_name(subj.id)} generó {es_number(n['value'], 2)} de xG en la {half} parte."
        )
    if metric == "top_shot_speed":
        shooter = meta.player_name(_players(fact)[0])
        return f"Disparo más rápido de la {half} parte: {int(n['value'])} km/h, de {shooter}."
    if metric == "shots":
        return f"{meta.player_name(subj.id)} realizó {int(n['value'])} disparos en la {half} parte."
    raise ValueError(f"no Spanish template for {metric}")
