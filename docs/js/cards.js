/* Broadcast graphics (P13): pure functions that turn program state into overlay HTML.
   No DOM access, so tests/js/cards.test.mjs runs them in Node. Every number drawn here comes
   from a cue's card or text, built and number-checked at build time (app/cards.py), or from the
   score timeline and clock in the bundle. */

const PERIOD_START_MINUTE = [0, 45, 90, 105];

export const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* Official minute for match time t, as core/events.Clock.minute computes it. */
export function minuteOf(clock, t) {
  let i = 0;
  clock.period_starts.forEach((start, k) => { if (start <= t) i = k; });
  return PERIOD_START_MINUTE[i] + Math.floor((t - clock.period_starts[i]) / 60) + 1;
}

/* Goals for each team at match time t, from the bundle's score timeline. */
export function scoreAt(timeline, t, home, away) {
  let row = timeline[0];
  for (const r of timeline) if (r.t <= t) row = r;
  return { [home]: row[home], [away]: row[away] };
}

/* The message the control room sends and the overlay draws. */
export function programState(meta, timeline, t, cue) {
  const { home, away, teams, clock } = meta;
  const score = scoreAt(timeline, t, home, away);
  return {
    v: 1,
    minute: minuteOf(clock, t),
    home: { code: home, name: teams[home].name, score: score[home] },
    away: { code: away, name: teams[away].name, score: score[away] },
    cue: cue ? { fact_id: cue.fact_id, lang: cue.lang, layout: cue.layout, card: cue.card } : null,
  };
}

export function bugHtml(st) {
  return `<span class="team home"><i></i><b>${esc(st.home.code)}</b></span>` +
    `<span class="score">${esc(st.home.score)}–${esc(st.away.score)}</span>` +
    `<span class="team away"><b>${esc(st.away.code)}</b><i></i></span>` +
    `<span class="min">${esc(st.minute)}'</span>`;
}

/* A shown value as a number, for bar lengths only ("67%", "67 %", "0,27" -> 67, 67, 0.27). */
const asNumber = shown => Number(String(shown).replace('%', '').replace(',', '.').trim());

function compareHtml(c, side) {
  const vals = [asNumber(c.home.shown), asNumber(c.away.shown)];
  const share = String(c.home.shown).includes('%');
  const top = share ? 100 : Math.max(...vals, 1e-9);
  const row = (s, v, cls) => `<div class="row ${cls}${s.team === c.subject ? ' subject' : ''}">` +
    `<span class="name">${esc(s.name)}</span><span class="bar"><span style="width:${(100 * v / top).toFixed(1)}%"></span></span>` +
    `<span class="val">${esc(s.shown)}</span></div>`;
  return `<div class="g g-compare ${side}"><div class="head"><b>${esc(c.label)}</b><span>${esc(c.period)}</span></div>` +
    row(c.home, vals[0], 'home') + row(c.away, vals[1], 'away') + `<p class="line">${esc(c.text)}</p></div>`;
}

function playerHtml(c, side) {
  return `<div class="g g-player ${side}"><div class="stripe"></div><div class="body">` +
    `<p class="team">${esc(c.team_name)}</p><p class="name">${esc(c.name)}</p>` +
    `<p class="figure">${esc(c.figure)}</p><p class="label">${esc(c.label)}${c.period ? ' · ' + esc(c.period) : ''}</p>` +
    `<p class="line">${esc(c.text)}</p></div></div>`;
}

/* One cue's card as HTML. `side` is "home" or "away" (the team colour) or "" for neither. */
export function cardHtml(card, side = '') {
  if (!card) return '';
  if (card.layout === 'stat_comparison') return compareHtml(card, side);
  if (card.layout === 'player_card') return playerHtml(card, side);
  if (card.layout === 'ticker') return `<div class="g g-ticker"><span>${esc(card.text)}</span></div>`;
  return `<div class="g g-lower ${side}"><span class="label">${esc(card.label)}</span><span class="text">${esc(card.text)}</span></div>`;
}

/* Which team's colour a card takes. */
export function sideOf(card, meta) {
  const team = card.layout === 'stat_comparison' ? card.subject : card.team;
  if (team === meta.home) return 'home';
  if (team === meta.away) return 'away';
  return '';
}
