"""Broadcast graphics (P13): which layout a cue uses and the typed card the overlay draws.

Every number on a card is either one of the fact's own `text.numbers` (as written in the sentence)
or, on a stat comparison, the other team's value for the same metric and window, recomputed from
the event log by the metric registry — the computation the verifier runs. app/renditions.py
checks both before a cue can air. Labels are fixed words with no digits.

Spanish labels are unreviewed, like the rest of the Spanish text.
"""

from __future__ import annotations

from core.config import MetricContext, Settings
from core.events import Match
from core.facts import (
    Card,
    Fact,
    Layout,
    LowerThirdCard,
    PlayerCard,
    Side,
    StatComparisonCard,
    Subject,
    TickerCard,
)
from core.metrics import REGISTRY, compute_metric

Lang = str
LABELS: dict[str, tuple[str, str]] = {  # metric -> (en, es)
    "xg_sum": ("Expected goals (xG)", "Goles esperados (xG)"),
    "pass_accuracy": ("Pass accuracy", "Precisión de pase"),
    "shots": ("Shots", "Disparos"),
    "xt_added": ("Expected threat added (xT)", "Amenaza esperada añadida (xT)"),
    "most_central_player": ("Most central passer · linked passes", "Pasador más central · pases"),
}
WORDS: dict[str, tuple[str, str]] = {
    "first_half": ("First half", "Primera parte"),
    "second_half": ("Second half", "Segunda parte"),
    "goal": ("Goal", "Gol"),
    "match": ("Match", "Partido"),
    "of": ("of", "de"),
}


def word(key: str, lang: Lang) -> str:
    en, es = WORDS.get(key) or LABELS[key]
    return es if lang == "es" else en


def layout_for(fact: Fact, s: Settings) -> Layout:
    r, metric, kind = s.renditions, fact.claim.metric, fact.claim.subject.type
    if kind == "team" and metric in r.comparison_metrics:
        return "stat_comparison"
    if kind == "player" and metric in r.player_card_metrics:
        return "player_card"
    return "ticker" if metric in r.ticker_metrics else "lower_third"


def shown(value: float, metric: str, lang: Lang) -> str:
    """A value written the way the sentences write it (and the number check reads it)."""
    kind = REGISTRY[metric].kind
    if kind == "share":
        return f"{round(value)}{' %' if lang == 'es' else '%'}"
    if kind == "sum":
        text = f"{value:.2f}"
        return text.replace(".", ",") if lang == "es" else text
    return str(round(value))


def period(fact: Fact, match: Match, lang: Lang) -> str:
    w, half = fact.claim.window, match.meta.clock.half_t
    if w.start_t == 0 and w.end_t <= half:
        return word("first_half", lang)
    if w.start_t >= half:
        return word("second_half", lang)
    return ""


def _number(fact: Fact, key: str) -> float | None:
    return next((n.value for n in fact.text.numbers if n.key == key), None)


def team_of_player(match: Match, player_id: str) -> str:
    return next(t for t, team in match.meta.teams.items() if player_id in team.players)


def other_value(fact: Fact, match: Match, s: Settings) -> float | None:
    """The other team's value for the claim's metric and window, from the event log up to the
    fact's time, through the registry."""
    me = fact.claim.subject.id
    other = match.meta.away if me == match.meta.home else match.meta.home
    known = [e for e in match.events if e.t <= fact.created_t]
    m = compute_metric(
        fact.claim.metric,
        Subject(type="team", id=other),
        fact.claim.window,
        known,
        MetricContext.of(s),
    )
    return None if m is None or m.n == 0 else m.value


def _comparison(fact: Fact, text: str, match: Match, s: Settings, lang: Lang) -> Card | None:
    mine, theirs = _number(fact, "value"), other_value(fact, match, s)
    if mine is None or theirs is None:
        return None
    metric, me, meta = fact.claim.metric, fact.claim.subject.id, match.meta
    values = {me: mine, (meta.away if me == meta.home else meta.home): theirs}
    side = {
        t: Side(team=t, name=meta.team_name(t), shown=shown(values[t], metric, lang))
        for t in (meta.home, meta.away)
    }
    return StatComparisonCard(
        layout="stat_comparison",
        label=word(metric, lang),
        period=period(fact, match, lang),
        subject=me,
        home=side[meta.home],
        away=side[meta.away],
        text=text,
    )


def _figure(fact: Fact, lang: Lang) -> str | None:
    if fact.claim.metric == "most_central_player":
        k, n = _number(fact, "k"), _number(fact, "n")
        return None if k is None or n is None else f"{round(k)} {word('of', lang)} {round(n)}"
    v = _number(fact, "value")
    return None if v is None else shown(v, fact.claim.metric, lang)


def _player(fact: Fact, text: str, match: Match, lang: Lang) -> Card | None:
    figure, pid = _figure(fact, lang), fact.claim.subject.id
    if figure is None:
        return None
    team = team_of_player(match, pid)
    return PlayerCard(
        layout="player_card",
        player_id=pid,
        name=match.meta.names()[pid],
        team=team,
        team_name=match.meta.team_name(team),
        label=word(fact.claim.metric, lang),
        figure=figure,
        period=period(fact, match, lang),
        text=text,
    )


def lower_third_label(fact: Fact, match: Match, lang: Lang) -> str:
    sub = fact.claim.subject
    if fact.kind == "score_event":
        return word("goal", lang)
    if sub.type == "player":
        return match.meta.names()[sub.id]
    return match.meta.team_name(sub.id) if sub.type == "team" else word("match", lang)


def build_card(fact: Fact, text: str, match: Match, s: Settings, lang: Lang) -> Card:
    """The card for one cue. A card that lacks a number it needs falls back to a lower third."""
    layout = layout_for(fact, s)
    card: Card | None = None
    if layout == "stat_comparison":
        card = _comparison(fact, text, match, s, lang)
    elif layout == "player_card":
        card = _player(fact, text, match, lang)
    elif layout == "ticker":
        card = TickerCard(layout="ticker", text=text)
    return card or LowerThirdCard(
        layout="lower_third", label=lower_third_label(fact, match, lang), text=text
    )
