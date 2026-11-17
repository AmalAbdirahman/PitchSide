/* KPI row: five cards with sparklines over match time, up to the replay clock (never ahead of it).
   npxG, field tilt and win probability come from hero.json series; the verified count from the
   bundle's facts; aired from this session's decisions. The page computes no statistic itself. */

import { D, S, $, COL, idxAt } from './data.js';
import { actionable } from './state.js';

const W = 100, H = 28;
let passedAt = null;  // cumulative drafts that passed the verifier, per series sample

/* One sparkline path from kick-off to the replay clock, stretched across the card. */
function path(vals, upto, lo, hi) {
  const n = upto || 1, span = hi - lo || 1;
  let d = '', pen = 'M';
  for (let k = 0; k <= upto; k++) {
    const v = vals[k];
    if (v === null || v === undefined) { pen = 'M'; continue; }
    const x = (k / n) * W, y = H - ((Math.min(hi, Math.max(lo, v)) - lo) / span) * H;
    d += `${pen}${x.toFixed(1)} ${y.toFixed(1)}`;
    pen = 'L';
  }
  return d;
}

function spark(id, lines, upto, lo, hi, base) {
  let g = base === undefined ? '' : `<line x1="0" x2="${W}" y1="${H - ((base - lo) / (hi - lo)) * H}" y2="${H - ((base - lo) / (hi - lo)) * H}" stroke="${COL.line2}" stroke-dasharray="2 2" vector-effect="non-scaling-stroke"/>`;
  for (const [vals, col] of lines) {
    g += `<path d="${path(vals, upto, lo, hi)}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
  }
  $(id).innerHTML = g;
}

/* Cumulative count per series sample of the times in ts (sorted or not). */
function cumulative(ts) {
  const sorted = ts.slice().sort((a, b) => a - b);
  let k = 0;
  return D.series.t.map(t => { while (k < sorted.length && sorted[k] <= t) k++; return k; });
}

const pct = v => (v === null || v === undefined ? '–' : Math.round(v) + '%');
const upTo = (vals, i) => Math.max(...vals.slice(0, i + 1).map(v => v || 0));

function renderNpxg(i, home, away) {
  const h = D.series.teams[home].npxg_sum, a = D.series.teams[away].npxg_sum;
  $('#k-npxg-h').textContent = (h[i] || 0).toFixed(2);
  $('#k-npxg-a').textContent = '– ' + (a[i] || 0).toFixed(2);
  spark('#sp-npxg', [[h, COL.home], [a, COL.away]], i, 0, Math.max(0.5, upTo(h, i), upTo(a, i)));
}

function renderTilt(i, home) {
  const v = D.series.teams[home].field_tilt;
  $('#k-tilt').textContent = pct(v[i]);
  $('#k-tilt-s').textContent = `${home}, last ${D.series.tilt_window_s / 60} min`;
  spark('#sp-tilt', [[v, COL.home]], i, 0, 100, 50);
}

function renderWinProb(i, home) {
  const wp = D.series.win_prob.map(r => r[0] * 100);
  $('#k-wp').textContent = pct(wp[i]);
  $('#k-wp-s').textContent = `${home} · model estimate`;
  spark('#sp-wp', [[wp, COL.text]], i, 0, 100);
}

function renderVerified(i) {
  passedAt = passedAt || cumulative(D.items.filter(it => !it.rejected).map(it => it.t));
  $('#k-ver').textContent = S.c.verified + S.c.corrected;
  $('#k-ver-n').textContent = '/ ' + S.c.drafts;
  $('#k-ver-s').textContent = `${S.c.rejected} rejected by verifier`;
  spark('#sp-ver', [[passedAt, COL.verified]], i, 0, Math.max(1, passedAt[passedAt.length - 1]));
}

function renderAired(i) {
  const waiting = D.items.filter(actionable).length, aired = cumulative(S.airedAt);
  $('#k-air').textContent = S.c.aired;
  $('#k-air-s').textContent = `${waiting} waiting · ${S.c.vetoed + S.c.declined} vetoed`;
  spark('#sp-air', [[aired, COL.accent]], i, 0, Math.max(1, passedAt[passedAt.length - 1]));
}

export function renderKpis() {
  const i = idxAt(S.t), { home, away } = D.meta;
  renderNpxg(i, home, away);
  renderTilt(i, home);
  renderWinProb(i, home);
  renderVerified(i);
  renderAired(i);
}

/* Static parts: team codes and the bundle date. */
export function setupKpis() {
  const { home, away, match_id: id, built } = D.meta;
  $('#k-npxg-s').textContent = `${home} – ${away}`;
  $('#match-id').textContent = id;
  $('#built').textContent = built || '–';
  if (built) $('#built').setAttribute('datetime', built);
  passedAt = null;
}
